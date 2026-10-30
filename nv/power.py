"""Batarya olcumu: INA219 (I2C-1, 0x40), 3S bataryanin artisinda, step-down'dan once.

Arka planda okunur. 128 ornek ortalamasi (~68 ms) motor PWM'inin (200 Hz) dalgalanmasini duzler.
Oran akim sayimiyla izlenir (capacity_mah'ten harcanan): voltaj motor cekerken duser, akim toplami
bundan etkilenmez. Baslangic dinlenme voltajindan; kalan kapasite dosyada saklanir, pil sarj
olmadiysa yeniden acilista oradan devam edilir. Boştayken oran blend_s surede voltaja yaklasir,
pil dolu voltajina cikinca %100 olur: sayimin zamanla biriken hatasi boylece temizlenir.

Seviye degisimleri modele olay olarak gider, ne yapacagina o karar verir. Yerel tek davranis,
voltaj critical_v altinda critical_hold_s kalinca temiz kapanmadir (AI ulasilamaz olabilir).
"""

import asyncio
import json
import logging
import struct
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG, SHUNT, BUS, POWER, CURRENT, CALIBRATION = range(6)
# 32 V aralik, +-320 mV sont, voltaj ve akim 128 ornek ortalamali, surekli olcum
CONFIG_WORD = 0x3FFF
CURRENT_LSB = 0.0001  # A
POWER_LSB = 20 * CURRENT_LSB  # W
BUS_LSB = 0.004  # V
FAILS_MAX = 5  # art arda bu kadar okuma hatasi: olcum yok sayilir
MAX_STEP_S = 5.0  # okuma bundan uzun aksarsa arada gecen sure sayilmaz
SAVE_S = 120.0  # kalan kapasite bu aralikla diske yazilir
AVG_S = 60.0  # kalan sure icin ortalama akimin zaman sabiti
CHARGED_V = 0.25  # boşta voltaj kayitlidan bu kadar farkliysa pil degisti ya da sarj oldu
LEVELS = ["iyi", "azalıyor", "düşük", "çok düşük"]  # goz: mavi, sari, turuncu, kirmizi
LEVEL_BACK = 3  # voltaj dalgalaninca seviye (goz rengi) gidip gelmesin: bu kadar % yukarida geri don


