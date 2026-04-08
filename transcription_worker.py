"""
transcription_worker.py
=======================
Runs faster-whisper in a QThread.

Signals emitted (all on the main/GUI thread via Qt's cross-thread signal mechanism):
    transcript_ready(str, str)   — (text, timestamp HH:MM:SS)
    model_loaded()               — emitted once after model loads
    error(str)                   — human-readable error message

Dependencies:
    pip install faster-whisper PyQt6
    # GPU (optional): pip install torch --index-url https://download.pytorch.org/whl/cu118
"""

from __future__ import annotations

import io
import queue
import time
import re
import logging
import numpy as np
from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

try:
    from faster_whisper import WhisperModel
    _FW_AVAILABLE = True
except ImportError:
    _FW_AVAILABLE = False


# ── Oracle / OIC vocabulary hints fed to Whisper ──────────────────────────────
INITIAL_PROMPT = (
    "OIC Oracle Integration Cloud VBCS Visual Builder Cloud Service "
    "REST SOAP adapter integration flow mapping transformation "
    "process automation FTP file server database adapter lookup "
    "orchestration instance monitoring error resubmit"
)

# ── Noise / hallucination patterns to discard ──────────────────────────────────
_NOISE_PATTERNS = [
    re.compile(r"^\s*[\.\,\!\?;:\-]+\s*$"),
    re.compile(r"(?:thank you for watching|subscribe|like and subscribe)", re.I),
    re.compile(r"(?:www\.|http|\.com|\.org|\.net)", re.I),
    re.compile(r"(?:microsoft\.com|oracle\.com|youtube\.com)", re.I),
    re.compile(r"^(?:you[\s\.]*){2,}$", re.I),
    re.compile(r"^\s*[\s\.]+\s*$"),
]

# Oracle domain terms — auto-QA trigger words
_ORACLE_TERMS = re.compile(
    r"\b(oic|vbcs|oracle|integration|cloud|adapter|rest|soap|ftp|"
    r"mapping|flow|orchestration|automation|visual builder|faas|"
    r"ics|bpel|soa|middleware|fusion|erp|hcm)\b",
    re.I,
)

SAMPLE_RATE = 16_000


def _correct_terms(text: str) -> str:
    """Correct common Whisper mis-transcriptions of Oracle terms."""
    fixes = {
        r"\bbbcs\b": "VBCS",
        r"\bvbc\'?s\b": "VBCS",
        r"\bpbcs\b": "VBCS",
        r"\boic\b": "OIC",
        r"\boik\b": "OIC",
        r"\bo\.?i\.?c\.?\b": "OIC",
        r"\boracle integration cloud\b": "Oracle Integration Cloud",
        r"\bvisual builder\b": "Visual Builder",
    }
    for pattern, replacement in fixes.items():
        text = re.sub(pattern, replacement, text, flags=re.I)
    return text


def _is_noise(text: str) -> bool:
    """Return True if this transcript chunk is garbage / noise."""
    stripped = text.strip()
    if len(stripped) < 3:
        return True
    for pat in _NOISE_PATTERNS:
        if pat.search(stripped):
            return True
    # Hallucination: all words identical (Whisper echoing initial_prompt into silence)
    words = stripped.lower().split()
    if len(words) >= 3 and len(set(words)) == 1:
        return True
    return False


def _lookslike_question(text: str) -> bool:
    """Return True if this looks like a genuine question worth auto-answering."""
    t = text.strip().lower()
    # Must have at least 3 words
    if len(t.split()) < 3:
        return False
    # Has explicit question mark
    if "?" in t:
        return True
    # Starts with a question word AND contains an Oracle term
    question_starters = ("what", "how", "why", "when", "where", "which",
                          "who", "can", "does", "is", "are", "explain")
    starts_with_q = any(t.startswith(w) for w in question_starters)
    has_oracle = bool(_ORACLE_TERMS.search(t))
    return starts_with_q and has_oracle


