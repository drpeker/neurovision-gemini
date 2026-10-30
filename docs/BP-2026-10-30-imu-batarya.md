# Breakpoint: bp-2026-10-30-imu-batarya

NeuroVision online, Raspberry Pi Zero 2 W (`neuroeye.local`), Gemini Live.
Depo: `~/projects/neurovision-updated`, dal `imu-angle`. Servis: `systemctl --user neurovision.service`.
Çalışan ayar repo dışında: `~/.config/neurovision/zero-gecici.toml`; bu noktadaki kopyası `deploy/zero-gecici.toml`.

## Bu noktada robotun durumu

### Hareket
- Dönüşler MPU6050 jiroskopuyla açılı (`nv/imu.py`). Yer testinde ±30/60/90/180° hata en fazla 0.67°.
- İleri ve geri süreyle gider, jiroskop düz çizgiyi korur. Mesafe ölçülmez.
- `turn_to` ve takip jiroskop kullanır. Hedef kaybolunca jiroskopla arama yapılır (`sweep_step_deg = 25`).
- Kalibrasyon: `sign = 1`, `scale = 1.0125` (kamerayla ölçüldü), `hfov_deg = 56`, `coast_s` yön başına öğrenilir.
- `move` hareket bitince yanıt verir. Tek hareket en fazla 6 sn; konuşmadan 6 sn, 60 sn'de 20 sn sınırı var.
- Masa modu kapalı (`turn_only` yok). Konuşunca durma kapalı (`barge_db = 0`).

### Batarya
- INA219 `0x40`, 3S pilin artısında, step-down'lardan önce. Pi ve 6 V motorlar ayrı step-down'dan beslenir.
- Yapılandırma: config `0x3FFF`, kalibrasyon 4096, 0.25 sn okuma. Şönt `R100` varsayıldı (doğrulanmadı).
- Oran akım sayımıyla hesaplanır (varsayılan 2500 mAh), `data/battery.json` içinde saklanır.
- Boşta oran yavaşça voltaja yaklaşır, 12.45 V üstünde %100 olur.
- Boşta tüketim 0.144 A, 1.75 W (12.16 V).
- Yüzde eğrisi genel LiPo tablosu, bu pil için ölçülmedi.

### AI ve hafıza
- Kontrol AI'da. Seviye olayları Gemini'ye gider, ne yapacağına o karar verir.
- `body_status` anlık akım, voltaj, güç, ortalama akım, kalan mAh ve kapasiteyi verir.
- Kalan süreyi AI kendisi hesaplar. Kapasiteyi `battery_capacity` aracıyla sorabilir ve kaydedebilir.
- Yanlış hafıza kayıtları silindi: 24, 26, 28, 29 ve 1923, 1927 numaralı konuşma satırları.
  Yedek: `data/memory-pre-silme.db`.

### Yerel tek davranış
Voltaj 30 sn kesintisiz 10.5 V altında kalırsa hareket durur, veda istenir, Pi temiz kapanır.

### Ekran
- Sol üst: Wi-Fi simgesi. Yeşil Gemini bağlı, sarı Wi-Fi var Gemini yok, kırmızı çarpı Wi-Fi yok.
- Sağ üst: yüzde ve pil simgesi. Köşe payı 9 piksel.
- Gözler batarya seviyesine göre mavi, sarı, turuncu, kırmızı. Yüksek ısıda turuncu.

### Batarya uyarıları
| Eşik | Davranış |
|---|---|
| %35, %20, %10 altı | Gemini'ye bir kez olay gider, sözlü bildirir |
| 10.5 V, 30 sn | Yerel kapanış sekansı |

## 2227349'dan sonra değişenler
- `nv/power.py` yeni: INA219 sürücüsü, akım sayımı, seviye, kritik kapanış izleyicisi.
- `nv/display.py`: Wi-Fi ve pil simgesi, batarya göz rengi, yeşil noktalar kaldırıldı.
- `nv/body.py`: `batarya` bölümü.
- `nv/tools.py`: `battery_capacity`, hareket bütçesi, bekleyen `move`, `turn_only`.
- `nv/live.py`: kapanış sekansı, oturum açılışında batarya bilgisi, konuşma kesme bağlantısı.
- `nv/motors.py`: temiz kapanış, `listen_now`.
- `nv/vad.py`: `MotionBarge` (şu an kapalı).
- `config.toml`, `deploy/zero-gecici.toml`, `requirements.txt`.

## Bilinen sorunlar
1. Hareket sırasında "dur" duyulmuyor: mikrofon kapalı ve `move` Gemini mesaj döngüsünü bekletiyor.
2. Konuşma algılama zayıf. Dil kodu `tr-TR` sabit değil, ekran metni ile AI'ın anladığı farklı olabiliyor.
3. Metne bakan yabancı dil koruması doğru komutları reddedebiliyor.
4. Cümle başları kesilebiliyor. 48 → 16 kHz dönüşüm kalitesi ölçülmedi.
5. Takip bildirimleri AI'ı sık konuşturuyor, o sırada mikrofon kapalı kalıyor.
6. Bağlantı koparsa takip görevi durmuyor.
7. Motorların tek sahibi yok. Yeni AI yönetimli mimari kurulmadı.
8. Zero'da sonar yok. "Bana gel" için mesafe ölçümü yok.
9. `max_speed = 70` notu eski (2S). 6 V hatta motor yaklaşık 4.2 V alır.

## Test edilmeyenler
- Gerçek pil boşalmasında kritik kapanış ve yüzde uyarıları.
- Gerçek şarj sonrası %100'e dönüş ve tam boşalma döngüsü.
- Hareket altında kalkış akımı ve 3.2 A sınırı.
- Yürüyen kişi takibi, sesli "dur".
- Yeni AI batarya yönergesinin sesli testi.

## Geri dönüş
```bash
ssh drpeker@neuroeye.local
cd ~/projects/neurovision-updated
git checkout bp-2026-10-30-imu-batarya
cp deploy/zero-gecici.toml ~/.config/neurovision/zero-gecici.toml
systemctl --user restart neurovision.service
```
Tam yedek Mac'te: `backup/bp-2026-10-30-imu-batarya/`. İlk sistem yedeği: `backup/zero-gemini-2026-10-30/`.
Yedeklerde API anahtarı ve kişisel hafıza var; paylaşma.
