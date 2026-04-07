"""
qa_worker.py
============
Runs RAG (Retrieval-Augmented Generation) Q&A in a QThread so the GUI
never freezes during LLM calls.

Uses DocStore (.md file-based search) instead of FAISS vector DB for
instant uploads and fast text-based retrieval.

Signals emitted on the main / GUI thread:
    token_ready(str)              — each streaming token from the LLM
    answer_done(str, str)         — (source_type, full_answer_text)
    error(str)                    — human-readable error message
    index_progress(int, int)      — (files_added, total_files)
    index_done(int)               — total document count after adding
    service_ready(str)            — LLM mode label after service init

Dependencies:
    pip install langchain langchain-openai langchain-community openai python-dotenv
    pip install langchain-ollama   # if using local Ollama mode
"""

from __future__ import annotations

import os
import re
import queue
import threading
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, QObject, pyqtSignal

logger = logging.getLogger(__name__)


from doc_store import DocStore

# ── Internal task types ────────────────────────────────────────────────────────
class _AskTask:
    def __init__(self, question: str, transcript_context: str = ""):
        self.question = question
        self.transcript_context = transcript_context  # full accumulated transcript

class _IndexTask:
    def __init__(self, file_path: str):
        self.file_path = file_path

class _ClearTask:
    pass

class _StopTask:
    pass


# ── Oracle-focused prompt & RAG constants (mirrors web app's qa_service.py) ────
_RAG_PROMPT_TEMPLATE = """You are an expert Oracle Cloud consultant specialising in OIC (Oracle Integration Cloud) and VBCS (Oracle Visual Builder Cloud Service).

STRICT DOMAIN RULES — read before answering:
1. Every question in this application is about Oracle Cloud middleware. NEVER give generic dictionary meanings.
   - "OIC"  ALWAYS means Oracle Integration Cloud — NOT "Oh I see" / "Officer in Charge".
   - "VBCS" ALWAYS means Oracle Visual Builder Cloud Service — NOT any other product.
2. Use the Document Context below as the primary source. If the context covers the topic, base your answer on it.
3. If the Document Context is empty or off-topic, draw on your own Oracle training knowledge to give a correct Oracle answer — do NOT say "not found" and do NOT suggest a generic web meaning.
4. Keep answers concise, practical, and specific to Oracle OIC / VBCS.
5. Do NOT cite external URLs (dictionary.cambridge.org, wikipedia, acronym.io, etc.).
6. If a Live Transcript is provided, treat it as the conversation happening right now.
   Identify ALL Oracle-related topics, questions, and concepts discussed in it.
   Answer comprehensively covering every relevant point from the transcript.

Document Context:
{context}

Live Transcript (ongoing conversation / meeting):
{transcript}

User Question: {question}

Answer (Oracle OIC/VBCS expert):"""

_NOT_FOUND_PHRASES = [
    "couldn't find information",
    "not found in the documents",
    "not in the uploaded documents",
    "no information about",
    "not mentioned in",
    "don't have information",
    "no relevant information",
    "cannot find",
    "no documents",
    "not available in",
]

_TOP_K = 5


def _is_not_found(answer: str) -> bool:
    lower = answer.lower()
    return any(phrase in lower for phrase in _NOT_FOUND_PHRASES)


class QAWorker(QObject):
    """
    Non-blocking RAG Q&A worker.

    Architecture
    ------------
    - Runs an internal QThread with an event loop.
    - All heavy work (LLM calls, FAISS search, file processing) runs in that
      thread so the GUI stays responsive.
    - Streaming tokens are emitted via token_ready for live display.
    - File indexing is done chunk-by-chunk with progress signals.

    Usage
    -----
        worker = QAWorker()
        worker.token_ready.connect(my_slot)
        worker.answer_done.connect(my_slot)
        worker.start_service()   # initialise LLM + vector store in background

        worker.ask("What is OIC?")
        worker.index_file("/path/to/doc.pdf")
    """

    token_ready     = pyqtSignal(str)        # streaming token
    answer_done     = pyqtSignal(str, str)   # (source_type, full_text)
    error           = pyqtSignal(str)
    index_progress  = pyqtSignal(int, int)   # (done, total)
    index_done      = pyqtSignal(int)        # total chunks
    service_ready   = pyqtSignal(str)        # mode label

    def __init__(self, parent=None):
        super().__init__(parent)
        self._task_queue: queue.Queue = queue.Queue()
        self._service = None           # QAService instance (created in worker thread)
        self._doc_count_lock = threading.Lock()
        self._doc_count = 0
        self._thread  = _WorkerThread(self._task_queue, self)

    # ── Public API (safe to call from GUI thread) ──────────────────────────────

    def start_service(self, openai_api_key: Optional[str] = None,
                      vectorstore_dir: str = "vectorstore"):
        """
        Initialise the RAG service in the background thread.
        If openai_api_key is None the worker tries OPENAI_API_KEY env var,
        then falls back to local Ollama mode.
        """
        # Load .env file if python-dotenv is installed
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            # dotenv not installed — try reading .env manually
            _env_path = os.path.join(os.path.dirname(__file__), ".env")
            if os.path.exists(_env_path):
                with open(_env_path) as _f:
                    for _line in _f:
                        _line = _line.strip()
                        if _line and not _line.startswith("#") and "=" in _line:
                            _k, _v = _line.split("=", 1)
                            os.environ.setdefault(_k.strip(), _v.strip())

        key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        self._thread.api_key = key
        self._thread.vectorstore_dir = vectorstore_dir
        if not self._thread.isRunning():
            self._thread.start()

    def ask(self, question: str, transcript_context: str = ""):
        """Queue a Q&A request with optional full transcript context."""
        self._task_queue.put(_AskTask(question, transcript_context))

    def index_file(self, file_path: str):
        """Queue a document for indexing."""
        self._task_queue.put(_IndexTask(file_path))

    def clear_docs(self):
        """Clear the vector store."""
        self._task_queue.put(_ClearTask())

    def stop(self):
        self._task_queue.put(_StopTask())

    @property
    def document_count(self) -> int:
        with self._doc_count_lock:
            return self._doc_count

    def _update_doc_count(self, count: int):
        with self._doc_count_lock:
            self._doc_count = count


