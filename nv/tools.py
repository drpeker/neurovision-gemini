"""Modelin cagirabilecegi araclar. Yeni arac: DECLARATIONS'a ekle, handle()'a dal ekle.

Aciklamalar ve donen sonuclar modele gider: duzgun Turkce yazilir.
"""

import asyncio
import collections
import logging
import time

from google.genai import types

from .body import SECTIONS, Body
from .camera import Camera
from .display import EMOTIONS
from . import web
from .lang import looks_foreign
from .memory import CATEGORIES, Memory
from .vision import COCO80, COLORED, COLORS

log = logging.getLogger(__name__)


TURN_DPS = 60.0  # süreyle verilen dönüş <-> açı çevirisi (0.3 sn ~ 20 derece)
# Hareket sınırı: mikrofon motor dönerken kapalı, "dur" duyulmaz. Model move'u döngüye sokunca robot
# 81 sn durmadı (2026-10-30): kullanıcı konuşmadan MOVE_BUDGET_S, konuşsa da (gürültü de konuşma
# sayılabilir) MOVE_WINDOW_S içinde MOVE_WINDOW_MAX_S saniyeden fazla hareket yok
MOVE_BUDGET_S = 15.0
MOVE_WINDOW_S = 60.0
MOVE_WINDOW_MAX_S = 45.0
INTERRUPTED = "Kullanıcı konuştuğu için hareketi yarıda kesip durdun. Söylediğini dinle, kendiliğinden devam etme."
MOVE_REFUSED = ("Hareket sınırına ulaştın, durdun ve hareket yapılmadı. Yeniden move çağırma; "
                "kullanıcıya kısaca söyle ve yeni komutunu bekle.")


