"""Pi kamera: rpicam-vid MJPEG akisindan son JPEG kareyi tutar (ek kutuphane yok)."""

import asyncio
import logging
import shutil
import time

log = logging.getLogger(__name__)

SOI, EOI = b"\xff\xd8", b"\xff\xd9"


class Camera:
    def __init__(self, width: int, height: int, fps: int, rotation: int = 0):
        self._args = [
            "-t", "0", "-n", "--codec", "mjpeg", "--quality", "70",
            "--width", str(width), "--height", str(height),
            "--framerate", str(fps), "--rotation", str(rotation), "--flush", "-o", "-",
        ]
        self._proc = None
        self._task = None
        self.latest: bytes | None = None
        self.latest_at = 0.0

    async def start(self) -> None:
        exe = shutil.which("rpicam-vid")
        if not exe:
            log.warning("rpicam-vid bulunamadi, kamera kapali")
            return
        self._proc = await asyncio.create_subprocess_exec(
            exe, *self._args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        self._task = asyncio.create_task(self._read(), name="camera")
        log.info("kamera acildi: %s", " ".join(self._args[7:13]))

    async def _read(self) -> None:
        buf = bytearray()
        while True:
            chunk = await self._proc.stdout.read(65536)
            if not chunk:
                log.warning("kamera akisi kapandi")
                return
            buf.extend(chunk)
            # Tampondaki en son tam JPEG'i al, gerisini at
            end = buf.rfind(EOI)
            if end < 0:
                continue
            start = buf.rfind(SOI, 0, end)
            if start >= 0:
                self.latest = bytes(buf[start:end + 2])
                self.latest_at = time.monotonic()
            del buf[:end + 2]

    def frame(self, max_age: float = 3.0) -> bytes | None:
        if self.latest and time.monotonic() - self.latest_at <= max_age:
            return self.latest
        return None

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            await self._proc.wait()