# ── Internal worker thread ─────────────────────────────────────────────────────

class _WorkerThread(QThread):
    def __init__(self, task_queue: queue.Queue, worker: QAWorker):
        super().__init__()
        self._queue       = task_queue
        self._worker      = worker
        self._service     = None
        self._doc_store   = None       # .md file-based search
        self.api_key      = ""
        self.vectorstore_dir = "vectorstore"

    def run(self):
        """Worker thread event loop."""
        self._init_service()

        while True:
            task = self._queue.get()
            if isinstance(task, _StopTask):
                break
            elif isinstance(task, _AskTask):
                self._handle_ask(task.question, task.transcript_context)
            elif isinstance(task, _IndexTask):
                self._handle_index(task.file_path)
            elif isinstance(task, _ClearTask):
                self._handle_clear()

    # ── Service initialisation ─────────────────────────────────────────────────

    def _init_service(self):
        # Initialize DocStore (instant, no embedding needed)
        try:
            kb_dir = str(Path(__file__).parent / "knowledge_base")
            self._doc_store = DocStore(kb_dir=kb_dir)
            logger.info(f"DocStore ready — {self._doc_store.document_count} docs in knowledge_base/")
        except Exception as ex:
            logger.warning(f"DocStore init failed: {ex}")

        try:
            # Try to import QAService from the web project path (sibling folder)
            import sys
            _web_project = Path(__file__).parent.parent / "speech_qa_project"
            if _web_project.exists():
                sys.path.insert(0, str(_web_project))

            from services.qa_service import QAService
            self._service = QAService(
                openai_api_key=self.api_key or None,
                vectorstore_dir=str(Path(__file__).parent / self.vectorstore_dir),
            )
            self._worker.service_ready.emit(self._service.mode_label)
            self._auto_index_knowledge_base()

        except Exception:
            # Fall back to minimal inline service
            try:
                self._service = _MinimalQAService(self.api_key, self._doc_store)
                self._worker.service_ready.emit(self._service.mode_label)
                self._auto_index_knowledge_base()
            except Exception as ex2:
                self._worker.error.emit(
                    f"QA service unavailable: {ex2}\n"
                    f"Run: pip install -r requirements_desktop.txt"
                )

    def _auto_index_knowledge_base(self):
        """Auto-index OIC_VBCS_Knowledge_Base.md if it can be found."""
        candidates = [
            Path(__file__).parent / "OIC_VBCS_Knowledge_Base.md",
            Path(__file__).parent / "uploads" / "OIC_VBCS_Knowledge_Base.md",
            Path(__file__).parent.parent / "speech_qa_project" / "uploads" / "OIC_VBCS_Knowledge_Base.md",
        ]
        for p in candidates:
            if p.exists():
                try:
                    # Add to DocStore (instant copy)
                    if self._doc_store:
                        kb_path = self._doc_store.kb_dir / p.name
                        if not kb_path.exists():
                            self._doc_store.add_file(str(p))
                            logger.info(f"Auto-added to knowledge base: {p}")
                except Exception as ex:
                    logger.warning(f"Auto-index failed: {ex}")
                break

    # ── Task handlers ──────────────────────────────────────────────────────────

    def _handle_ask(self, question: str, transcript_context: str = ""):
        if not self._service:
            self._worker.error.emit("QA service not ready yet.")
            return

        try:
            full_text = ""
            source_type = "docs"

            # Build search query from BOTH the question AND the full transcript
            # This ensures we search docs using ALL topics mentioned in the meeting
            search_query = question
            if transcript_context:
                # Combine transcript + question for comprehensive doc search
                search_query = transcript_context + " " + question

            # Get context from DocStore using the full combined text
            doc_context = ""
            if self._doc_store and self._doc_store.document_count > 0:
                doc_context = self._doc_store.get_context(search_query, max_chars=4000)

            # Try streaming first
            if hasattr(self._service, "stream_query"):
                # Use try/except to handle services with different signatures
                try:
                    stream = self._service.stream_query(
                        question,
                        doc_context=doc_context,
                        transcript_context=transcript_context,
                    )
                except TypeError:
                    # External QAService may not accept our custom params
                    # Fall back to basic call
                    stream = self._service.stream_query(question)

                for token in stream:
                    self._worker.token_ready.emit(token)
                    full_text += token

                # Detect "not found" → web fallback
                if _is_not_found(full_text):
                    self._worker.token_ready.emit("\n\n🌐 Searching the web…\n\n")
                    full_text = ""
                    oracle_q = f"Oracle OIC Oracle Integration Cloud VBCS: {question}"
                    try:
                        stream = self._service.stream_query(oracle_q, force_web=True)
                    except TypeError:
                        stream = self._service.stream_query(oracle_q)
                    for token in stream:
                        self._worker.token_ready.emit(token)
                        full_text += token
                    source_type = "web"

            else:
                # Blocking fallback
                result = self._service.query(question)
                full_text = result.answer
                source_type = result.source_type
                self._worker.token_ready.emit(full_text)

            self._worker.answer_done.emit(source_type, full_text)

        except Exception as ex:
            self._worker.error.emit(f"Q&A error: {ex}")

    def _handle_index(self, file_path: str):
        """Add a file to the knowledge base (instant copy, no embedding)."""
        if not self._doc_store:
            self._worker.error.emit("DocStore not initialized.")
            return

        try:
            p = Path(file_path)
            if not p.exists():
                self._worker.error.emit(f"File not found: {file_path}")
                return

            # Instant file add — just copy/convert to .md
            saved_name = self._doc_store.add_file(file_path)
            self._worker.index_progress.emit(1, 1)

            count = self._doc_store.document_count
            self._worker._update_doc_count(count)
            self._worker.index_done.emit(count)
            logger.info(f"Added to knowledge base: {saved_name}")

        except Exception as ex:
            self._worker.error.emit(f"Upload error: {ex}")

    def _handle_clear(self):
        if self._doc_store:
            self._doc_store.clear_all()
        if self._service:
            try:
                self._service.clear_documents()
            except AttributeError:
                pass