class TranscriptionWorker(QThread):
    """
    QThread that pulls float32 PCM bytes from an internal queue and
    transcribes them with faster-whisper.

    Usage
    -----
    worker = TranscriptionWorker()
    audio_capture.chunk_ready.connect(worker.enqueue_chunk)
    worker.transcript_ready.connect(my_slot)
    worker.start()          # starts the thread
    worker.load_model("small")   # loads model (can be called before or after start)
    """

    transcript_ready = pyqtSignal(str, str)   # (cleaned_text, timestamp)
    model_loaded     = pyqtSignal()
    error            = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue      = queue.Queue(maxsize=64)
        self._model      = None
        self._model_name = "small"
        self._running    = True
        self._paused     = False

    # ── Public API (called from main thread) ───────────────────────────────────

    def load_model(self, model_name: str = "small"):
        """
        Load (or reload) a Whisper model.
        Safe to call from any thread — model is loaded inside the worker thread.
        """
        self._model_name = model_name
        self._model = None   # will be reloaded on next transcription

    def enqueue_chunk(self, pcm_bytes: bytes, sample_rate: int):
        """Slot connected to AudioCapture.chunk_ready."""
        if self._paused:
            return
        try:
            self._queue.put_nowait((pcm_bytes, sample_rate))
        except queue.Full:
            logger.warning("Transcription queue full — dropped audio chunk")

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def stop_worker(self):
        self._running = False
        self._queue.put_nowait(None)   # sentinel to unblock queue.get()

    # ── QThread.run ────────────────────────────────────────────────────────────

    def run(self):
        """Main transcription loop — runs in the worker thread."""
        if not _FW_AVAILABLE:
            self.error.emit(
                "faster-whisper not installed.\n"
                "Run: pip install faster-whisper"
            )
            return

        self._ensure_model()

        while self._running:
            item = self._queue.get()
            if item is None:
                break

            pcm_bytes, sample_rate = item
            if self._paused:
                continue

            try:
                text = self._transcribe(pcm_bytes, sample_rate)
                if text:
                    ts = datetime.now().strftime("%H:%M:%S")
                    self.transcript_ready.emit(text, ts)
            except Exception as ex:
                self.error.emit(f"Transcription error: {ex}")

    # ── Private helpers ────────────────────────────────────────────────────────

    def _ensure_model(self):
        if self._model is not None:
            return
        try:
            self._model = WhisperModel(
                self._model_name,
                device="cuda" if self._cuda_available() else "cpu",
                compute_type="float16" if self._cuda_available() else "int8",
            )
            self.model_loaded.emit()
        except Exception as ex:
            self.error.emit(f"Model load error ({self._model_name}): {ex}")
            self._model = None

    def _transcribe(self, pcm_bytes: bytes, sample_rate: int) -> str | None:
        """Transcribe a chunk and return cleaned text or None."""
        if self._model is None:
            self._ensure_model()
            if self._model is None:
                return None

        # Convert bytes → numpy float32
        audio = np.frombuffer(pcm_bytes, dtype=np.float32).copy()

        # Resample if needed (AudioCapture already targets 16k, but be safe)
        if sample_rate != SAMPLE_RATE:
            audio = self._resample(audio, sample_rate, SAMPLE_RATE)

        # Skip truly silent chunks only (very low threshold for loopback audio)
        # Teams/Zoom recordings can be very quiet — use an extremely low threshold
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms < 0.00005:
            logger.debug(f"Silent chunk skipped (RMS={rms:.8f})")
            return None

        # Normalize quiet audio (e.g. Teams recordings) so Whisper gets a usable signal
        peak = float(np.max(np.abs(audio)))
        if 0 < peak < 0.1:
            # Boost to ~50% amplitude, cap at 0.95 to avoid clipping
            gain = min(0.5 / peak, 20.0)
            audio = audio * gain
            audio = np.clip(audio, -0.95, 0.95)
            logger.info(f"Normalized quiet audio: peak={peak:.6f} → gain={gain:.1f}x")

        segments, _info = self._model.transcribe(
            audio,
            language="en",
            initial_prompt=INITIAL_PROMPT,
            vad_filter=False,          # disabled — loopback audio is always "active"
            beam_size=5,
            best_of=3,
            temperature=0.0,
            no_speech_threshold=0.4,   # lower = less likely to drop quiet speech (Teams/Zoom)
            condition_on_previous_text=False,
        )

        raw = " ".join(seg.text for seg in segments).strip()
        if not raw:
            return None

        corrected = _correct_terms(raw)
        if _is_noise(corrected):
            return None

        return corrected

    @staticmethod
    def _cuda_available() -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False

    @staticmethod
    def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
        """Simple linear-interpolation resample."""
        if orig_sr == target_sr:
            return audio
        ratio = target_sr / orig_sr
        n_new = int(len(audio) * ratio)
        x_old = np.linspace(0, 1, len(audio))
        x_new = np.linspace(0, 1, n_new)
        return np.interp(x_new, x_old, audio).astype(np.float32)


# ── Utility: expose helper so main_window can import it ───────────────────────
def lookslike_question(text: str) -> bool:
    return _lookslike_question(text)