def _params(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


DECLARATIONS = [
    types.FunctionDeclaration(
        name="look",
        description="Kameradan şu anki görüntüyü al. Kullanıcı etrafta ne olduğunu ya da neyi "
                    "gördüğünü sorduğunda veya bir şeye bakman gerektiğinde kullan.",
    ),
    types.FunctionDeclaration(
        name="remember",
        description="Kullanıcı ya da ev hakkında ileride işe yarayacak kalıcı bir bilgiyi kaydet: "
                    "isim, tercih, aile, eşyaların yeri, alışkanlıklar. Geçici şeyleri kaydetme. "
                    "Sağlık, şifre, banka gibi hassas bilgileri kaydetme.",
        parameters_json_schema=_params({
            "text": {"type": "string",
                     "description": "Tek cümlelik Türkçe bilgi, örneğin 'Kullanıcının kızının adı Elif.'"},
            "category": {"type": "string", "enum": CATEGORIES},
            "importance": {"type": "integer", "description": "1 (önemsiz) ile 5 (çok önemli) arası"},
        }, ["text", "category"]),
    ),
    types.FunctionDeclaration(
        name="recall",
        description="Hafızada ara. Kullanıcı geçmişten bir şey sorduğunda ya da bir bilgiyi "
                    "hatırlaman gerektiğinde kullan (örneğin 'anahtarlarım nerede', 'dün ne konuştuk').",
        parameters_json_schema=_params({
            "query": {"type": "string", "description": "Aranacak anahtar kelimeler"},
        }, ["query"]),
    ),
    types.FunctionDeclaration(
        name="body_status",
        description="Kendi donanım durumunu ölç: güç/voltaj, işlemci ısısı, CPU, bellek, disk, "
                    "wifi ve bağlantı, GPIO pinleri, I2C'ye bağlı cihazlar, ortam ışığı, ortam ses "
                    "düzeyi, hoparlör ses düzeyi, motor hız düzeyi, kamera ve sistemler, Gemini token kullanımı (kota). Kullanıcı nasıl "
                    "olduğunu, ısını, pilini/gücünü, pinlerini, sensörlerini, ne kadar kota/token "
                    "harcadığını sorduğunda kullan. 'uyarılar' boş değilse söyle.",
        parameters_json_schema=_params({
            "section": {"type": "string", "enum": SECTIONS,
                        "description": "ozet: genel durum (varsayılan). Belirli bir soru için ilgili "
                                       "bölümü seç: gpio, i2c, guc, isi, cpu, ram, disk, ag, isik, ses, motor, "
                                       "gorus (nesne takibi ve son görülenler), "
                                       "sistemler, kota (token kullanımı); hepsi: tümü"},
        }, []),
    ),
    types.FunctionDeclaration(
        name="move",
        description="Paletlerle hareket et. Kullanıcı ileri/geri gitmeni ya da sağa/sola dönmeni "
                    "istediğinde kullan. Dönüşte (sol/sag) her zaman degrees ver: söylenen açı, 'biraz' 20, "
                    "'çeyrek' 90, 'yarım tur' 180, 'tam tur' 360; dönüş bitince gerçek açı döner. İleri/"
                    "geride seconds ver: 'hafifçe/biraz' kısa süre (0.3 sn). 'Yavaşça' speed=yavaş, "
                    "'çok yavaş' speed=çok yavaş demektir; ikisi birlikte de olabilir. Bir istek için tek "
                    "move çağır: araç hareket bitince döner, bitene kadar kullanıcıyı duyamazsın. "
                    "Hedefe varmak için tekrar tekrar çağırma: mesafeyi ölç, süreyi hesapla, tek hareketle "
                    "git; kullanıcı konuşmadan en fazla 15 saniye hareket edebilirsin.",
        parameters_json_schema=_params({
            "direction": {"type": "string", "enum": ["ileri", "geri", "sol", "sag"],
                          "description": "sol/sag: yerinde dönüş"},
            "degrees": {"type": "number", "description": "sol/sag için dönüş açısı, 1 ile 720 arası derece"},
            "seconds": {"type": "number", "description": "ileri/geri için 0.25 ile 15 arası saniye; hafifçe 0.3, normal 1"},
            "speed": {"type": "string", "enum": ["çok yavaş", "yavaş", "normal", "hızlı"]},
        }, ["direction"]),
    ),
    types.FunctionDeclaration(
        name="stop",
        description="Hareketi hemen durdur. Kullanıcı 'dur' dediğinde ya da bir engel/tehlike "
                    "gördüğünde kullan.",
    ),
    types.FunctionDeclaration(
        name="express",
        description="Yüzündeki ifadeyi ekranda birkaç saniye göster. Söylediğin şeye uygun bir "
                    "duygu olduğunda kullan (sevinç, şaşkınlık, üzüntü...). Her cümlede kullanma.",
        parameters_json_schema=_params({
            "emotion": {"type": "string", "enum": EMOTIONS},
        }, ["emotion"]),
    ),
    types.FunctionDeclaration(
        name="weather",
        description="Bir yerin hava durumunu öğren: şu anki durum ve günlük tahmin. Kullanıcı hava, "
                    "sıcaklık, yağmur, şemsiye gibi şeyler sorduğunda kullan.",
        parameters_json_schema=_params({
            "place": {"type": "string", "description": "Şehir ya da ilçe adı, örneğin 'İzmir'"},
            "days": {"type": "integer", "description": "Kaç günlük tahmin (1-7); bugün 1, yarın için 2"},
        }, ["place"]),
    ),
    types.FunctionDeclaration(
        name="wiki",
        description="Vikipedi'de ara: kişiler, yerler, tarih, bilim gibi ansiklopedik bilgiler. "
                    "Emin olmadığın bir bilgi sorulduğunda kullan. Güncel haber ve fiyat bulmaz.",
        parameters_json_schema=_params({
            "query": {"type": "string", "description": "Aranacak konu"},
        }, ["query"]),
    ),
    types.FunctionDeclaration(
        name="forget",
        description="Kullanıcı bir bilgiyi unutmanı istediğinde o bilgiyi hafızadan sil.",
        parameters_json_schema=_params({
            "query": {"type": "string", "description": "Silinecek bilgiyi tarif eden kelimeler"},
        }, ["query"]),
    ),
]


VOLUME_STEP = 15
# Yanlis duyulan (yabanci dil/gurultu) bir sozle yapilmamasi gereken isler
GUARDED = {"move", "remember", "forget", "volume", "shutdown", "motor_speed", "track", "turn_to",
           "battery_capacity"}


class Tools:
    def __init__(self, camera: Camera | None, memory: Memory, body: Body, speaker):
        self._camera = camera
        self._memory = memory
        self._body = body
        self._speaker = speaker
        self.face = None  # LiveBrain.attach_face
        self.motors = None  # LiveBrain.attach_motors
        self.radio = None  # LiveBrain.attach_radio
        self.heard = lambda: ""  # LiveBrain: kullanicinin su anki sozunun yazisi
        self.on_shutdown = None  # robot.py: on_shutdown("robot" | "sistem")
        self.tracker = None  # robot.py: nv.tracker.Tracker
        self.turner = None  # LiveBrain.attach_turner (nv.imu.AngleTurner, MPU6050)
        self._spent = 0.0  # son konuşmadan beri hareket süresi
        self.turn_only = False  # LiveBrain: ileri/geri kapalı (masada test)
        self.senses = None  # LiveBrain.attach_senses
        self.sonar = None  # LiveBrain.attach_sonar
        self.near_cm = 0.0  # ileri giderken engel bundan yakinsa arac hemen doner
        self._interrupted = False  # kullanıcı hareket sırasında konuştu
        self._turn_task: asyncio.Task | None = None
        self._moves: collections.deque = collections.deque()  # (bitiş zamanı, süre)

    def declarations(self) -> list[types.FunctionDeclaration]:
        """Sabit araclar + istasyon listesine gore radyo ve ses duzeyi."""
        decls = list(DECLARATIONS)
        decls.append(types.FunctionDeclaration(
            name="volume",
            description="Hoparlör ses düzeyini öğren ya da değiştir. Kullanıcı ses düzeyini sorduğunda "
                        "'sor'; sesini aç/kıs, daha yüksek/alçak konuş dediğinde değiştir.",
            parameters_json_schema=_params({
                "action": {"type": "string", "enum": ["sor", "artır", "azalt", "ayarla"]},
                "level": {"type": "integer", "description": "ayarla için 0-100 arası yüzde"},
            }, ["action"]),
        ))
        if self.sonar:
            decls.append(types.FunctionDeclaration(
                name="distance",
                description="Önündeki en yakın şeyin uzaklığını santimetre olarak ölç. Kullanıcıya "
                            "yaklaşırken, engel sorulduğunda ya da ne kadar gideceğini hesaplarken kullan.",
            ))
        if self._body.battery:
            decls.append(types.FunctionDeclaration(
                name="battery_capacity",
                description="Bataryanın kapasitesini öğren ya da kaydet. Kapasiteden emin değilsen "
                            "kullanıcıya sor, söyleyince ayarla ile kaydet; oran ve kalan mAh buna göre "
                            "hesaplanır.",
                parameters_json_schema=_params({
                    "action": {"type": "string", "enum": ["sor", "ayarla"]},
                    "mah": {"type": "integer", "description": "ayarla için kapasite (mAh)"},
                }, ["action"]),
            ))
        if self.motors:
            decls.append(types.FunctionDeclaration(
                name="motor_speed",
                description="Motor hız düzeyini öğren ya da değiştir; tüm hareketleri çarpar. Kullanıcı "
                            "'motor hızın kaç', 'daha yavaş/hızlı hareket et', 'motor hızını yüzde 50 "
                            "yap' dediğinde kullan. Tek bir hareketi yavaşlatmak için move'un speed "
                            "seçeneğini kullan, bunu değil.",
                parameters_json_schema=_params({
                    "action": {"type": "string", "enum": ["sor", "artır", "azalt", "ayarla"]},
                    "level": {"type": "integer", "description": "ayarla için 20-100 arası yüzde"},
                }, ["action"]),
            ))
        if self.tracker:
            decls.append(types.FunctionDeclaration(
                name="track",
                description="Bir nesneyi ya da kişiyi gözünle takip et: yerinde dönerek onu kadrajın "
                            "ortasında tutarsın (ileri gitmezsin). 'Beni takip et', 'bana dön', 'kediyi "
                            "izle' gibi isteklerde kullan; kullanıcının kendisi için 'person'. Hedefi "
                            "Türkçeden listedeki İngilizce ada çevir (kupa: cup, kase: bowl, şişe: bottle, "
                            "fare: mouse, telefon: cell phone, kumanda: remote). Renk söylendiyse color ile "
                            "ver ('turuncu kase': bowl + turuncu, 'siyah fare': mouse + siyah). Listede "
                            "olmayan ama rengi belli bir nesne için target='colored object' ve color ver "
                            "('kırmızı kutu': colored object + kırmızı); o renkteki en büyük bölge takip "
                            "edilir. Rengi de belli değilse look + turn_to kullan. Takip arka planda "
                            "sürer; 'dur' denince stop kullan.",
                parameters_json_schema=_params({
                    "target": {"type": "string", "enum": COCO80 + [COLORED]},
                    "color": {"type": "string", "enum": COLORS, "description": "İsteğe bağlı renk"},
                    "seconds": {"type": "number", "description": "Takip süresi; varsayılan 60, en fazla 300"},
                }, ["target"]),
            ))
            decls.append(types.FunctionDeclaration(
                name="turn_to",
                description="Az önce look ile gördüğün bir şeye dön. look sonucundaki x değerini ver: 0 en sol, "
                            "500 orta, 1000 en sağ. Kişiye de nesneye de dönmek için tek seferlik kullan.",
                parameters_json_schema=_params({
                    "x": {"type": "integer", "description": "0-1000 arası yatay konum"},
                }, ["x"]),
            ))
        if self.on_shutdown:
            decls.append(types.FunctionDeclaration(
                name="shutdown",
                description="Kendini kapat. Yalnızca kullanıcı açıkça 'sistemi kapat', 'kendini kapat' "
                            "ya da 'robotu kapat' dediğinde kullan; önce kısaca veda et. 'sistem': "
                            "Raspberry Pi tamamen kapanır (fişi çekmek güvenli olur); 'robot': yalnızca "
                            "robot programı kapanır, Pi açık kalır.",
                parameters_json_schema=_params({
                    "target": {"type": "string", "enum": ["sistem", "robot"]},
                }, ["target"]),
            ))
        if self.radio:
            decls.append(types.FunctionDeclaration(
                name="radio",
                description="İnternet radyosunu aç ya da kapat. İstasyonlar: "
                            + ", ".join(self.radio.stations) + ". Kullanıcı radyo, müzik açmanı "
                            "ya da kapatmanı istediğinde kullan.",
                parameters_json_schema=_params({
                    "action": {"type": "string", "enum": ["aç", "kapat", "liste"]},
                    "station": {"type": "string", "enum": list(self.radio.stations),
                                "description": "aç için istasyon"},
                }, ["action"]),
            ))
        return decls

    async def handle(self, call: types.FunctionCall) -> types.FunctionResponse:
        args = call.args or {}
        log.info("arac cagrisi: %s %s", call.name, args)
        heard = self.heard()
        if call.name in GUARDED and looks_foreign(heard):
            log.info("arac reddedildi, soz Turkce degil: %r", heard)
            return types.FunctionResponse(id=call.id, name=call.name, response={
                "error": "Kullanıcının son sözü anlaşılamadı (yabancı dil ya da gürültü), işlem "
                         "yapılmadı. Kısaca anlayamadığını söyle ve tekrar etmesini iste."})
        try:
            if call.name == "look":
                return await self._look(call)
            if call.name == "body_status":
                result = await self._body.status(args.get("section", "ozet"))
            elif call.name in ("weather", "wiki"):
                result = await getattr(web, call.name)(**args)
            elif call.name == "radio":
                result = await self._radio(**args)
            elif call.name == "shutdown":
                result = self._shutdown(**args)
            elif call.name == "motor_speed":
                result = self._motor_speed(**args)
            elif call.name in ("move", "stop"):
                if not self.motors:
                    result = {"error": "Motorlar bağlı değil."}
                elif call.name == "move":
                    tracking = self.tracker and self.tracker.active
                    if self.tracker:
                        await self.tracker.stop()  # elle hareket takibi bitirir
                    result = await self._move(**args)
                    if tracking:
                        # Model takibin surdugunu sanip "takipteyim" diyordu
                        result["takip"] = ("Bu hareket takibi bitirdi; takip artık kapalı. Kullanıcıya "
                                           "kısaca söyle. Takip ederken ileri yürüyemezsin.")
                else:
                    if self.tracker:
                        await self.tracker.stop()
                    result = await self.motors.stop()
            elif call.name == "track":
                result = await self.tracker.start(**args)
            elif call.name == "turn_to":
                result = await self._turn_to(**args)
            else:
                result = getattr(self, f"_{call.name}")(**args)
        except Exception as e:
            log.warning("arac hatasi %s: %s", call.name, e)
            result = {"error": f"Araç çalışmadı: {e}"}
        return types.FunctionResponse(id=call.id, name=call.name, response=result)

    async def _radio(self, action: str, station: str = "") -> dict:
        if not self.radio:
            return {"error": "Radyo yok."}
        if action == "kapat":
            return await self.radio.stop()
        if action == "aç" and station:
            return await self.radio.play(station)
        return self.radio.status()

    def _shutdown(self, target: str = "sistem") -> dict:
        if not self.on_shutdown:
            return {"error": "Kapatma bağlı değil."}
        # Veda cumlesi calinsin diye kapatma, konusma bitince yapilir
        asyncio.create_task(self._shutdown_after_speech(target), name="shutdown")
        what = "Raspberry Pi kapanıyor" if target == "sistem" else "Robot programı kapanıyor"
        return {"result": f"{what}; konuşman bitince kapanacak. Kısaca veda et."}

    async def _shutdown_after_speech(self, target: str) -> None:
        for _ in range(30):  # veda sesi baslasin (en fazla 3 sn)
            if self._speaker.busy:
                break
            await asyncio.sleep(0.1)
        for _ in range(150):  # bitsin (en fazla 15 sn)
            if not self._speaker.busy:
                break
            await asyncio.sleep(0.1)
        log.info("kapatma: %s", target)
        self.on_shutdown(target)

    async def _move(self, direction: str, seconds: float | None = None, speed: str = "normal",
                    degrees: float | None = None) -> dict:
        turning = direction in ("sol", "sag")
        if self.turn_only and not turning:
            log.warning("hareket reddedildi: yalniz donus acik (%s)", direction)
            return {"error": "Şu an yalnızca sağa sola dönebilirsin; ileri geri gitmek kapalı. Kullanıcıya söyle."}
        budget = self._budget()
        if budget < 0.25:
            log.warning("hareket reddedildi: sinir doldu (%s)", direction)
            return {"error": MOVE_REFUSED}
        self._interrupted = False
        started = time.monotonic()
        if self.senses:
            self.senses.claimed = True
        try:
            if turning and self.turner:
                # Jiroskopla; biten dönüş sonucu modele döner, sıradaki hareket bunu kesmez
                degrees = min(720.0, abs(degrees or (seconds or 1.0) * TURN_DPS))
                left = await self._turn(degrees if direction == "sol" else -degrees)
                if left is None:
                    return {"result": INTERRUPTED}
                if self.turner.stalled:
                    return {"error": "Motorlara güç gitti ama robot dönmedi (sıkışma ya da düşük batarya)."}
                result = {"result": f"{direction} yönünde {degrees - abs(left):.0f} derece dönüldü. Hareket bitti."}
                event = self.senses.take() if self.senses else None
                return {**result, "olay": event} if event else result
            if turning and degrees and seconds is None:
                seconds = abs(degrees) / TURN_DPS  # jiroskop yok: açı süreye çevrilir
            seconds = min(seconds or 1.0, budget)
            result = await self.motors.move(direction, seconds, speed)
            if "error" in result:
                return result
            peak = 0.0
            battery = self._body.battery
            sonar = self.sonar if direction == "ileri" else None
            ahead = sonar.distance if sonar else None
            warn = sonar and (ahead is None or ahead >= self.near_cm)  # zaten yakınsa model biliyor
            while self.motors.moving:
                ahead = sonar.distance if sonar else None
                if warn and ahead is not None and ahead < self.near_cm:
                    return {"result": "İleri giderken önüne bir şey geldi; motor hâlâ çalışıyor, durmak "
                                      "istiyorsan hemen stop çağır.", "olay": f"Önünde {ahead:.0f} cm'de engel var."}
                peak = max(peak, battery.amps) if battery else 0.0
                event = self.senses.take() if self.senses else None
                if event:  # karar modelin: motor durmadı, durdurmak için stop
                    return {"result": f"{direction} hareketi sürerken olay oldu; motor hâlâ çalışıyor, "
                                      "durmak istiyorsan hemen stop çağır.", "olay": event}
                await asyncio.sleep(0.02)
            log.info("hareket bitti: %s %.1f sn, tepe akim %.2f A", direction, seconds, peak)
            if self._interrupted:
                return {"result": INTERRUPTED}
            result = {"result": f"{direction} yönünde {seconds:.1f} saniye gidildi. Hareket bitti."}
            if self.sonar and self.sonar.distance is not None:
                result["öndeki_mesafe_cm"] = round(self.sonar.distance)
            return result
        except asyncio.CancelledError:
            await self.motors.stop(quiet=True)
            raise
        finally:
            if self.senses:
                self.senses.claimed = False
            self._spend(time.monotonic() - started)

    async def _turn(self, degrees: float) -> float | None:
        """Jiroskopla dön; kalan hata, kullanıcı araya girdiyse None."""
        self._turn_task = asyncio.create_task(self.turner.turn(degrees), name="turn")
        try:
            return await self._turn_task
        except asyncio.CancelledError:
            if self._interrupted:
                return None
            raise

    def disable_motion(self) -> None:
        """Batarya kritik: bundan sonra hareket yok."""
        self.motors = self.turner = self.tracker = None

    async def interrupt(self) -> None:
        """Kullanıcı hareket sırasında konuştu: her şeyi durdur, sözünü dinle."""
        self._interrupted = True
        if self.tracker and self.tracker.active:
            await self.tracker.stop()
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        await self.motors.stop(quiet=True)
        self.motors.listen_now()

    def new_speech(self) -> None:
        """Kullanıcı konuştu: hareket bütçesi yenilenir."""
        self._spent = 0.0

    def _budget(self) -> float:
        now = time.monotonic()
        while self._moves and now - self._moves[0][0] > MOVE_WINDOW_S:
            self._moves.popleft()
        return min(MOVE_BUDGET_S - self._spent, MOVE_WINDOW_MAX_S - sum(s for _, s in self._moves))

    def _spend(self, seconds: float) -> None:
        self._spent += seconds
        self._moves.append((time.monotonic(), seconds))

    async def _turn_to(self, x: int) -> dict:
        await self.tracker.stop()
        if self._budget() < 0.25:
            log.warning("donus reddedildi: sinir doldu")
            return {"error": MOVE_REFUSED}
        e = max(0, min(1000, int(x))) / 1000 - 0.5
        if abs(e) <= self.tracker._cfg["center_tolerance"]:
            return {"result": "Nesne zaten tam önünde."}
        started = time.monotonic()
        if self.turner:
            self._interrupted = False
            await self._turn(-e * self.tracker._cfg["hfov_deg"])
        else:
            await self.tracker.turn_toward(e)
        self._spend(time.monotonic() - started)
        return {"result": f"{'Sola' if e < 0 else 'Sağa'} döndüm; emin olmak için tekrar look ile bakabilirsin."}

    async def _seen(self, jpeg: bytes) -> dict:
        """Nesne tanıma ölçümü: görüntüdeki gözle tahminden kesin; turn_to bu x değerini alır."""
        if not self.tracker:
            return {}
        found = await self.tracker.detect(jpeg)
        return {"görülenler": [{"ad": d.name, "x": round(d.cx * 1000), "güven": round(d.conf, 2)} for d in found[:8]],
                "not": "x: 0 en sol, 500 orta, 1000 en sağ."}

    def _distance(self) -> dict:
        if not self.sonar:
            return {"error": "Mesafe sensörü yok."}
        d = self.sonar.distance
        if d is None:
            return {"öndeki_mesafe_cm": None, "not": f"{self.sonar.max_cm} cm içinde yankı yok."}
        return {"öndeki_mesafe_cm": round(d)}

    def _battery_capacity(self, action: str, mah: int | None = None) -> dict:
        battery = self._body.battery
        if not battery:
            return {"error": "Batarya ölçümü yok."}
        if action == "ayarla" and mah and mah > 0:
            battery.capacity_mah = int(mah)
            self._memory.set("battery_capacity_mah", str(battery.capacity_mah))
        return {"kapasite_mAh": battery.capacity_mah}

    def _motor_speed(self, action: str, level: int | None = None) -> dict:
        if not self.motors:
            return {"error": "Motorlar bağlı değil."}
        if action == "ayarla" and level is not None:
            self.motors.speed_level = level
        elif action in ("artır", "azalt"):
            self.motors.speed_level += 10 if action == "artır" else -10
        if action != "sor":
            self._memory.set("motor_speed", str(self.motors.speed_level))
        return self.motors.status()

    def _volume(self, action: str, level: int | None = None) -> dict:
        v = self._speaker.volume
        if action == "sor":
            return {"ses_düzeyi_%": v}
        if action == "ayarla" and level is not None:
            v = level
        elif action in ("artır", "azalt"):
            v += VOLUME_STEP if action == "artır" else -VOLUME_STEP
        self._speaker.volume = max(10, v)  # tamamen susarsa robotu duyamazsin
        self._memory.set("volume", str(self._speaker.volume))
        return {"ses_düzeyi_%": self._speaker.volume}

    def _express(self, emotion: str) -> dict:
        if not self.face:
            return {"result": "Ekran yok."}
        self.face.set_emotion(emotion)
        return {"result": f"Yüzünde '{emotion}' ifadesi gösteriliyor."}

    async def _look(self, call: types.FunctionCall) -> types.FunctionResponse:
        if self.face:
            self.face.flash()
        jpeg = self._camera.frame() if self._camera else None
        if jpeg and self.face:
            self.face.preview(jpeg)
        if not jpeg:
            return types.FunctionResponse(id=call.id, name=call.name,
                                          response={"error": "Kamera görüntüsü alınamadı."})
        return types.FunctionResponse(
            id=call.id, name=call.name,
            response={"result": "Kameradan alınan güncel görüntü ekte.", **await self._seen(jpeg)},
            parts=[types.FunctionResponsePart(
                inline_data=types.FunctionResponseBlob(mime_type="image/jpeg", data=jpeg))],
        )

    def _remember(self, text: str, category: str = "diğer", importance: int = 3) -> dict:
        fid = self._memory.remember(text, category, importance)
        return {"result": "Kaydedildi." if fid else "Bu bilgi zaten kayıtlı."}

    def _recall(self, query: str) -> dict:
        facts = [t for _, _, t in self._memory.recall(query)]
        turns = [f"{time.strftime('%d.%m %H:%M', time.localtime(ts))} "
                 f"{'kullanıcı' if r == 'user' else 'sen'}: {t}"
                 for ts, r, t in self._memory.search_turns(query)]
        if not facts and not turns:
            return {"result": "Hafızada bununla ilgili bir şey yok."}
        return {"bilgiler": facts, "gecmis_konusmalar": turns}

    def _forget(self, query: str) -> dict:
        removed = self._memory.forget(query)
        return {"silinenler": removed} if removed else {"result": "Eşleşen bilgi bulunamadı."}
