"""
qa_worker.py
============
Runs RAG (Retrieval-Augmented Generation) Q&A in a QThread so the GUI
never freezes during LLM calls.

Signals emitted on the main / GUI thread:
    token_ready(str)              — each streaming token from the LLM
    answer_done(str, str)         — (source_type, full_answer_text)
    error(str)                    — human-readable error message
    index_progress(int, int)      — (chunks_indexed, total_chunks)
    index_done(int)               — total chunks after indexing
    service_ready(str)            — LLM mode label after service init

Dependencies:
    pip install langchain langchain-openai langchain-community faiss-cpu openai python-dotenv
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


# ── Internal task types ────────────────────────────────────────────────────────
class _AskTask:
    def __init__(self, question: str):
        self.question = question

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

Document Context:
{context}

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

    def ask(self, question: str):
        """Queue a Q&A request."""
        self._task_queue.put(_AskTask(question))

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
        if self._thread._service and self._thread._service.vectorstore:
            try:
                return self._thread._service.vectorstore.index.ntotal
            except Exception:
                return 0
        return 0


# ── Internal worker thread ─────────────────────────────────────────────────────

class _WorkerThread(QThread):
    def __init__(self, task_queue: queue.Queue, worker: QAWorker):
        super().__init__()
        self._queue       = task_queue
        self._worker      = worker
        self._service     = None
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
                self._handle_ask(task.question)
            elif isinstance(task, _IndexTask):
                self._handle_index(task.file_path)
            elif isinstance(task, _ClearTask):
                self._handle_clear()

    # ── Service initialisation ─────────────────────────────────────────────────

    def _init_service(self):
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
                self._service = _MinimalQAService(self.api_key)
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
                    if self._service and self._service.document_count == 0:
                        self._handle_index(str(p))
                        logger.info(f"Auto-indexed: {p}")
                except Exception as ex:
                    logger.warning(f"Auto-index failed: {ex}")
                break

    # ── Task handlers ──────────────────────────────────────────────────────────

    def _handle_ask(self, question: str):
        if not self._service:
            self._worker.error.emit("QA service not ready yet.")
            return

        try:
            full_text = ""
            source_type = "docs"

            # Try streaming first
            if hasattr(self._service, "stream_query"):
                for token in self._service.stream_query(question):
                    self._worker.token_ready.emit(token)
                    full_text += token

                # Detect "not found" → web fallback
                if _is_not_found(full_text):
                    full_text = ""
                    oracle_q = f"Oracle OIC Oracle Integration Cloud VBCS: {question}"
                    for token in self._service.stream_query(oracle_q, force_web=True):
                        self._worker.token_ready.emit(token)
                        full_text += token
                    source_type = "web"

            else:
                # Blocking fallback
                result = self._service.query(question)
                full_text = result.answer
                source_type = result.source_type
                # Emit all at once as a single "token" for simplicity
                self._worker.token_ready.emit(full_text)

            self._worker.answer_done.emit(source_type, full_text)

        except Exception as ex:
            self._worker.error.emit(f"Q&A error: {ex}")

    def _handle_index(self, file_path: str):
        if not self._service:
            self._worker.error.emit("QA service not ready. Cannot index file.")
            return

        try:
            p = Path(file_path)
            if not p.exists():
                self._worker.error.emit(f"File not found: {file_path}")
                return

            # Process file → chunks
            chunks = _load_and_chunk_file(p)
            total = len(chunks)
            if total == 0:
                self._worker.error.emit(f"No text extracted from: {p.name}")
                return

            # Convert dicts → LangChain Documents (works with both QAService versions)
            try:
                from langchain_core.documents import Document as LCDoc
                lc_chunks = [
                    LCDoc(
                        page_content=c.get("content", c.get("page_content", str(c))),
                        metadata=c.get("metadata", {})
                    ) if isinstance(c, dict) else c
                    for c in chunks
                ]
            except ImportError:
                lc_chunks = chunks   # fallback: pass as-is

            # Index in batches of 20
            batch_size = 20
            done = 0
            for i in range(0, total, batch_size):
                batch = lc_chunks[i: i + batch_size]
                self._service.add_documents(batch)
                done += len(batch)
                self._worker.index_progress.emit(done, total)

            self._worker.index_done.emit(self._service.document_count)

        except Exception as ex:
            self._worker.error.emit(f"Indexing error: {ex}")

    def _handle_clear(self):
        if self._service:
            try:
                self._service.clear_documents()
            except AttributeError:
                pass


# ── Minimal inline QA service (if web project not on path) ────────────────────

class _MinimalQAService:
    """
    Minimal in-process RAG service using LangChain directly.
    Mirrors the logic in speech_qa_project/services/qa_service.py.
    """

    def __init__(self, api_key: str = ""):
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        api_key_valid = bool(api_key and not api_key.startswith("sk-your"))

        if api_key_valid:
            from langchain_openai import ChatOpenAI, OpenAIEmbeddings
            self._llm = ChatOpenAI(
                model="gpt-4o", temperature=0.1, openai_api_key=api_key,
                streaming=True
            )
            self._embeddings = OpenAIEmbeddings(
                model="text-embedding-3-small", openai_api_key=api_key
            )
            self._mode = "OpenAI (gpt-4o)"
            self._openai_key = api_key
        else:
            from langchain_ollama import ChatOllama, OllamaEmbeddings
            self._llm = ChatOllama(
                model="llama3", base_url="http://localhost:11434", temperature=0.1
            )
            self._embeddings = OllamaEmbeddings(
                model="nomic-embed-text", base_url="http://localhost:11434"
            )
            self._mode = "Local Ollama (llama3)"
            self._openai_key = ""

        self._vectorstore = None
        self._prompt = ChatPromptTemplate.from_template(_RAG_PROMPT_TEMPLATE)
        self._parser = StrOutputParser()

        # Try loading existing vectorstore
        _vs_dir = Path(__file__).parent / "vectorstore"
        if _vs_dir.exists():
            try:
                from langchain_community.vectorstores import FAISS
                self._vectorstore = FAISS.load_local(
                    str(_vs_dir), self._embeddings,
                    allow_dangerous_deserialization=True
                )
            except Exception:
                pass

    @property
    def mode_label(self) -> str:
        return self._mode

    @property
    def document_count(self) -> int:
        if self._vectorstore:
            try:
                return self._vectorstore.index.ntotal
            except Exception:
                return 1
        return 0

    def add_documents(self, docs: list):
        from langchain_community.vectorstores import FAISS
        from langchain_core.documents import Document as LCDoc

        lc_docs = []
        for d in docs:
            if isinstance(d, dict):
                lc_docs.append(LCDoc(
                    page_content=d.get("content", d.get("page_content", "")),
                    metadata=d.get("metadata", {})
                ))
            elif hasattr(d, "page_content"):
                lc_docs.append(d)
            else:
                lc_docs.append(LCDoc(page_content=str(d)))

        if self._vectorstore is None:
            self._vectorstore = FAISS.from_documents(lc_docs, self._embeddings)
        else:
            self._vectorstore.add_documents(lc_docs)

        # Persist
        _vs_dir = Path(__file__).parent / "vectorstore"
        _vs_dir.mkdir(parents=True, exist_ok=True)
        self._vectorstore.save_local(str(_vs_dir))

    def clear_documents(self):
        self._vectorstore = None

    def stream_query(self, question: str, force_web: bool = False):
        """Yield streaming tokens from LLM."""
        context = ""
        if self._vectorstore and not force_web:
            try:
                docs = self._vectorstore.similarity_search(question, k=_TOP_K)
                context = "\n\n".join(d.page_content for d in docs)
            except Exception:
                pass

        if force_web:
            context = self._web_search(question)

        chain = self._prompt | self._llm | self._parser
        for chunk in chain.stream({"context": context, "question": question}):
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


# ── File loading helpers ───────────────────────────────────────────────────────

def _load_and_chunk_file(path: Path, chunk_size: int = 1000,
                          chunk_overlap: int = 200) -> list:
    """
    Load a file and split into overlapping text chunks.
    Returns a list of dicts: {content: str, metadata: dict}
    """
    text = _extract_text(path)
    if not text:
        return []

    # Split on double-newlines (paragraphs), then merge to chunk_size
    paragraphs = re.split(r"\n{2,}", text)
    chunks = []
    current = ""
    overlap_buf = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current) + len(para) + 2 <= chunk_size:
            current += ("\n\n" if current else "") + para
        else:
            if current:
                chunks.append({
                    "content": current,
                    "metadata": {"source": path.name, "chunk": len(chunks)},
                })
                # Keep last overlap_buf chars as prefix for next chunk
                overlap_buf = current[-chunk_overlap:]
            current = overlap_buf + ("\n\n" if overlap_buf else "") + para
            overlap_buf = ""

    if current:
        chunks.append({
            "content": current,
            "metadata": {"source": path.name, "chunk": len(chunks)},
        })

    return chunks


def _extract_text(path: Path) -> str:
    """Extract plain text from a file (txt, md, pdf, docx)."""
    suffix = path.suffix.lower()

    if suffix in (".txt", ".md", ".csv"):
        return path.read_text(encoding="utf-8", errors="ignore")

    if suffix == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            return "\n\n".join(
                page.extract_text() or "" for page in reader.pages
            )
        except ImportError:
            try:
                import pdfminer.high_level as pdfm
                return pdfm.extract_text(str(path))
            except ImportError:
                return ""

    if suffix in (".docx", ".doc"):
        try:
            import docx
            doc = docx.Document(str(path))
            return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except ImportError:
            return ""

    # Fallback: try raw text
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
