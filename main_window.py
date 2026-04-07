"""
OIC / VBCS Live Q&A — Windows Desktop Application
===================================================
Python + PyQt6 | WASAPI loopback | faster-whisper | LangChain RAG

Run:  python main_window.py
"""

# Fix Windows COM threading conflict (must be before ANY other import)
import sys
sys.coinit_flags = 2   # COINIT_APARTMENTTHREADED — matches what Qt expects
import os
import re
import threading
import queue
import time
import numpy as np
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QTextEdit, QFileDialog,
    QSplitter, QFrame, QScrollArea, QProgressBar, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QSizePolicy
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QSize
)
from PyQt6.QtGui import (
    QColor, QPalette, QFont, QIcon, QTextCursor
)

from audio_capture import AudioCapture
from transcription_worker import TranscriptionWorker, lookslike_question
from transcription_worker import _correct_terms as correct_oracle_terms
from qa_worker import QAWorker

# ── Dark theme palette ─────────────────────────────────────────────────────────
DARK_BG      = "#0f172a"
SURFACE      = "#1e293b"
SURFACE2     = "#273348"
BORDER       = "#334155"
PRIMARY      = "#6366f1"
SUCCESS      = "#22c55e"
DANGER       = "#ef4444"
WARNING      = "#f59e0b"
TEXT         = "#f1f5f9"
TEXT_MUTED   = "#94a3b8"
LIVE_COLOR   = "#f97316"

STYLE_SHEET = f"""
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
    padding: 4px;
}}
QPushButton {{
    border-radius: 7px;
    padding: 8px 18px;
    font-weight: 600;
    font-size: 13px;
    border: none;
}}
QPushButton#btnStart {{
    background-color: {PRIMARY};
    color: white;
}}
QPushButton#btnStart:hover {{ background-color: #4f46e5; }}
QPushButton#btnStart:disabled {{ opacity: 0.5; background-color: #4f46e5; }}
QPushButton#btnStop {{
    background-color: rgba(239,68,68,0.15);
    border: 1px solid rgba(239,68,68,0.4);
    color: {DANGER};
}}
QPushButton#btnStop:hover {{ background-color: rgba(239,68,68,0.28); }}
QPushButton#btnPause {{
    background-color: rgba(245,158,11,0.15);
    border: 1px solid rgba(245,158,11,0.4);
    color: {WARNING};
}}
QPushButton#btnPause:hover {{ background-color: rgba(245,158,11,0.28); }}
QPushButton#btnClear {{
    background-color: rgba(239,68,68,0.1);
    border: 1px solid rgba(239,68,68,0.3);
    color: {DANGER};
    padding: 4px 12px;
    font-size: 12px;
}}
QPushButton#btnUpload {{
    background-color: rgba(99,102,241,0.15);
    border: 1px solid rgba(99,102,241,0.4);
    color: {PRIMARY};
}}
QPushButton#btnUpload:hover {{ background-color: rgba(99,102,241,0.28); }}
QPushButton#btnAsk {{
    background-color: {PRIMARY};
    color: white;
    padding: 8px 16px;
}}
QPushButton#btnAsk:hover {{ background-color: #4f46e5; }}
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
    selection-background-color: {PRIMARY};
}}
QLineEdit {{
    background-color: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 8px 12px;
    color: {TEXT};
}}
QLineEdit:focus {{ border-color: {PRIMARY}; }}
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
    font-size: 20px;
    font-weight: 800;
    color: {PRIMARY};
}}
QLabel#subtext {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QLabel#status {{
    color: {SUCCESS};
    font-size: 12px;
    font-weight: 600;
}}
QLabel#statusIdle {{
    color: {TEXT_MUTED};
    font-size: 12px;
}}
QProgressBar {{
    background-color: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 4px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {PRIMARY};
    border-radius: 4px;
}}
QSplitter::handle {{
    background-color: {BORDER};
    width: 1px;
}}
"""


