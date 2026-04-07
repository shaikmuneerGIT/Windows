"""
audio_capture.py
================
Captures system audio (WASAPI loopback) or microphone on Windows.

Libraries used:
  - PyAudioWPatch  → WASAPI loopback  (pip install PyAudioWPatch)
  - sounddevice    → device listing + mic fallback
  - numpy          → audio processing
"""

from __future__ import annotations

import threading
import time
import logging
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
SAMPLE_RATE   = 16_000          # Whisper expects 16 kHz mono
CHUNK_SECONDS = 3
CHUNK_FRAMES  = SAMPLE_RATE * CHUNK_SECONDS


class AudioCapture(QObject):
    """
    Captures system audio or microphone audio, emits PCM chunks.

    Signals
    -------
    chunk_ready(bytes, int)        Raw float32 PCM + sample rate
    device_list_ready(list)        [(id, label, is_loopback), ...]
    error(str)
    """

    chunk_ready       = pyqtSignal(bytes, int)
    device_list_ready = pyqtSignal(list)
    audio_level       = pyqtSignal(float)   # RMS level for diagnostics
    error             = pyqtSignal(str)

    SOURCE_SYSTEM = 0
    SOURCE_MIC    = 1
    SOURCE_BOTH   = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source      = self.SOURCE_SYSTEM
        self._device_id   = None
        self._stop_event  = threading.Event()
        self._pause_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def enumerate_devices(self) -> list:
        """List audio devices using sounddevice (safe, no WASAPI crash)."""
        devices = []
        try:
            import sounddevice as sd
            all_devs  = sd.query_devices()
            host_apis = sd.query_hostapis()
            default_out = sd.default.device[1]

            for idx, dev in enumerate(all_devs):
                name     = dev["name"]
                has_out  = dev["max_output_channels"] > 0
                has_in   = dev["max_input_channels"]  > 0
                api_name = host_apis[dev["hostapi"]]["name"] if dev["hostapi"] < len(host_apis) else ""
                is_def   = (idx == default_out)

                if has_out:
                    label = f"🖥 {name}" + (" ✓" if is_def else "")
                    devices.append((str(idx), label, True))
                elif has_in:
                    devices.append((str(idx), f"🎙 {name}", False))

        except Exception as ex:
            self.error.emit(f"Device scan: {ex}")

        if not devices:
            devices = [
                ("default_out", "🖥 Default System Audio", True),
                ("default_mic", "🎙 Default Microphone",   False),
            ]

        self.device_list_ready.emit(devices)
        return devices

    def set_source(self, source: int):
        self._source = source

    def set_device(self, device_id: str | None):
        self._device_id = device_id

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._pause_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self):
        self._pause_event.set()

    def resume(self):
        self._pause_event.clear()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=4)
        self._thread = None

    # ── Capture router ─────────────────────────────────────────────────────────

    def _run(self):
        if self._source == self.SOURCE_MIC:
            self._capture_mic()
        elif self._source == self.SOURCE_BOTH:
            t_sys = threading.Thread(target=self._capture_system, daemon=True)
            t_mic = threading.Thread(target=self._capture_mic,    daemon=True)
            t_sys.start()
            t_mic.start()
            t_sys.join()
            t_mic.join()
        else:
            self._capture_system()

    # ── System audio via PyAudioWPatch (WASAPI loopback) ───────────────────────

    def _capture_system(self):
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            self.error.emit(
                "PyAudioWPatch not installed.\n"
                "Run:  pip install PyAudioWPatch\n"
                "Falling back to microphone."
            )
            self._capture_mic()
            return

        pa = pyaudio.PyAudio()
        try:
            wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)

            # Determine which output device to loop back from
            # Priority: 1) user-selected device  2) Windows default output
            target_name = None
            if self._device_id and self._device_id not in ("default_out", "default"):
                try:
                    import sounddevice as sd
                    idx = int(self._device_id)
                    target_name = sd.query_devices(idx)["name"].lower()
                except Exception:
                    target_name = None

            if target_name is None:
                # Fall back to Windows default output
                default_out = pa.get_device_info_by_index(
                    wasapi_info["defaultOutputDevice"]
                )
                target_name = default_out["name"].lower()

            # Find matching loopback device
            loopback_dev = None
            all_loopbacks = []
            try:
                for lb in pa.get_loopback_device_info_generator():
                    all_loopbacks.append(lb)
                    logger.info(f"  Loopback device: [{lb['index']}] {lb['name']}")
                    lb_name = lb["name"].lower()
                    # Match if either name contains the other
                    if target_name in lb_name or lb_name in target_name:
                        loopback_dev = lb
                        break
                    # Partial word match (e.g. "jabra" in name)
                    for word in target_name.split():
                        if len(word) > 3 and word in lb_name:
                            loopback_dev = lb
                            break
                    if loopback_dev:
                        break
            except Exception:
                pass

            # Last resort: first available loopback
            if loopback_dev is None and all_loopbacks:
                loopback_dev = all_loopbacks[0]

            if loopback_dev:
                logger.info(f"  Selected loopback: [{loopback_dev['index']}] {loopback_dev['name']}")
            else:
                logger.warning("  No loopback device matched!")

            if loopback_dev is None:
                self.error.emit(
                    "No WASAPI loopback device found.\n"
                    "Go to Windows Sound Settings → Recording tab\n"
                    "→ right-click empty area → Show Disabled Devices\n"
                    "→ Enable 'Stereo Mix', then restart the app."
                )
                return

            dev_rate = int(loopback_dev["defaultSampleRate"])
            channels = loopback_dev["maxInputChannels"]

            stream = pa.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=dev_rate,
                input=True,
                input_device_index=loopback_dev["index"],
                frames_per_buffer=1024,
            )

            accumulated = np.zeros(0, dtype=np.float32)

            while not self._stop_event.is_set():
                if self._pause_event.is_set():
                    time.sleep(0.05)
                    continue

                raw  = stream.read(1024, exception_on_overflow=False)
                data = np.frombuffer(raw, dtype=np.float32).copy()

                # Mix to mono
                if channels > 1:
                    data = data.reshape(-1, channels).mean(axis=1)

                # Resample to 16 kHz if device runs at different rate
                if dev_rate != SAMPLE_RATE:
                    data = _resample(data, dev_rate, SAMPLE_RATE)

                accumulated = np.concatenate([accumulated, data])
                if len(accumulated) >= CHUNK_FRAMES:
                    chunk       = accumulated[:CHUNK_FRAMES]
                    accumulated = accumulated[CHUNK_FRAMES:]
                    # Emit audio level for UI diagnostics
                    rms = float(np.sqrt(np.mean(chunk ** 2)))
                    self.audio_level.emit(rms)
                    self.chunk_ready.emit(chunk.tobytes(), SAMPLE_RATE)

            stream.stop_stream()
            stream.close()

        except Exception as ex:
            self.error.emit(f"System audio error: {ex}")
        finally:
            pa.terminate()

    # ── Microphone via sounddevice ─────────────────────────────────────────────

    def _capture_mic(self):
        try:
            import sounddevice as sd
        except ImportError:
            self.error.emit("sounddevice not installed. Run: pip install sounddevice")
            return

        device_idx = None
        if self._device_id and self._device_id.lstrip("-").isdigit():
            try:
                idx = int(self._device_id)
                dev = sd.query_devices(idx)
                if dev["max_input_channels"] > 0:
                    device_idx = idx
            except Exception:
                pass

        try:
            accumulated = np.zeros(0, dtype=np.float32)
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=device_idx,
                blocksize=1024,
            ) as stream:
                while not self._stop_event.is_set():
                    if self._pause_event.is_set():
                        time.sleep(0.05)
                        continue
                    data, _ = stream.read(1024)
                    accumulated = np.concatenate([accumulated, data.flatten()])
                    if len(accumulated) >= CHUNK_FRAMES:
                        chunk       = accumulated[:CHUNK_FRAMES]
                        accumulated = accumulated[CHUNK_FRAMES:]
                        self.chunk_ready.emit(chunk.tobytes(), SAMPLE_RATE)
        except Exception as ex:
            self.error.emit(f"Microphone error: {ex}")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return audio
    n_new = int(len(audio) * target_sr / orig_sr)
    x_old = np.linspace(0, 1, len(audio))
    x_new = np.linspace(0, 1, n_new)
    return np.interp(x_new, x_old, audio).astype(np.float32)
