"""
Mic Assistant — Speak & Get AI Answers
=======================================
A compact desktop app that captures your microphone, transcribes your speech
with Whisper, searches uploaded .md documents first, falls back to web search
if not found, and shows both input transcript and AI response in a small window.

Run:  python mic_assistant.py
"""

# Fix Windows COM threading conflict (must be before ANY other import)
import sys
sys.coinit_flags = 2  # COINIT_APARTMENTTHREADED — matches what Qt expects

import os
import threading
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QTextEdit, QFrame,
    QScrollArea, QMessageBox, QSplitter, QFileDialog, QListWidget,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QObject
from PyQt6.QtGui import QFont, QTextCursor, QColor

from audio_capture import AudioCapture
from transcription_worker import TranscriptionWorker
from doc_store import DocStore

# ── Dark theme colours ─────────────────────────────────────────────────────────
DARK_BG    = "#0f172a"
SURFACE    = "#1e293b"
SURFACE2   = "#273348"
BORDER     = "#334155"
PRIMARY    = "#6366f1"
SUCCESS    = "#22c55e"
DANGER     = "#ef4444"
WARNING    = "#f59e0b"
TEXT       = "#f1f5f9"
TEXT_MUTED = "#94a3b8"
WEB_BLUE   = "#63b3ed"

STYLE = f"""
QMainWindow, QWidget {{
    background-color: {DARK_BG};
    color: {TEXT};
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}}
QFrame#card {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 6px;
}}
QPushButton#btnMic {{
    background-color: {PRIMARY};
    color: white;
    border-radius: 7px;
    padding: 10px 22px;
    font-weight: 700;
    font-size: 14px;
    border: none;
}}
QPushButton#btnMic:hover {{ background-color: #4f46e5; }}
QPushButton#btnStop {{
    background-color: rgba(239,68,68,0.15);
    border: 1px solid rgba(239,68,68,0.4);
    color: {DANGER};
    border-radius: 7px;
    padding: 10px 22px;
    font-weight: 700;
    font-size: 14px;
}}
QPushButton#btnStop:hover {{ background-color: rgba(239,68,68,0.28); }}
QPushButton#btnClear {{
    background-color: rgba(239,68,68,0.1);
    border: 1px solid rgba(239,68,68,0.3);
    color: {DANGER};
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 12px;
}}
QPushButton#btnUpload {{
    background-color: rgba(99,102,241,0.15);
    border: 1px solid rgba(99,102,241,0.4);
    color: {PRIMARY};
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#btnUpload:hover {{ background-color: rgba(99,102,241,0.28); }}
QComboBox {{
    background-color: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 6px 12px;
    color: {TEXT};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    color: {TEXT};
    selection-background-color: {PRIMARY};
}}
QTextEdit, QListWidget {{
    background-color: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    color: {TEXT};
    font-size: 13px;
    padding: 8px;
    selection-background-color: {PRIMARY};
}}
QScrollBar:vertical {{
    background: {SURFACE};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QLabel#heading {{
    font-size: 18px;
    font-weight: 800;
    color: {PRIMARY};
}}
QLabel#sectionTitle {{
    font-size: 11px;
    font-weight: 700;
    color: {TEXT_MUTED};
    letter-spacing: 1px;
}}
QLabel#status {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}
QLabel#sourceDocs {{
    color: {SUCCESS};
    font-size: 11px;
    font-weight: 700;
}}
QLabel#sourceWeb {{
    color: {WEB_BLUE};
    font-size: 11px;
    font-weight: 700;
}}
QSplitter::handle {{
    background-color: {BORDER};
    height: 1px;
}}
"""

# ── Phrases that indicate docs had no answer ───────────────────────────────────
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
    "i don't have access",
    "no uploaded documents",
    "no context provided",
    "not in the provided",
]

def _is_not_found(answer: str) -> bool:
    lower = answer.lower()
    return any(p in lower for p in _NOT_FOUND_PHRASES)


