"""Mikrofon ve hoparlor: ham 16-bit PCM, ornekleme donusumu ALSA plug'da."""

import asyncio
import logging
import math
import subprocess
import threading
import time
from array import array

import sounddevice as sd

log = logging.getLogger(__name__)

MIC_RATE = 16000      # Gemini Live girisi
SPEAKER_RATE = 24000  # Gemini Live cikisi
FRAME_MS = 20
MIC_FRAME_BYTES = MIC_RATE * FRAME_MS // 1000 * 2


def set_mic_boost(db: int, card: str) -> None:
    """~/.asoundrc softvol kontrolu: -10 dB = 0, 1 dB adim; acilista sifirlandigi icin her seferinde."""
    step = max(0, min(40, db + 10))
    r = subprocess.run(["amixer", "-q", "-c", card, "sset", "Mic Boost", str(step)],
                       capture_output=True, text=True)
    if r.returncode:
        log.warning("mic kazanci ayarlanamadi: %s", r.stderr.strip())
    else:
        log.info("mic kazanci: %+d dB", db)


class HighPass:
    """Birinci derece DC engelleyici / yuksek geciren filtre.

    INMP441 buyuk bir DC ve <2 Hz kayma uretiyor (ham -26 dBFS, temizlenince -40):
    hem enerji olcumunu hem VAD'i hem de modele giden sesi bozuyor.
    """

    def __init__(self, cutoff_hz: float = 80.0):
        self._r = 1.0 - 2 * math.pi * cutoff_hz / MIC_RATE
        self._x = 0.0
        self._y = 0.0

    def __call__(self, pcm: bytes) -> bytes:
        samples = array("h", pcm)
        r, x1, y1 = self._r, self._x, self._y
        for i, x in enumerate(samples):
            y1 = x - x1 + r * y1
            x1 = x
            samples[i] = max(-32768, min(32767, int(y1)))
        self._x, self._y = x1, y1
        return samples.tobytes()


class Mic:
    """Filtrelenmis 20 ms'lik PCM karelerini asyncio kuyruguna yazar."""

    def __init__(self, device: str, highpass_hz: float = 80.0):
        self._device = device
        self._stream = None
        self._filter = HighPass(highpass_hz)
        self.frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)

    def start(self) -> None:
        loop = asyncio.get_running_loop()

        def push(data: bytes) -> None:
            if self.frames.full():
                self.frames.get_nowait()
            self.frames.put_nowait(data)

        def callback(indata, frames, time_info, status):
            if status:
                log.debug("mic: %s", status)
            loop.call_soon_threadsafe(push, self._filter(bytes(indata)))

        self._stream = sd.RawInputStream(
            device=self._device, samplerate=MIC_RATE, channels=1, dtype="int16",
            blocksize=MIC_FRAME_BYTES // 2, callback=callback,
        )
        self._stream.start()
        log.info("mikrofon acildi: %s %d Hz", self._device, MIC_RATE)

    def stop(self) -> None:
        if self._stream:
            self._stream.close()
            self._stream = None


class Speaker:
    """Gelen PCM parcalarini tamponlayip calar; konusurken 'busy' doner."""

    def __init__(self, device: str, echo_tail_ms: int):
        self._device = device
        self._tail = echo_tail_ms / 1000
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._last_sound = 0.0
        self._stream = None

    def start(self) -> None:
        def callback(outdata, frames, time_info, status):
            n = len(outdata)
            with self._lock:
                chunk = self._buf[:n]
                del self._buf[:n]
            if chunk:
                self._last_sound = time.monotonic()
            outdata[:len(chunk)] = chunk
            outdata[len(chunk):] = b"\x00" * (n - len(chunk))

        self._stream = sd.RawOutputStream(
            device=self._device, samplerate=SPEAKER_RATE, channels=1, dtype="int16",
            blocksize=SPEAKER_RATE * FRAME_MS // 1000, latency="high", callback=callback,
        )
        self._stream.start()
        log.info("hoparlor acildi: %s %d Hz", self._device, SPEAKER_RATE)

    def play(self, pcm: bytes) -> None:
        with self._lock:
            self._buf.extend(pcm)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    @property
    def busy(self) -> bool:
        with self._lock:
            if self._buf:
                return True
        return time.monotonic() - self._last_sound < self._tail

    def stop(self) -> None:
        if self._stream:
            self._stream.close()
            self._stream = None
