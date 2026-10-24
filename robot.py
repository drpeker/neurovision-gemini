"""NeuroVision robot: Gemini Live ile sesli ve gorsel sohbet.

    .venv/bin/python robot.py            # robotu calistir
    .venv/bin/python robot.py --check    # donanim testi (API anahtari gerekmez)
"""

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sys
import tomllib
from pathlib import Path

from nv.audio import Mic, Speaker, set_mic_boost
from nv.camera import Camera
from nv.vad import SpeechGate

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("robot")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def setup_logging(cfg: dict) -> None:
    path = ROOT / cfg["file"]
    path.parent.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    file_h = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3)
    console_h = logging.StreamHandler(sys.stdout)
    for h in (file_h, console_h):
        h.setFormatter(fmt)
    logging.basicConfig(level=cfg["level"], handlers=[file_h, console_h])
    for noisy in ("websockets", "httpx", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def make_camera(cfg: dict) -> Camera | None:
    c = cfg["camera"]
    if not c["enabled"]:
        return None
    return Camera(c["width"], c["height"], c["fps"], c["rotation"])


async def check(cfg: dict) -> None:
    """3 sn kaydet, kac karede konusma oldugunu say, hoparlorden geri cal, kamera karesi al."""
    a, v = cfg["audio"], cfg["vad"]
    mic, speaker = Mic(a["mic"]), Speaker(a["speaker"], a["echo_tail_ms"])
    gate = SpeechGate(v["aggressiveness"], v["preroll_ms"], v["hangover_ms"])
    camera = make_camera(cfg)
    if camera:
        await camera.start()
    mic.start()
    print("3 saniye konus...")
    frames, voiced = [], 0
    while len(frames) < 150:
        f = await mic.frames.get()
        frames.append(f)
        voiced += gate._vad.is_speech(f, 16000)
    mic.stop()
    peak = max(abs(int.from_bytes(f[i:i + 2], "little", signed=True))
               for f in frames for i in range(0, len(f), 2))
    print(f"mikrofon: {len(frames)} kare, konusma {voiced} kare, tepe {peak / 32768:.2f}")

    # 16 kHz -> 24 kHz: her 2 ornegi 3'e cikar (sadece test icin kaba donusum)
    pcm16 = b"".join(frames)
    out = bytearray()
    for i in range(0, len(pcm16) - 3, 4):
        s0, s1 = pcm16[i:i + 2], pcm16[i + 2:i + 4]
        out += s0 + s0 + s1
    speaker.start()
    print("kayit geri calaniyor...")
    speaker.play(bytes(out))
    while speaker.busy:
        await asyncio.sleep(0.1)
    speaker.stop()

    if camera:
        await asyncio.sleep(1.5)
        jpeg = camera.frame()
        if jpeg:
            (ROOT / "logs").mkdir(exist_ok=True)
            (ROOT / "logs/check.jpg").write_bytes(jpeg)
            print(f"kamera: {len(jpeg)} bayt JPEG -> logs/check.jpg")
        else:
            print("kamera: kare alinamadi")
        await camera.stop()


async def run(cfg: dict) -> None:
    from nv.live import LiveBrain
    from nv.memory import Memory

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit(f"GEMINI_API_KEY yok: {ROOT / '.env'} dosyasina ekle (.env.example'a bak)")

    memory = Memory(ROOT / cfg["memory"]["db"])
    log.info("hafiza: %s", memory.count())
    a = cfg["audio"]
    mic, speaker = Mic(a["mic"]), Speaker(a["speaker"], a["echo_tail_ms"])
    camera = make_camera(cfg)
    if camera:
        await camera.start()
    speaker.start()
    mic.start()
    brain = LiveBrain(cfg, key, mic, speaker, camera, memory)

    task = asyncio.create_task(brain.run(), name="brain")
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    log.info("NeuroVision hazir. Durdurmak icin Ctrl+C.")
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        mic.stop()
        speaker.stop()
        if camera:
            await camera.stop()
        memory.close()
        log.info("kapatildi")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="donanim testi")
    p.add_argument("--config", default=str(ROOT / "config.toml"))
    args = p.parse_args()

    cfg = tomllib.loads(Path(args.config).read_text())
    cfg["debug"]["dir"] = str(ROOT / cfg["debug"]["dir"])
    load_env(ROOT / ".env")
    setup_logging(cfg["log"])
    set_mic_boost(cfg["audio"]["mic_boost_db"])
    asyncio.run(check(cfg) if args.check else run(cfg))


if __name__ == "__main__":
    main()