class Battery:
    def __init__(self, cfg: dict, capacity_mah: int | None = None):
        from smbus2 import SMBus
        self.capacity_mah = capacity_mah or cfg["capacity_mah"]  # AI kullanicidan ogrenip degistirebilir
        self._cfg = cfg
        self._addr = cfg["addr"]
        self._bus = SMBus(cfg["bus"])
        self._curve = sorted(cfg["curve"])
        self._state_file = Path(__file__).parent.parent / cfg["state_file"]
        self._write(CONFIG, CONFIG_WORD)
        self._write(CALIBRATION, int(0.04096 / (CURRENT_LSB * cfg["shunt_ohm"])))
        self.volts = self.amps = self.watts = 0.0
        self.percent: int | None = None
        self.charge: float | None = None  # kalan oran (%), akim sayimi
        self.avg_amps = 0.0
        self._read_at = 0.0
        self._saved_at = 0.0
        self.level = 0  # LEVELS indeksi
        self.peak_amps = 0.0  # acilistan beri en yuksek akim
        self.overflows = 0  # akim olcum araligini (~3.2 A) kac okumada asti
        self.ok = False
        self._fails = 0
        self._running = False

    def _write(self, reg: int, value: int) -> None:
        self._bus.write_i2c_block_data(self._addr, reg, list(struct.pack(">H", value)))

    def _word(self, reg: int, fmt: str = ">H") -> int:
        return struct.unpack(fmt, bytes(self._bus.read_i2c_block_data(self._addr, reg, 2)))[0]

    def _percent_of(self, volts: float) -> int:
        """Dinlenme voltajindan oran: egride iki nokta arasi dogrusal."""
        cell, curve = volts / self._cfg["cells"], self._curve
        if cell <= curve[0][0]:
            return 0
        for (v0, p0), (v1, p1) in zip(curve, curve[1:]):
            if cell <= v1:
                return round(p0 + (p1 - p0) * (cell - v0) / (v1 - v0))
        return 100

    def _update_level(self) -> None:
        bands = self._cfg["level_bands"]
        level = sum(self.percent < b for b in bands)
        if level > self.level or (level < self.level and self.percent >= bands[self.level - 1] + LEVEL_BACK):
            self.level = level

    def _load(self, volts_percent: int, idle: bool) -> float:
        """Ilk okuma: kayitli kapasite gecerliyse ondan, degilse dinlenme voltajindan basla."""
        try:
            saved = json.loads(self._state_file.read_text())
            if idle and abs(self.volts - saved["volts"]) < CHARGED_V:
                return float(saved["charge"])
        except (OSError, ValueError, KeyError):
            pass
        log.info("batarya orani voltajdan: %%%d", volts_percent)
        return float(volts_percent)

    def _save(self, now: float) -> None:
        self._saved_at = now
        tmp = self._state_file.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"charge": round(self.charge, 2), "volts": round(self.volts, 2)}))
            tmp.replace(self._state_file)
        except OSError as e:
            log.warning("batarya durumu yazilamadi: %s", e)

    def _count(self, now: float) -> None:
        c = self._cfg
        idle = abs(self.amps) < c["idle_a"]
        volts_percent = self._percent_of(self.volts)
        if self.charge is None:
            self.charge = self._load(volts_percent, idle)
            self.avg_amps = self.amps
        else:
            dt = now - self._read_at
            if dt <= MAX_STEP_S:
                self.charge -= self.amps * dt / 3.6 / self.capacity_mah * 100
                self.avg_amps += (self.amps - self.avg_amps) * dt / AVG_S
                if idle:
                    self.charge += (volts_percent - self.charge) * dt / c["blend_s"]
        if idle and self.volts >= c["full_v"]:
            self.charge = 100.0
        self.charge = max(0.0, min(100.0, self.charge))
        self._read_at = now
        self.percent = round(self.charge)
        self._update_level()
        if idle and now - self._saved_at > SAVE_S:
            self._save(now)

    def read(self) -> None:
        bus = self._word(BUS)
        self.volts = (bus >> 3) * BUS_LSB
        self.amps = self._word(CURRENT, ">h") * CURRENT_LSB
        self.watts = self._word(POWER) * POWER_LSB
        self.peak_amps = max(self.peak_amps, abs(self.amps))
        if bus & 1:
            self.overflows += 1
        self._count(time.monotonic())
        self.ok = True
        self._fails = 0

    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._loop, name="batarya", daemon=True).start()

    def stop(self) -> None:
        self._running = False
        if self.charge is not None and abs(self.amps) < self._cfg["idle_a"]:
            self._save(time.monotonic())

    def _loop(self) -> None:
        while self._running:
            try:
                self.read()
            except OSError:
                self._fails += 1
                if self._fails == FAILS_MAX:
                    self.ok = False
                    log.warning("batarya olcumu yanit vermiyor")
            time.sleep(self._cfg["poll_s"])

    def status(self) -> dict:
        if not self.ok:
            return {"durum": "ölçüm yok (INA219 yanıt vermiyor)"}
        left = self.charge / 100 * self.capacity_mah
        return {"oran_%": self.percent, "seviye": LEVELS[self.level], "kapasite_mAh": self.capacity_mah,
                "kalan_mAh": round(left), "kullanılan_mAh": round(self.capacity_mah - left),
                "ortalama_akım_A": round(self.avg_amps, 3),
                "voltaj_V": round(self.volts, 2),
                "hücre_V": round(self.volts / self._cfg["cells"], 2), "akım_A": round(self.amps, 3),
                "güç_W": round(self.watts, 2), "en_yüksek_akım_A": round(self.peak_amps, 2),
                "not": "Ölçümdür. Oran harcanan akımdan hesaplanır; akım anlık, ortalama son dakikanındır. "
                        "Kalan süreyi bunlardan sen hesapla."}


async def watch(battery: Battery, brain, cfg: dict, shutdown) -> None:
    """Seviye dusunce modele haber ver; voltaj kritik kalirsa temiz kapan."""
    level, low_since = battery.level, None
    while True:
        await asyncio.sleep(1)
        if not battery.ok:
            low_since = None
            continue
        now = time.monotonic()
        # 5 V alti: olcum hatasi, robot o voltajda zaten calismaz
        low_since = (low_since or now) if 5 < battery.volts < cfg["critical_v"] else None
        if low_since and now - low_since >= cfg["critical_hold_s"]:
            log.warning("batarya kritik: %.2f V, %.0f sn; kapaniliyor", battery.volts, now - low_since)
            await brain.power_down()
            shutdown()
            return
        if battery.level < level:
            level = battery.level
        elif battery.level > level and brain.body.connected_since is not None:
            level = battery.level
            await brain.notify(f"Batarya seviyen {LEVELS[level]}: %{battery.percent}, {battery.volts:.1f} V. "
                               "Kullanıcıya kısaca söyle; ne yapacağına sen karar ver.")
