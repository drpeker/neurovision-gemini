"""Palet motorlari (L298N). Guvenlik kurallari burada, modelin kararina birakilmaz.

- her hareket en fazla max_seconds; yeni komut eskisini iptal eder
- yon degisirken once dur, kisa bekle
- yumusak kalkis (ramp_start'tan hedefe ramp_up_ms) ve yumusak durus (ramp_down_ms);
  stop komutu, baglanti kopmasi ve cikista ise ANINDA durur
- sicak ya da dusuk voltajda hiz dusurulur
- motor calisirken (ve durduktan sonra mute_tail_ms) mikrofon susar: motor sesi modele gitmez
"""

import asyncio
import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

# yon -> (sol, sag) palet yonu
MOVES = {"ileri": (1, 1), "geri": (-1, -1), "sol": (-1, 1), "sag": (1, -1)}
SPEEDS = {"yavaş": 0.8, "normal": 1.0, "hızlı": 1.25}
RAMP_STEP_S = 0.025  # rampada guc guncelleme araligi


class Motors:
    def __init__(self, cfg: dict):
        import lgpio
        self._lg = lgpio
        self._cfg = cfg
        self._h = lgpio.gpiochip_open(0)
        self._sides = {"sol": cfg["left"], "sag": cfg["right"]}
        for side in self._sides.values():
            for key in ("in1", "in2", "en"):
                lgpio.gpio_claim_output(self._h, side[key], 0)
        self._task: asyncio.Task | None = None
        self._prev_dir: tuple[int, int] = (0, 0)  # son hareketin yonu (durunca sifirlanmaz)
        self._stopped_at = 0.0
        self._moving_until = 0.0
        self._quiet_until = 0.0
        log.info("motorlar hazir (sol A, sag B%s)", " ters" if cfg["right"]["invert"] else "")

    @property
    def busy(self) -> bool:
        """Motor calisiyor ya da yeni durdu: mikrofon bu surede susar."""
        return time.monotonic() < max(self._moving_until, self._quiet_until)

    # --- dusuk seviye ---
    def _drive(self, side: dict, direction: int, duty: float) -> None:
        if direction == 0 or duty <= 0:
            # tx_pwm(..., 0) arka planda calismaya devam edip sonraki yazmalari eziyor: pini birak
            self._lg.gpio_free(self._h, side["en"])
            self._lg.gpio_claim_output(self._h, side["en"], 0)
            self._lg.gpio_write(self._h, side["in1"], 0)
            self._lg.gpio_write(self._h, side["in2"], 0)
            return
        if side["invert"]:
            direction = -direction
        self._lg.gpio_write(self._h, side["in1"], 1 if direction > 0 else 0)
        self._lg.gpio_write(self._h, side["in2"], 0 if direction > 0 else 1)
        self._lg.tx_pwm(self._h, side["en"], self._cfg["pwm_hz"], min(100.0, duty))

    def _set(self, left: int, right: int, duty: float) -> None:
        self._drive(self._sides["sol"], left, duty)
        self._drive(self._sides["sag"], right, duty)

    def halt(self) -> None:
        """Senkron, aninda dur (cikis, hata, baglanti kopmasi)."""
        self._set(0, 0, 0)
        self._stopped_at = time.monotonic()
        self._moving_until = 0.0
        self._quiet_until = self._stopped_at + self._cfg["mute_tail_ms"] / 1000

    # --- hiz siniri ---
    def _derate(self) -> float:
        """Sicaklik ya da dusuk voltaj varsa hizi dusur."""
        try:
            temp = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
            raw = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True,
                                 timeout=1).stdout
            undervolt = int(raw.split("=")[-1], 16) & 1 if "=" in raw else 0
        except (OSError, ValueError, subprocess.SubprocessError):
            return 1.0
        if temp >= self._cfg["hot_c"] or undervolt:
            log.warning("motor hizi dusuruldu (isi %.1f C, dusuk voltaj %s)", temp, bool(undervolt))
            return self._cfg["derate"]
        return 1.0

    # --- disari acilan ---
    async def _cancel(self) -> None:
        # Eski gorev tamamen bitsin (finally'deki halt) ki yeni hareketin durumunu ezmesin
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def move(self, direction: str, seconds: float, speed: str = "normal") -> dict:
        """Hareketi baslatir ve hemen doner; onceki hareket once durdurulur."""
        if direction not in MOVES:
            return {"error": f"bilinmeyen yön: {direction}"}
        c = self._cfg
        # Cok kisa hareket rampada kaybolur, palet yerde kalkmaz: en az min_seconds
        seconds = max(c["min_seconds"], min(float(seconds), c["max_seconds"]))
        base = c["turn_speed"] if direction in ("sol", "sag") else c["drive_speed"]
        duty = min(c["max_speed"], base * SPEEDS.get(speed, 1.0)) * self._derate()
        duty = max(duty, c["min_speed"])
        await self._cancel()
        self._moving_until = time.monotonic() + seconds + 0.5
        self._task = asyncio.create_task(self._run(MOVES[direction], seconds, duty), name="motor")
        log.info("hareket: %s %.1f sn %%%.0f", direction, seconds, duty)
        return {"result": f"{direction} yönünde {seconds:.1f} saniye hareket ediliyor."}

    async def _run(self, dirs: tuple[int, int], seconds: float, duty: float) -> None:
        c = self._cfg
        try:
            # Yon degisiyorsa ve az once durduysa, motor/disli icin kisa bekle
            pause = c["reverse_pause_ms"] / 1000 - (time.monotonic() - self._stopped_at)
            if self._prev_dir not in ((0, 0), dirs) and pause > 0:
                await asyncio.sleep(pause)
            self._prev_dir = dirs
            up, down = c["ramp_up_ms"] / 1000, c["ramp_down_ms"] / 1000
            # Rampalar surenin en fazla %60'i; kalan surede guc hedefte kalsin
            if up + down > seconds * 0.6:
                k = seconds * 0.6 / (up + down)
                up, down = up * k, down * k
            await self._ramp(dirs, c["ramp_start"], duty, up)
            await asyncio.sleep(seconds - up - down)
            await self._ramp(dirs, duty, c["ramp_start"], down)
        finally:
            self.halt()

    async def _ramp(self, dirs: tuple[int, int], start: float, end: float, seconds: float) -> None:
        step = RAMP_STEP_S
        n = max(1, int(seconds / step))
        for i in range(1, n + 1):
            self._set(*dirs, start + (end - start) * i / n)
            await asyncio.sleep(step)

    async def stop(self) -> dict:
        await self._cancel()
        self.halt()
        log.info("motorlar durduruldu")
        return {"result": "Durdum."}

    def close(self) -> None:
        self.halt()
        self._lg.gpiochip_close(self._h)