# ── LLM Worker — docs first, web fallback ─────────────────────────────────────

class _LLMRequest:
    def __init__(self, text: str, doc_context: str = ""):
        self.text = text
        self.doc_context = doc_context

class _LLMStop:
    pass


class LLMWorker(QObject):
    """Sends transcript to LLM: uses doc context first, falls back to web."""

    token_ready  = pyqtSignal(str)              # each streamed token
    answer_done  = pyqtSignal(str, str)         # (source_type, full_answer)
    error        = pyqtSignal(str)
    ready        = pyqtSignal(str)              # model label after init

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = __import__("queue").Queue()
        self._thread = _LLMThread(self._queue, self)

    def start_service(self):
        # Load .env for OPENAI_API_KEY
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            env_path = os.path.join(os.path.dirname(__file__), ".env")
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            os.environ.setdefault(k.strip(), v.strip())
        if not self._thread.isRunning():
            self._thread.start()

    def ask(self, text: str, doc_context: str = ""):
        self._queue.put(_LLMRequest(text, doc_context))

    def stop(self):
        self._queue.put(_LLMStop())


class _LLMThread(QThread):
    def __init__(self, task_queue, worker: LLMWorker):
        super().__init__()
        self._queue = task_queue
        self._worker = worker
        self._llm = None
        self._openai_key = ""
        self._mode = ""

    def run(self):
        self._init_llm()
        while True:
            task = self._queue.get()
            if isinstance(task, _LLMStop):
                break
            if isinstance(task, _LLMRequest):
                self._handle(task.text, task.doc_context)

    def _init_llm(self):
        api_key = os.environ.get("OPENAI_API_KEY", "")
        valid = bool(api_key and not api_key.startswith("sk-your"))

        if valid:
            try:
                from langchain_openai import ChatOpenAI
                self._llm = ChatOpenAI(
                    model="gpt-4o", temperature=0.2,
                    openai_api_key=api_key, streaming=True,
                )
                self._openai_key = api_key
                self._mode = "OpenAI (gpt-4o)"
                self._worker.ready.emit(self._mode)
                return
            except Exception as ex:
                self._worker.error.emit(f"OpenAI init failed: {ex}")

        # Fallback: try Ollama
        try:
            from langchain_ollama import ChatOllama
            self._llm = ChatOllama(
                model="llama3", base_url="http://localhost:11434",
                temperature=0.2,
            )
            self._mode = "Local Ollama (llama3)"
            self._worker.ready.emit(self._mode)
        except Exception as ex:
            self._worker.error.emit(
                f"No LLM available: {ex}\n"
                "Set OPENAI_API_KEY in .env or run Ollama locally."
            )

    def _handle(self, text: str, doc_context: str):
        if not self._llm:
            self._worker.error.emit("LLM not initialised.")
            return

        try:
            from langchain_core.prompts import ChatPromptTemplate
            from langchain_core.output_parsers import StrOutputParser

            source_type = "docs"
            full = ""

            # ── Step 1: Try with document context ──────────────────────────
            if doc_context:
                prompt = ChatPromptTemplate.from_template(
                    "You are a helpful AI assistant. The user spoke the following "
                    "via their microphone. Use the Document Context below as your "
                    "PRIMARY source of information to answer.\n\n"
                    "Document Context:\n{context}\n\n"
                    "User said:\n{input}\n\n"
                    "If the document context does not contain relevant information "
                    "to answer, say exactly: 'Not found in the documents.'\n\n"
                    "Assistant:"
                )
                chain = prompt | self._llm | StrOutputParser()
                for chunk in chain.stream({"input": text, "context": doc_context}):
                    self._worker.token_ready.emit(chunk)
                    full += chunk

                # Check if docs had the answer
                if not _is_not_found(full):
                    self._worker.answer_done.emit("docs", full)
                    return

                # Docs didn't have the answer — fall through to web
                self._worker.token_ready.emit("\n\n--- Searching the web... ---\n\n")
                full = ""
                source_type = "web"

            else:
                # No docs uploaded — go straight to web
                source_type = "web"

            # ── Step 2: Web search fallback ────────────────────────────────
            web_context = self._web_search(text)

            if web_context:
                prompt = ChatPromptTemplate.from_template(
                    "You are a helpful AI assistant. The user spoke the following "
                    "via their microphone. Use the Web Search Results below to "
                    "provide an accurate, up-to-date answer.\n\n"
                    "Web Search Results:\n{context}\n\n"
                    "User said:\n{input}\n\n"
                    "Assistant:"
                )
            else:
                # No web results either — answer from general knowledge
                prompt = ChatPromptTemplate.from_template(
                    "You are a helpful AI assistant. The user just said the "
                    "following via their microphone. Understand what they said "
                    "and provide a helpful, concise response.\n\n"
                    "User said:\n{input}\n\n"
                    "Assistant:"
                )
                web_context = ""

            chain = prompt | self._llm | StrOutputParser()
            for chunk in chain.stream({"input": text, "context": web_context}):
                self._worker.token_ready.emit(chunk)
                full += chunk

            self._worker.answer_done.emit(source_type, full)

        except Exception as ex:
            self._worker.error.emit(f"LLM error: {ex}")

    def _web_search(self, query: str) -> str:
        """Use OpenAI gpt-4o-search-preview as web search fallback."""
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