# ── Minimal inline QA service (if web project not on path) ────────────────────

class _MinimalQAService:
    """
    Minimal in-process RAG service using LangChain directly.
    Uses DocStore (.md file search) for context instead of FAISS vector DB.
    """

    def __init__(self, api_key: str = "", doc_store: Optional[DocStore] = None):
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        self._doc_store = doc_store
        api_key_valid = bool(api_key and not api_key.startswith("sk-your"))

        if api_key_valid:
            from langchain_openai import ChatOpenAI
            self._llm = ChatOpenAI(
                model="gpt-4o", temperature=0.1, openai_api_key=api_key,
                streaming=True
            )
            self._mode = "OpenAI (gpt-4o)"
            self._openai_key = api_key
        else:
            from langchain_ollama import ChatOllama
            self._llm = ChatOllama(
                model="llama3", base_url="http://localhost:11434", temperature=0.1
            )
            self._mode = "Local Ollama (llama3)"
            self._openai_key = ""

        self._prompt = ChatPromptTemplate.from_template(_RAG_PROMPT_TEMPLATE)
        self._parser = StrOutputParser()

    @property
    def mode_label(self) -> str:
        return self._mode

    @property
    def document_count(self) -> int:
        if self._doc_store:
            return self._doc_store.document_count
        return 0

    def add_documents(self, docs: list):
        """No-op — DocStore handles file storage directly."""
        pass

    def clear_documents(self):
        if self._doc_store:
            self._doc_store.clear_all()

    def stream_query(self, question: str, force_web: bool = False,
                     doc_context: str = "", transcript_context: str = ""):
        """Yield streaming tokens from LLM."""
        context = doc_context

        if force_web:
            context = self._web_search(question)

        # If no context provided and we have DocStore, search it
        if not context and self._doc_store and not force_web:
            # Use transcript + question for broader search
            search_text = (transcript_context + " " + question).strip() if transcript_context else question
            context = self._doc_store.get_context(search_text, max_chars=4000)

        # Truncate transcript to keep within token limits
        transcript = transcript_context[:3000] if transcript_context else ""

        chain = self._prompt | self._llm | self._parser
        for chunk in chain.stream({
            "context": context,
            "transcript": transcript,
            "question": question,
        }):
            yield chunk

    def _web_search(self, query: str) -> str:
        """Use OpenAI search-preview model as web fallback."""
        if not self._openai_key:
            return ""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self._openai_key)
            resp = client.chat.completions.create(
                model="gpt-4o-search-preview",
                messages=[{"role": "user", "content": query}],
                max_tokens=800,
            )
            return resp.choices[0].message.content or ""
        except Exception:
            return ""


# ── File loading helpers (kept for backward compatibility) ─────────────────────
# DocStore now handles all file loading and conversion.
# These are retained only if external code references them.
