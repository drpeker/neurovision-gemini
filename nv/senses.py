"""Beden algisi: MPU6050 ivme ve jiroskoptan carpma, egim, tasinma; INA219 akimindan zorlanma.

Burada karar verilmez: olaylar modele gider, ne yapacagina o karar verir. Hareket araci calisirken
olay araca birakilir (model o sirada arac sonucunu bekliyor), yoksa bildirim olarak gider.
Esikler 6 V olcumunden (2026-10-30): bosta sarsinti 0.008 g, duz giderken 0.87 g, donerken 2.1 g.
"""

import asyncio
import collections
import logging
import math
import threading
import time

log = logging.getLogger(__name__)

TILT_LP_S = 0.3  # egim icin ivme ortalamasi: titresim egim sanilmasin
TILT_CLEAR = 0.6  # egim olayi tilt_deg'in bu oraninin altinda biter
STALL_HOLD_S = 0.6


class Senses:
    def __init__(self, cfg: dict, motors, battery):
        self._cfg = cfg
        self._motors, self._battery = motors, battery
        self._ref = cfg["gravity"]
        self._ref_g = math.sqrt(sum(v * v for v in self._ref))
        self._lp = list(self._ref)
        self._prev = None
        self._lock = threading.Lock()
        self._events: collections.deque = collections.deque(maxlen=8)
        self._last_event: dict[str, float] = {}
        self.tilt = 0.0  # derece
        self.tilted = False
        self.handled = False  # elle tasiniyor ya da kaldirildi
        self._handled_since = self._still_since = None
        self._stall_since = None
        self.claimed = False  # hareket araci calisiyor: olaylari o alir
        self._task: asyncio.Task | None = None

    def _event(self, kind: str, text: str, now: float) -> None:
        if now - self._last_event.get(kind, -1e9) < self._cfg["repeat_s"]:
            return
        self._last_event[kind] = now
        log.info("algi: %s", text)
        with self._lock:
            self._events.append(text)

    def take(self) -> str | None:
        with self._lock:
            return self._events.popleft() if self._events else None

    def feed(self, now: float, ax: float, ay: float, az: float, gx: float, gy: float) -> None:
        """Jiroskop is parcacigindan, ~200 Hz."""
        c, a = self._cfg, (ax, ay, az)
        if self._prev:
            jerk = max(abs(p - v) for p, v in zip(self._prev, a))
            if jerk >= (c["turn_bump_g"] if self._motors.turning else c["bump_g"]):
                self._event("carpma", f"Çarpma algılandı ({jerk:.1f} g).", now)
        self._prev = a
        k = min(1.0, 0.005 / TILT_LP_S)
        self._lp = [p + (v - p) * k for p, v in zip(self._lp, a)]
        dot = sum(p * r for p, r in zip(self._lp, self._ref))
        size = math.sqrt(sum(p * p for p in self._lp)) * self._ref_g or 1.0
        self.tilt = math.degrees(math.acos(max(-1.0, min(1.0, dot / size))))
        if not self.tilted and self.tilt >= c["tilt_deg"]:
            self.tilted = True
            self._event("egim", f"Robot {self.tilt:.0f} derece eğildi.", now)
        elif self.tilted and self.tilt < c["tilt_deg"] * TILT_CLEAR:
            self.tilted = False
        self._handling(now, a, gx, gy)

    def _handling(self, now: float, a: tuple, gx: float, gy: float) -> None:
        c = self._cfg
        if self._motors.busy:  # motor sarsintisi tasinma sayilmasin
            self._handled_since = None
            return
        g = math.sqrt(sum(v * v for v in a))
        moved = max(abs(gx), abs(gy)) >= c["handle_dps"] or abs(g - self._ref_g) >= c["handle_g"]
        if moved:
            self._still_since = None
            self._handled_since = self._handled_since or now
            if not self.handled and now - self._handled_since >= c["handle_s"]:
                self.handled = True
                self._event("tasinma", "Robot kaldırıldı ya da elle taşınıyor.", now)
            return
        self._handled_since = None
        if self.handled:
            self._still_since = self._still_since or now
            if now - self._still_since >= c["still_s"]:
                self.handled = False

    def _stall(self, now: float) -> None:
        if self._motors.moving and self._battery and self._battery.amps >= self._cfg["stall_a"]:
            self._stall_since = self._stall_since or now
            if now - self._stall_since >= STALL_HOLD_S:
                self._event("zorlanma", f"Motorlar zorlanıyor, {self._battery.amps:.1f} A çekiyor; "
                                        "takılmış olabilirsin.", now)
        else:
            self._stall_since = None

    async def _watch(self, notify) -> None:
        while True:
            await asyncio.sleep(0.1)
            self._stall(time.monotonic())
            while not self.claimed and (text := self.take()):
                await notify(f"{text} Ne yapacağına sen karar ver.")

    def start(self, notify) -> None:
        self._task = asyncio.create_task(self._watch(notify), name="algi")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def status(self) -> dict:
        return {"eğim_derece": round(self.tilt, 1), "elle_taşınıyor": self.handled}