# ── Main Window ────────────────────────────────────────────────────────────────

class MicAssistant(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Mic Assistant — Speak & Get AI Answers")
        self.setMinimumSize(750, 580)
        self.resize(820, 620)

        # State
        self._recording = False
        self._transcript_parts: list[str] = []
        self._llm_busy = False
        self._accepting_audio = False     # True while we accept audio chunks

        # DocStore for uploaded .md files
        kb_dir = str(Path(__file__).parent / "knowledge_base")
        self._doc_store = DocStore(kb_dir=kb_dir)

        # Workers
        self._audio = AudioCapture()
        self._trans = TranscriptionWorker()
        self._llm   = LLMWorker()

        # Audio signals
        self._audio.chunk_ready.connect(self._on_audio_chunk)
        self._audio.device_list_ready.connect(self._on_devices)
        self._audio.error.connect(lambda m: self._set_status(f"Audio: {m}"))

        # Transcription signals
        self._trans.transcript_ready.connect(self._on_transcript)
        self._trans.model_loaded.connect(self._on_model_loaded)
        self._trans.error.connect(lambda m: self._set_status(f"Whisper: {m}"))

        # LLM signals
        self._llm.token_ready.connect(self._on_llm_token)
        self._llm.answer_done.connect(self._on_llm_done)
        self._llm.error.connect(lambda m: self._on_llm_error(m))
        self._llm.ready.connect(lambda l: self._set_status(f"LLM ready: {l}"))

        self._build_ui()
        self._refresh_devices()
        self._refresh_doc_count()
        self._llm.start_service()

        # Post-stop delay timer — waits for flushed audio to be transcribed
        self._post_stop_timer = QTimer(self)
        self._post_stop_timer.setSingleShot(True)
        self._post_stop_timer.timeout.connect(self._after_stop_send)

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(STYLE)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("Mic Assistant")
        title.setObjectName("heading")
        hdr.addWidget(title)
        hdr.addStretch()
        self._status_lbl = QLabel("Initialising...")
        self._status_lbl.setObjectName("status")
        hdr.addWidget(self._status_lbl)
        root.addLayout(hdr)

        # Controls card — row 1: device + model + start/clear
        ctrl = self._card()
        ctrl_lay = QVBoxLayout(ctrl)
        ctrl_lay.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(10)
        row1.addWidget(QLabel("Device:"))
        self._dev_combo = QComboBox()
        row1.addWidget(self._dev_combo, 1)
        row1.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.addItems(["base", "small", "medium", "large-v3"])
        self._model_combo.setCurrentIndex(1)
        row1.addWidget(self._model_combo)
        self._mic_btn = QPushButton("Start")
        self._mic_btn.setObjectName("btnMic")
        self._mic_btn.clicked.connect(self._toggle_recording)
        row1.addWidget(self._mic_btn)
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setObjectName("btnClear")
        self._clear_btn.clicked.connect(self._clear_all)
        row1.addWidget(self._clear_btn)
        ctrl_lay.addLayout(row1)

        # Controls card — row 2: upload docs
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self._upload_btn = QPushButton("Upload Docs")
        self._upload_btn.setObjectName("btnUpload")
        self._upload_btn.clicked.connect(self._upload_docs)
        row2.addWidget(self._upload_btn)
        self._doc_count_lbl = QLabel("0 docs")
        self._doc_count_lbl.setStyleSheet(f"color:{TEXT_MUTED}; font-size:12px;")
        row2.addWidget(self._doc_count_lbl)
        row2.addStretch()
        self._source_lbl = QLabel("")
        self._source_lbl.setObjectName("sourceDocs")
        row2.addWidget(self._source_lbl)
        ctrl_lay.addLayout(row2)

        root.addWidget(ctrl)

        # Splitter: Input (top) / Output (bottom)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(2)

        # INPUT panel
        input_w = QWidget()
        input_lay = QVBoxLayout(input_w)
        input_lay.setContentsMargins(0, 0, 0, 0)
        input_lay.setSpacing(4)

        inp_title = QLabel("INPUT — What you said")
        inp_title.setObjectName("sectionTitle")
        input_lay.addWidget(inp_title)

        self._input_box = QTextEdit()
        self._input_box.setPlaceholderText(
            "Press Start and speak into your microphone...\n"
            "Your speech will appear here as live transcript."
        )
        self._input_box.setReadOnly(True)
        input_lay.addWidget(self._input_box, 1)

        # OUTPUT panel
        output_w = QWidget()
        output_lay = QVBoxLayout(output_w)
        output_lay.setContentsMargins(0, 0, 0, 0)
        output_lay.setSpacing(4)

        out_title = QLabel("OUTPUT — AI Response")
        out_title.setObjectName("sectionTitle")
        output_lay.addWidget(out_title)

        self._output_box = QTextEdit()
        self._output_box.setPlaceholderText(
            "AI response will appear here automatically when you stop recording..."
        )
        self._output_box.setReadOnly(True)
        output_lay.addWidget(self._output_box, 1)

        splitter.addWidget(input_w)
        splitter.addWidget(output_w)
        splitter.setSizes([260, 260])
        root.addWidget(splitter, 1)

        # Timer display
        self._elapsed = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer_lbl = QLabel("")
        self._timer_lbl.setStyleSheet(f"color:{TEXT_MUTED}; font-size:11px;")

        bottom = QHBoxLayout()
        self._rec_dot = QLabel("")
        self._rec_dot.setStyleSheet(f"color:{TEXT_MUTED}; font-size:13px;")
        bottom.addWidget(self._rec_dot)
        bottom.addWidget(self._timer_lbl)
        bottom.addStretch()
        self._bottom_lbl = QLabel("Ready")
        self._bottom_lbl.setStyleSheet(f"color:{TEXT_MUTED}; font-size:11px;")
        bottom.addWidget(self._bottom_lbl)
        root.addLayout(bottom)

    def _card(self) -> QFrame:
        f = QFrame()
        f.setObjectName("card")
        return f

    def _set_status(self, text: str):
        self._status_lbl.setText(text)

    # ── Document upload ────────────────────────────────────────────────────────

    def _upload_docs(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Upload Documents",
            str(Path.home()),
            "Documents (*.pdf *.docx *.txt *.md *.csv);;All Files (*)"
        )
        if not files:
            return
        added = 0
        for fp in files:
            try:
                self._doc_store.add_file(fp)
                added += 1
            except Exception as ex:
                self._set_status(f"Upload error: {ex}")
        self._refresh_doc_count()
        self._set_status(f"Uploaded {added} file(s) to knowledge base")

    def _refresh_doc_count(self):
        count = self._doc_store.document_count
        self._doc_count_lbl.setText(
            f"{count} doc{'s' if count != 1 else ''} in knowledge base"
        )

    # ── Device list ────────────────────────────────────────────────────────────

    def _refresh_devices(self):
        self._dev_combo.clear()
        try:
            devices = self._audio.enumerate_devices()
        except Exception:
            devices = [("default_mic", "Default Microphone", False)]
        self._on_devices(devices)

    def _on_devices(self, devices: list):
        self._dev_combo.clear()
        self._devices = devices
        for dev_id, name, is_loopback in devices:
            self._dev_combo.addItem(name, userData=dev_id)

    # ── Recording toggle ───────────────────────────────────────────────────────

    def _toggle_recording(self):
        if not self._recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except ImportError:
            QMessageBox.critical(self, "Missing Package",
                "faster-whisper is not installed.\n\n"
                "Run:  pip install faster-whisper\n\nThen restart.")
            return

        # Cancel any pending post-stop timer from previous session
        if self._post_stop_timer.isActive():
            self._post_stop_timer.stop()

        # Clear previous session data before starting fresh
        self._transcript_parts.clear()
        self._input_box.clear()
        self._output_box.clear()
        self._source_lbl.setText("")
        self._output_box.setPlaceholderText(
            "AI response will appear here automatically when you stop recording..."
        )

        # Force microphone source
        self._audio.set_source(AudioCapture.SOURCE_MIC)
        dev_id = self._dev_combo.currentData()
        self._audio.set_device(dev_id)

        model_name = self._model_combo.currentText()
        self._trans.load_model(model_name)
        if not self._trans.isRunning():
            self._trans.start()

        try:
            self._audio.stop()
        except Exception:
            pass
        self._audio.start()

        self._recording = True
        self._accepting_audio = True
        self._mic_btn.setText("Stop")
        self._mic_btn.setObjectName("btnStop")
        self._mic_btn.setStyle(self._mic_btn.style())  # force re-apply style
        self._rec_dot.setText("●")
        self._rec_dot.setStyleSheet(f"color:{DANGER}; font-size:13px;")
        self._elapsed = 0
        self._timer.start(1000)
        self._set_status("Recording...")
        self._bottom_lbl.setText(f"Whisper model: {model_name} — speak clearly")

    def _stop_recording(self):
        self._recording = False
        # Keep _accepting_audio = True so flushed chunks are still received
        try:
            self._audio.stop()          # flushes remaining audio buffer
        except Exception:
            pass
        self._trans.resume()  # unblock if paused
        self._timer.stop()

        self._mic_btn.setText("Start")
        self._mic_btn.setObjectName("btnMic")
        self._mic_btn.setStyle(self._mic_btn.style())
        self._rec_dot.setText("")
        self._rec_dot.setStyleSheet(f"color:{TEXT_MUTED}; font-size:13px;")

        # Wait 2 seconds for flushed audio to be transcribed, then send
        self._set_status("Stopped — processing audio...")
        self._bottom_lbl.setText("Waiting for transcription to finish...")
        self._post_stop_timer.start(2000)

    def _after_stop_send(self):
        """Called by post-stop timer — transcription should be done by now."""
        self._accepting_audio = False    # stop accepting any stale chunks

        if self._transcript_parts:
            self._set_status("Searching docs then AI...")
            self._bottom_lbl.setText(
                f"Captured {len(self._transcript_parts)} segments — querying..."
            )
            self._send_to_llm()
        else:
            self._set_status("Stopped — nothing captured")
            self._bottom_lbl.setText("No speech detected. Press Space to try again.")

    def _tick(self):
        self._elapsed += 1
        m, s = divmod(self._elapsed, 60)
        self._timer_lbl.setText(f"{m:02d}:{s:02d}")

    # ── Audio -> Transcription ─────────────────────────────────────────────────

    def _on_audio_chunk(self, pcm_bytes: bytes, sample_rate: int):
        if self._accepting_audio:
            self._trans.enqueue_chunk(pcm_bytes, sample_rate)

    def _on_transcript(self, text: str, timestamp: str):
        if not text.strip():
            return
        self._transcript_parts.append(text.strip())
        # Append to input box with timestamp
        self._input_box.moveCursor(QTextCursor.MoveOperation.End)
        self._input_box.insertPlainText(f"[{timestamp}]  {text.strip()}\n")
        self._input_box.moveCursor(QTextCursor.MoveOperation.End)
        self._bottom_lbl.setText(f"{len(self._transcript_parts)} segments captured")

    def _on_model_loaded(self):
        self._set_status("Recording — Whisper ready")

    # ── Send to LLM (docs first, web fallback) ────────────────────────────────

    def _send_to_llm(self):
        full_text = " ".join(self._transcript_parts).strip()
        if not full_text:
            full_text = self._input_box.toPlainText().strip()
        if not full_text or self._llm_busy:
            return

        self._llm_busy = True
        self._mic_btn.setEnabled(False)
        self._output_box.clear()
        self._output_box.setPlaceholderText("")
        self._source_lbl.setText("")

        # Search DocStore for relevant context
        doc_context = ""
        if self._doc_store.document_count > 0:
            doc_context = self._doc_store.get_context(full_text, max_chars=4000)
            if doc_context:
                self._set_status("Searching documents first...")
            else:
                self._set_status("No doc matches — will search web...")
        else:
            self._set_status("No docs uploaded — searching web...")

        self._llm.ask(full_text, doc_context=doc_context)

    def _on_llm_token(self, token: str):
        self._output_box.moveCursor(QTextCursor.MoveOperation.End)
        self._output_box.insertPlainText(token)
        self._output_box.moveCursor(QTextCursor.MoveOperation.End)

    def _on_llm_done(self, source_type: str, full_answer: str):
        self._llm_busy = False
        self._mic_btn.setEnabled(True)
        self._set_status("Response complete")

        if source_type == "docs":
            self._source_lbl.setText("Source: Documents")
            self._source_lbl.setObjectName("sourceDocs")
        else:
            self._source_lbl.setText("Source: Web")
            self._source_lbl.setObjectName("sourceWeb")
        self._source_lbl.setStyle(self._source_lbl.style())

        self._bottom_lbl.setText("Done — press Space to start a new session")

    def _on_llm_error(self, msg: str):
        self._llm_busy = False
        self._mic_btn.setEnabled(True)
        self._output_box.setPlainText(f"Error: {msg}")
        self._set_status("LLM error")

    # ── Clear ──────────────────────────────────────────────────────────────────

    def _clear_all(self):
        if self._recording:
            self._stop_recording()
        if self._post_stop_timer.isActive():
            self._post_stop_timer.stop()
        self._accepting_audio = False
        self._transcript_parts.clear()
        self._input_box.clear()
        self._output_box.clear()
        self._source_lbl.setText("")
        self._timer_lbl.setText("")
        self._set_status("Cleared")
        self._bottom_lbl.setText("Ready — press Start to begin")

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            if not self._llm_busy and not self._post_stop_timer.isActive():
                self._toggle_recording()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        try:
            self._audio.stop()
        except Exception:
            pass
        try:
            self._trans.stop_worker()
            self._trans.wait(2000)
        except Exception:
            pass
        try:
            self._llm.stop()
            if self._llm._thread.isRunning():
                self._llm._thread.wait(3000)
        except Exception:
            pass
        event.accept()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    import traceback

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Mic Assistant")

    def _excepthook(exc_type, exc_value, exc_tb):
        err = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(err)
        QMessageBox.critical(None, "Error", err[:2000])
    sys.excepthook = _excepthook

    win = MicAssistant()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