class TranscriptCard(QFrame):
    """A single transcript + answer card in the feed."""
    def __init__(self, timestamp: str, text: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background: {SURFACE};
                border: 1px solid {BORDER};
                border-radius: 8px;
                margin: 2px 0;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(4)

        ts = QLabel(f"🎙 {timestamp}")
        ts.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        lay.addWidget(ts)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {TEXT}; font-size: 13px;")
        lay.addWidget(body)


class AnswerCard(QFrame):
    """An auto-assist Q&A answer card."""
    def __init__(self, question: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background: {SURFACE};
                border-left: 3px solid {PRIMARY};
                border-top: 1px solid {BORDER};
                border-right: 1px solid {BORDER};
                border-bottom: 1px solid {BORDER};
                border-radius: 8px;
                margin: 2px 0;
            }}
        """)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(14, 10, 14, 10)
        self._lay.setSpacing(6)

        hdr = QLabel(f"🤖  Auto-assist  ·  {datetime.now().strftime('%I:%M %p')}")
        hdr.setStyleSheet(f"color: {PRIMARY}; font-size: 11px; font-weight: 700;")
        self._lay.addWidget(hdr)

        q_lbl = QLabel(question[:140] + ("…" if len(question) > 140 else ""))
        q_lbl.setWordWrap(True)
        q_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-style: italic; font-size: 12px;")
        self._lay.addWidget(q_lbl)

        self._answer_lbl = QLabel("⏳  Thinking…")
        self._answer_lbl.setWordWrap(True)
        self._answer_lbl.setStyleSheet(f"color: {TEXT}; font-size: 13px; line-height: 1.6;")
        self._answer_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._lay.addWidget(self._answer_lbl)

        self._answer_text = ""

    def append_token(self, token: str):
        self._answer_text += token
        self._answer_lbl.setText(self._answer_text + " ▍")

    def finish(self, source_type: str = "docs"):
        self._answer_lbl.setText(self._answer_text)
        badge_color = SUCCESS if source_type == "docs" else "#63b3ed"
        badge_icon  = "📄 Docs" if source_type == "docs" else "🌐 Web"
        badge = QLabel(badge_icon)
        badge.setStyleSheet(
            f"background: rgba(34,197,94,0.13); color: {badge_color}; "
            f"border-radius: 8px; padding: 2px 10px; font-size: 11px; font-weight: 700;"
        )
        badge.setMaximumWidth(80)
        self._lay.addWidget(badge)

    def set_error(self, msg: str):
        self._answer_lbl.setText(f"⚠️ {msg}")
        self._answer_lbl.setStyleSheet(f"color: {DANGER}; font-size: 13px;")


class FeedWidget(QScrollArea):
    """Scrollable feed holding transcript + answer cards."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout.setSpacing(6)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.setWidget(self._container)

        self._placeholder = QLabel("🎙  Listening — start speaking or play a video…")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px; padding: 40px;")
        self._layout.addWidget(self._placeholder)

    def add_transcript(self, timestamp: str, text: str) -> TranscriptCard:
        if self._placeholder:
            self._placeholder.hide()
        card = TranscriptCard(timestamp, text)
        self._layout.addWidget(card)
        QTimer.singleShot(50, self._scroll_bottom)
        return card

    def add_answer(self, question: str) -> AnswerCard:
        if self._placeholder:
            self._placeholder.hide()
        card = AnswerCard(question)
        self._layout.addWidget(card)
        QTimer.singleShot(50, self._scroll_bottom)
        return card

    def clear_feed(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._placeholder = QLabel("🗑  Cleared — listening…")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px; padding: 40px;")
        self._layout.addWidget(self._placeholder)

    def _scroll_bottom(self):
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OIC / VBCS Live Q&A Assistant")
        self.setMinimumSize(1100, 720)
        self.resize(1200, 800)

        # ── State ──────────────────────────────────────────────────────────────
        self._recording   = False
        self._paused      = False
        self._chunk_count = 0
        self._qa_active   = False
        self._qa_history  = []
        self._pending_q   = ""
        self._qa_timer    = None
        self._cur_answer_card: AnswerCard | None = None
        self._dev_items   = []
        self._pending_index_files: list = []
        self._transcript_buffer: list[str] = []   # persists across start/stop cycles

        # ── Interview log — one MD file per session ─────────────────────────
        self._interviews_dir = Path(__file__).parent / "Interviews"
        self._interviews_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._interview_file = self._interviews_dir / f"Interview_{ts}.md"
        self._interview_count = 0   # Q&A pair counter within this session

        # ── Workers ────────────────────────────────────────────────────────────
        self._audio = AudioCapture()
        self._trans = TranscriptionWorker()
        self._qa    = QAWorker()

        self._audio.chunk_ready.connect(self._on_audio_chunk)
        self._audio.device_list_ready.connect(self._on_device_list)
        self._audio.audio_level.connect(self._on_audio_level)
        self._audio.error.connect(lambda m: self._set_bottom(f"Audio: {m}"))

        self._trans.transcript_ready.connect(self._on_transcript)
        self._trans.model_loaded.connect(self._on_model_loaded)
        self._trans.error.connect(self._on_trans_error)

        self._qa.token_ready.connect(self._on_qa_token)
        self._qa.answer_done.connect(self._on_qa_done)
        self._qa.error.connect(self._on_qa_error)
        self._qa.service_ready.connect(
            lambda label: self._set_bottom(f"Q&A ready — {label}")
        )
        self._qa.index_progress.connect(self._on_index_progress)
        self._qa.index_done.connect(self._on_index_count)

        # Search delay timer — fires API search after user stays stopped
        # Quick stop+start = no search; longer stop = fire search
        self._search_delay_timer = QTimer(self)
        self._search_delay_timer.setSingleShot(True)
        self._search_delay_timer.timeout.connect(self._fire_search)

        self._build_ui()
        self._refresh_devices()
        self._qa.start_service()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(STYLE_SHEET)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("OIC / VBCS  Live Q&A")
        title.setObjectName("heading")
        sub   = QLabel("Windows Desktop  ·  WASAPI Audio  ·  Whisper  ·  Oracle RAG")
        sub.setObjectName("subtext")
        self._status_lbl = QLabel("● Ready")
        self._status_lbl.setObjectName("statusIdle")
        hdr.addWidget(title)
        hdr.addWidget(sub)
        hdr.addStretch()
        hdr.addWidget(self._status_lbl)
        root.addLayout(hdr)

        # Main splitter: left = controls + feed, right = docs + manual Q
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)

        # ── LEFT PANEL ────────────────────────────────────────────────────────
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 6, 0)
        left_lay.setSpacing(10)

        # Audio source controls
        ctrl_card = self._make_card()
        ctrl_inner = QVBoxLayout(ctrl_card)
        ctrl_inner.setSpacing(10)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("Audio Source:"))
        self._src_combo = QComboBox()
        self._src_combo.addItems([
            "🖥  System Audio  (WASAPI — YouTube / Teams / Zoom)",
            "🎙  Microphone Only",
            "🖥🎙  System Audio + Microphone",
        ])
        src_row.addWidget(self._src_combo, 1)
        ctrl_inner.addLayout(src_row)

        dev_row = QHBoxLayout()
        dev_row.addWidget(QLabel("Device:"))
        self._dev_combo = QComboBox()
        dev_row.addWidget(self._dev_combo, 1)
        self._refresh_btn = QPushButton("↺")
        self._refresh_btn.setMaximumWidth(36)
        self._refresh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._refresh_btn.clicked.connect(self._refresh_devices)
        dev_row.addWidget(self._refresh_btn)
        ctrl_inner.addLayout(dev_row)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Whisper Model:"))
        self._model_combo = QComboBox()
        self._model_combo.addItems([
            "base     (fastest)",
            "small    (balanced)",
            "medium   (accurate)",
            "large-v3 (best accuracy)",
        ])
        self._model_combo.setCurrentIndex(1)
        model_row.addWidget(self._model_combo, 1)
        ctrl_inner.addLayout(model_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._start_btn = QPushButton("▶  Start")
        self._start_btn.setObjectName("btnStart")
        self._start_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._start_btn.clicked.connect(self._start_session)

        self._pause_btn = QPushButton("⏸  Pause")
        self._pause_btn.setObjectName("btnPause")
        self._pause_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._toggle_pause)

        self._stop_btn  = QPushButton("⏹  Stop")
        self._stop_btn.setObjectName("btnStop")
        self._stop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_session)

        self._clear_btn = QPushButton("🗑 Clear")
        self._clear_btn.setObjectName("btnClear")
        self._clear_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clear_btn.clicked.connect(self._clear_feed)

        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._pause_btn)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._clear_btn)
        ctrl_inner.addLayout(btn_row)

        # Timer + chunk counter row
        info_row = QHBoxLayout()
        self._timer_lbl  = QLabel("00:00")
        self._timer_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size:13px;")
        self._chunk_lbl  = QLabel("0 chunks")
        self._chunk_lbl.setStyleSheet(f"color: {TEXT_MUTED}; font-size:12px;")
        self._rec_dot    = QLabel("●")
        self._rec_dot.setStyleSheet(f"color: {TEXT_MUTED}; font-size:14px;")
        info_row.addWidget(self._rec_dot)
        info_row.addWidget(self._timer_lbl)
        info_row.addStretch()
        info_row.addWidget(self._chunk_lbl)
        ctrl_inner.addLayout(info_row)

        left_lay.addWidget(ctrl_card)

        # Transcript feed label row
        feed_hdr = QHBoxLayout()
        feed_hdr.addWidget(self._make_section_label("LIVE TRANSCRIPT"))
        feed_hdr.addStretch()
        left_lay.addLayout(feed_hdr)

        # Feed
        self._feed = FeedWidget()
        left_lay.addWidget(self._feed, 1)

        # Timer (clock)
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_timer)
        self._elapsed_secs = 0

        # ── RIGHT PANEL ───────────────────────────────────────────────────────
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(6, 0, 0, 0)
        right_lay.setSpacing(10)

        # Document upload
        doc_card = self._make_card()
        doc_inner = QVBoxLayout(doc_card)
        doc_inner.setSpacing(8)
        doc_inner.addWidget(self._make_section_label("KNOWLEDGE BASE"))

        self._doc_status = QLabel("0 documents in knowledge base")
        self._doc_status.setStyleSheet(f"color: {TEXT_MUTED}; font-size:12px;")
        doc_inner.addWidget(self._doc_status)

        self._doc_list = QListWidget()
        self._doc_list.setMaximumHeight(120)
        self._doc_list.setStyleSheet(f"font-size:12px;")
        doc_inner.addWidget(self._doc_list)

        doc_btns = QHBoxLayout()
        self._upload_btn = QPushButton("📂  Upload Documents")
        self._upload_btn.setObjectName("btnUpload")
        self._upload_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._upload_btn.clicked.connect(self._upload_docs)
        self._clear_docs_btn = QPushButton("Clear Docs")
        self._clear_docs_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clear_docs_btn.clicked.connect(self._clear_docs)
        self._clear_docs_btn.setStyleSheet(f"color:{TEXT_MUTED}; background:transparent; border:1px solid {BORDER}; border-radius:6px; padding:5px 10px; font-size:12px;")
        doc_btns.addWidget(self._upload_btn, 1)
        doc_btns.addWidget(self._clear_docs_btn)
        doc_inner.addLayout(doc_btns)

        self._index_progress = QProgressBar()
        self._index_progress.setMaximumHeight(5)
        self._index_progress.setVisible(False)
        self._index_progress.setRange(0, 100)
        doc_inner.addWidget(self._index_progress)

        right_lay.addWidget(doc_card)

        # Manual question
        ask_card = self._make_card()
        ask_inner = QVBoxLayout(ask_card)
        ask_inner.setSpacing(8)
        ask_inner.addWidget(self._make_section_label("ASK A QUESTION"))

        self._question_input = QLineEdit()
        self._question_input.setPlaceholderText("Type a question about OIC / VBCS…")
        self._question_input.returnPressed.connect(self._manual_ask)
        ask_inner.addWidget(self._question_input)

        self._ask_btn = QPushButton("Ask")
        self._ask_btn.setObjectName("btnAsk")
        self._ask_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._ask_btn.clicked.connect(self._manual_ask)
        ask_inner.addWidget(self._ask_btn)
        right_lay.addWidget(ask_card)

        # Auto-Q&A settings
        settings_card = self._make_card()
        set_inner = QVBoxLayout(settings_card)
        set_inner.setSpacing(6)
        set_inner.addWidget(self._make_section_label("AUTO-Q&A SETTINGS"))

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Trigger sensitivity:"))
        self._sens_combo = QComboBox()
        self._sens_combo.addItems(["High (3 s delay)", "Medium (5 s delay)", "Low (8 s delay)"])
        row1.addWidget(self._sens_combo, 1)
        set_inner.addLayout(row1)

        self._autoqa_label = QLabel("Space = Start/Stop  —  search fires after delay on stop")
        self._autoqa_label.setStyleSheet(f"color: {SUCCESS}; font-size:12px;")
        set_inner.addWidget(self._autoqa_label)
        right_lay.addWidget(settings_card)

        # History panel
        hist_card = self._make_card()
        hist_inner = QVBoxLayout(hist_card)
        hist_inner.setSpacing(6)
        hist_inner.addWidget(self._make_section_label("QUESTION HISTORY"))
        self._history_list = QListWidget()
        self._history_list.setStyleSheet(f"font-size:12px;")
        self._history_list.itemDoubleClicked.connect(self._replay_history)
        hist_inner.addWidget(self._history_list, 1)
        right_lay.addWidget(hist_card, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([680, 420])
        root.addWidget(splitter, 1)

        # Bottom status bar
        status_bar = QHBoxLayout()
        self._bottom_status = QLabel("Ready  —  upload .md files to knowledge_base/ folder or use Upload button")
        self._bottom_status.setStyleSheet(f"color: {TEXT_MUTED}; font-size:11px;")
        status_bar.addWidget(self._bottom_status)
        root.addLayout(status_bar)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _make_card(self) -> QFrame:
        f = QFrame()
        f.setObjectName("card")
        return f

    def _make_section_label(self, text: str) -> QLabel:
        lbl = QLabel(text.upper())   # uppercase via Python, not CSS
        lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 10px; font-weight: 700;"
        )
        return lbl

    def _set_status(self, text: str, color: str = TEXT_MUTED):
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 600;")

    def _set_bottom(self, text: str):
        self._bottom_status.setText(text)

    def _pause_ms(self) -> int:
        idx = self._sens_combo.currentIndex()
        return [3000, 5000, 8000][idx]

    def _search_delay_ms(self) -> int:
        """Delay before firing API search after stop.
        Uses the sensitivity setting: High=3s, Medium=5s, Low=8s."""
        idx = self._sens_combo.currentIndex()
        return [3000, 5000, 8000][idx]

    # ── Device refresh ──────────────────────────────────────────────────────────

    def _refresh_devices(self):
        self._dev_combo.clear()
        # Call synchronously on the main thread — fast operation, safe for Qt
        try:
            devices = self._audio.enumerate_devices()
        except Exception as ex:
            devices = [("default", "Default Device", True)]
            self._set_bottom(f"Device scan error: {ex}")
        self._on_device_list(devices)

    def _on_device_list(self, devices: list):
        self._dev_combo.clear()
        self._dev_items = devices
        for dev_id, name, is_loopback in devices:
            self._dev_combo.addItem(name, userData=dev_id)
        if not devices:
            self._dev_combo.addItem("(no devices found)")

    # ── Session control ────────────────────────────────────────────────────────

    def _start_session(self):
        # If search timer is still counting down, cancel it — quick stop+start
        # Otherwise it means search already fired or completed — clear for fresh start
        quick_restart = self._search_delay_timer.isActive()
        if quick_restart:
            self._search_delay_timer.stop()
            self._set_bottom("Search cancelled — continuing to listen…")
        else:
            # Search already completed (or never started) — clear old data
            self._feed.clear_feed()
            self._transcript_buffer.clear()
            self._chunk_count = 0
            self._chunk_lbl.setText("0 chunks")
            self._elapsed_secs = 0
            self._timer_lbl.setText("00:00")

        # Reset Q&A state so it doesn't block future searches
        self._qa_active = False
        self._cur_answer_card = None

        # ── Check faster-whisper is installed ─────────────────────────────────
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except ImportError:
            QMessageBox.critical(self, "Missing Package",
                "faster-whisper is not installed.\n\n"
                "Run this in your terminal:\n"
                "    pip install faster-whisper\n\n"
                "Then restart the app.")
            return

        model_name = self._model_combo.currentText().split()[0]
        src_idx    = self._src_combo.currentIndex()

        # Map source combo to AudioCapture constants
        from audio_capture import AudioCapture as _AC
        source_map = {0: _AC.SOURCE_SYSTEM, 1: _AC.SOURCE_MIC, 2: _AC.SOURCE_BOTH}
        self._audio.set_source(source_map.get(src_idx, _AC.SOURCE_SYSTEM))

        dev_id = self._dev_combo.currentData()
        self._audio.set_device(dev_id)

        # Start transcription worker
        self._trans.load_model(model_name)
        if not self._trans.isRunning():
            self._trans.start()

        # Stop audio first to avoid "device already open" errors on rapid start/stop
        try:
            self._audio.stop()
        except Exception:
            pass

        # Start audio capture
        try:
            self._audio.start()
        except Exception as ex:
            QMessageBox.critical(self, "Audio Error",
                f"Could not open audio device:\n{ex}\n\n"
                "Make sure the device is connected and not in use.")
            return

        self._recording = True
        self._paused    = False
        self._pending_q = ""

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._pause_btn.setEnabled(True)
        self._pause_btn.setText("⏸  Pause")
        self._rec_dot.setStyleSheet(f"color: {WARNING}; font-size:14px;")
        self._clock_timer.start(1000)

        if quick_restart:
            self._set_status("● Recording", DANGER)
            self._set_bottom(
                f"Listening… ({len(self._transcript_buffer)} previous chunks carried forward)"
            )
        else:
            self._set_status("⏳ Loading model…", WARNING)
            self._set_bottom(
                f"Loading Whisper '{model_name}' model — first run downloads it (~300 MB). "
                f"Transcript will appear once loaded…"
            )

    def _toggle_pause(self):
        if not self._paused:
            self._paused = True
            self._audio.pause()
            self._trans.pause()
            self._clock_timer.stop()
            self._pause_btn.setText("▶  Resume")
            self._rec_dot.setStyleSheet(f"color: {WARNING}; font-size:14px;")
            self._set_status("⏸ Paused", WARNING)
            # Start search delay — same as stop behavior
            if self._transcript_buffer and not self._qa_active:
                delay_ms = self._search_delay_ms()
                self._search_delay_timer.start(delay_ms)
                secs = delay_ms // 1000
                self._set_bottom(f"Paused — searching in {secs}s… (Resume to cancel)")
            else:
                self._set_bottom("Paused — no new transcript to search")
        else:
            self._paused = False
            # Cancel pending search if user resumes quickly
            if self._search_delay_timer.isActive():
                self._search_delay_timer.stop()
            self._audio.resume()
            self._trans.resume()
            self._clock_timer.start(1000)
            self._pause_btn.setText("⏸  Pause")
            self._rec_dot.setStyleSheet(f"color: {DANGER}; font-size:14px;")
            self._set_status("● Recording", DANGER)
            self._set_bottom("Resumed — listening…")

    def _stop_session(self):
        self._recording = False
        self._paused    = False
        try:
            self._audio.stop()
        except Exception:
            pass
        self._trans.resume()   # unblock if paused
        self._clock_timer.stop()

        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._pause_btn.setEnabled(False)
        self._pause_btn.setText("⏸  Pause")
        self._rec_dot.setStyleSheet(f"color: {TEXT_MUTED}; font-size:14px;")

        # KEEP transcript buffer — it carries across start/stop cycles
        # Start search delay timer — if user stays stopped, fire API search
        if self._transcript_buffer:
            delay_ms = self._search_delay_ms()
            self._search_delay_timer.start(delay_ms)
            secs = delay_ms // 1000
            self._set_status("● Stopped", WARNING)
            if self._qa_active:
                self._set_bottom(
                    f"Stopped — previous search still running; "
                    f"new search queued in {secs}s…"
                )
            else:
                self._set_bottom(
                    f"Stopped — searching in {secs}s… "
                    f"(Press Space to resume without searching)"
                )
        else:
            self._set_status("● Stopped", TEXT_MUTED)
            self._set_bottom("Stopped — no transcript to search. Press Space to start.")

    def _clear_feed(self):
        self._feed.clear_feed()
        self._chunk_count = 0
        self._chunk_lbl.setText("0 chunks")
        self._pending_q = ""
        self._transcript_buffer.clear()
        self._qa_history.clear()
        self._history_list.clear()
        if self._search_delay_timer.isActive():
            self._search_delay_timer.stop()
        self._set_bottom("Cleared — press Space to start fresh.")

    # ── Timer ──────────────────────────────────────────────────────────────────

    def _tick_timer(self):
        self._elapsed_secs += 1
        m, s = divmod(self._elapsed_secs, 60)
        self._timer_lbl.setText(f"{m:02d}:{s:02d}")

    # ── Audio → Transcript ─────────────────────────────────────────────────────

    def _on_audio_level(self, rms: float):
        """Show real-time audio level in the status bar."""
        if not self._recording or self._paused:
            return
        bars = int(min(rms * 500, 10))
        self._set_bottom(f"🎙 Audio level: {'█' * bars}{'░' * (10 - bars)}  RMS={rms:.5f} — listening…")

    def _on_audio_chunk(self, pcm_bytes: bytes, sample_rate: int):
        if not self._recording or self._paused:
            return
        self._trans.enqueue_chunk(pcm_bytes, sample_rate)

    def _on_transcript(self, text: str, timestamp: str):
        if not text.strip():
            return
        # Filter hallucinations and noise
        if self._is_noise(text):
            return
        self._chunk_count += 1
        self._chunk_lbl.setText(f"{self._chunk_count} chunks")
        self._feed.add_transcript(timestamp, text)
        # Accumulate ALL transcript text — persists across start/stop cycles
        corrected = correct_oracle_terms(text.strip())
        self._transcript_buffer.append(corrected)

    def _on_model_loaded(self):
        """Called once Whisper model is ready — switch status to Recording."""
        self._rec_dot.setStyleSheet(f"color: {DANGER}; font-size:14px;")
        self._set_status("● Recording", DANGER)
        self._set_bottom("Whisper model ready — listening for speech…")

    def _on_trans_error(self, msg: str):
        self._set_bottom(f"Transcription error: {msg}")
        self._set_status("⚠ Error", WARNING)

    # ── Noise filtering ────────────────────────────────────────────────────────

    _NOISE_PATTERNS = [
        "thank you for watching",
        "please like and subscribe",
        "for more information visit",
        "www.", ".com", ".org", ".net",
        "microsoft.com", "oracle.com",
        "bye-bye", "goodbye",
    ]

    def _is_noise(self, text: str) -> bool:
        t = text.lower().strip()
        if len(t.replace(" ", "").replace(".", "")) < 3:
            return True
        words = t.split()
        if len(words) >= 2:
            unique = set(w.strip(".,!?") for w in words)
            if len(unique) == 1:
                return True   # hallucination: "you you you" / "VBCS VBCS VBCS"
        for pat in self._NOISE_PATTERNS:
            if pat in t:
                return True
        return False

    # ── Search pipeline (spacebar-controlled) ────────────────────────────────

    def _fire_search(self):
        """Called by search delay timer after user stays stopped.
        Uses ALL accumulated transcript from all start/stop cycles."""
        full_transcript = " ".join(self._transcript_buffer).strip()

        if not full_transcript:
            self._set_bottom("No transcript to search.")
            return

        # If a previous search is still running, force-reset and proceed
        if self._qa_active:
            self._qa_active = False
            self._cur_answer_card = None

        # History check
        hist = self._find_history(full_transcript)
        if hist:
            display = full_transcript[:120] + ("…" if len(full_transcript) > 120 else "")
            card = self._feed.add_answer(display)
            card.append_token(hist["answer"])
            card.finish("docs")
            self._set_bottom("Answered from history")
            # Save to interview file
            self._save_to_interview(display, hist["answer"], source="history")
            # Clear buffer after successful search
            self._transcript_buffer.clear()
            return

        self._set_bottom("Searching knowledge base with full transcript…")
        self._run_qa(full_transcript, transcript_context=full_transcript)

    def _run_qa(self, question: str, transcript_context: str = ""):
        self._qa_active = True
        # Show a shortened version in the card
        display_q = question[:200] + ("..." if len(question) > 200 else "")
        self._cur_answer_card = self._feed.add_answer(display_q)
        self._qa.ask(question, transcript_context=transcript_context)

    # ── QA callbacks ───────────────────────────────────────────────────────────

    def _on_qa_token(self, token: str):
        if self._cur_answer_card:
            self._cur_answer_card.append_token(token)

    def _on_qa_done(self, source_type: str, full_answer: str):
        q = ""
        try:
            if self._cur_answer_card:
                self._cur_answer_card.finish(source_type)
                # Save to history
                try:
                    item = self._cur_answer_card._lay.itemAt(1)
                    if item:
                        lbl = item.widget()
                        if isinstance(lbl, QLabel):
                            q = lbl.text()
                except Exception:
                    pass
                self._save_history(q or "question", full_answer)
        except Exception:
            pass
        # Save to interview file
        self._save_to_interview(q or "question", full_answer, source=source_type)
        self._qa_active = False
        self._cur_answer_card = None
        # Clear transcript buffer after successful search
        self._transcript_buffer.clear()
        self._set_bottom("Search complete — Press Space to start listening again.")

    def _on_qa_error(self, msg: str):
        try:
            if self._cur_answer_card:
                self._cur_answer_card.set_error(msg)
        except Exception:
            pass
        self._qa_active = False
        self._cur_answer_card = None
        self._set_bottom(f"Search error: {msg}")

    # ── Manual Q&A ─────────────────────────────────────────────────────────────

    def _manual_ask(self):
        q = self._question_input.text().strip()
        if not q:
            return
        self._question_input.clear()
        hist = self._find_history(q)
        if hist:
            card = self._feed.add_answer(q)
            card.append_token(hist["answer"])
            card.finish("docs")
            # Save to interview file
            self._save_to_interview(q, hist["answer"], source="history")
            return
        # Include any accumulated transcript as context for manual questions too
        transcript_context = " ".join(self._transcript_buffer) if self._transcript_buffer else ""
        self._run_qa(q, transcript_context=transcript_context)

    # ── History ────────────────────────────────────────────────────────────────

    def _qa_key(self, q: str) -> str:
        return re.sub(r'\s+', ' ',
            re.sub(r'[^a-z0-9 ]', '',
            re.sub(r'\b(what|is|are|the|a|an|of|in|by|to|do|does|mean|about)\b', ' ',
            q.lower()))).strip()

    def _similarity(self, a: str, b: str) -> float:
        wa = set(a.split())
        wb = set(b.split())
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / max(len(wa), len(wb))

    def _find_history(self, q: str):
        key = self._qa_key(q)
        best, best_score = None, 0.0
        for h in self._qa_history:
            s = self._similarity(key, h["key"])
            if s > best_score:
                best_score, best = s, h
        return best if best_score >= 0.72 else None

    def _save_history(self, question: str, answer: str):
        entry = {"key": self._qa_key(question), "question": question, "answer": answer}
        self._qa_history.append(entry)
        item = QListWidgetItem(f"♻  {question[:60]}")
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setForeground(QColor(TEXT_MUTED))
        self._history_list.addItem(item)

    def _save_to_interview(self, question: str, answer: str, source: str = ""):
        """Append a Q&A pair to the session's Interview markdown file."""
        try:
            self._interview_count += 1
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            is_new = not self._interview_file.exists()

            with open(self._interview_file, "a", encoding="utf-8") as f:
                if is_new:
                    session_ts = datetime.now().strftime("%B %d, %Y %I:%M %p")
                    f.write(f"# Interview Session — {session_ts}\n\n")
                    f.write("---\n\n")

                f.write(f"## Q{self._interview_count}  ({ts})\n\n")
                f.write(f"**Transcript / Question:**\n\n")
                f.write(f"{question.strip()}\n\n")
                f.write(f"**Answer:**\n\n")
                f.write(f"{answer.strip()}\n\n")
                if source:
                    f.write(f"*Source: {source}*\n\n")
                f.write("---\n\n")
        except Exception:
            pass

    def _replay_history(self, item: QListWidgetItem):
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry:
            card = self._feed.add_answer(entry["question"])
            card.append_token(entry["answer"])
            card.finish("docs")

    # ── Document management ────────────────────────────────────────────────────

    def _upload_docs(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Upload Documents",
            str(Path.home()),
            "Documents (*.pdf *.docx *.txt *.md *.csv);;All Files (*)"
        )
        if not files:
            return
        self._index_progress.setRange(0, len(files))
        self._index_progress.setValue(0)
        self._index_progress.setVisible(True)
        self._upload_btn.setEnabled(False)
        self._pending_index_files = [Path(fp).name for fp in files]
        self._set_bottom(f"Adding {len(files)} file(s) to knowledge base…")
        # Queue each file — instant copy to knowledge_base/ folder
        for fp in files:
            self._qa.index_file(fp)

    def _on_index_progress(self, done: int, total: int):
        if total > 0:
            self._index_progress.setRange(0, total)
            self._index_progress.setValue(done)

    def _on_index_count(self, total_docs: int):
        self._index_progress.setVisible(False)
        self._upload_btn.setEnabled(True)
        names = getattr(self, "_pending_index_files", [])
        for name in names:
            # Add to list only if not already there
            existing = [self._doc_list.item(i).text() for i in range(self._doc_list.count())]
            if f"📄  {name}" not in existing:
                self._doc_list.addItem(f"📄  {name}")
        self._pending_index_files = []
        self._doc_status.setText(f"{total_docs} documents in knowledge base")
        self._set_bottom(f"Upload complete — {total_docs} documents ready for search")

    def _clear_docs(self):
        self._qa.clear_docs()   # queues a _ClearTask
        self._doc_list.clear()
        self._doc_status.setText("0 documents in knowledge base")
        self._set_bottom("Knowledge base cleared")

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space:
            # Don't intercept Space when typing in the question input
            if self._question_input.hasFocus():
                super().keyPressEvent(event)
                return
            # Space = Start / Stop toggle (data stays visible on stop)
            if not self._recording:
                self._start_session()       # stopped → start
            else:
                self._stop_session()        # recording → stop (keeps data)
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
            self._qa.stop()
            if self._qa._thread.isRunning():
                self._qa._thread.wait(3000)
        except Exception:
            pass
        event.accept()


def main():
    import traceback

    # High-DPI — must be set BEFORE QApplication is created
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("OIC VBCS Q&A")

    # Show any unhandled errors in a dialog instead of silent crash
    def _excepthook(exc_type, exc_value, exc_tb):
        err = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(err)
        QMessageBox.critical(None, "Error", err[:2000])
    sys.excepthook = _excepthook

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
