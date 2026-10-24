# NeuroVision (Gemini Live)

Paletli ev robotu için kompakt sesli ve görsel asistan. Pi 4'te geliştirildi, Pi Zero 2W hedefleniyor.

- **Ses:** INMP441 I²S mikrofon (16 kHz) → Gemini Live → 3,5 mm jak (24 kHz)
- **Görme:** Pi kamera → `look` aracı ile modele tek kare
- **Uyku modu yok:** yerel VAD sadece konuşmayı gönderir, sessizlikte internete ses gitmez
- **Yarı çift yönlü:** robot konuşurken mikrofon kapalı (AEC gelene kadar)

Kaynak kullanımı (Pi 4): ~%5 CPU, ~90 MB RAM.

## Kurulum
```bash
cd ~/projects/neurovision-updated
uv venv --python /usr/bin/python3 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env && chmod 600 .env && nano .env   # GEMINI_API_KEY
```
ALSA cihazları (`mic`, `speaker`) `~/.asoundrc` dosyasında tanımlı. Mikrofon için `config.txt` içinde `dtoverlay=adau7002-simple,card-name=inmp441` gerekir.

## Çalıştırma
```bash
.venv/bin/python robot.py --check   # donanım testi: mikrofon, hoparlör, kamera
.venv/bin/python robot.py           # elle çalıştır (Ctrl+C ile durur)
```

## Servis (açılışta otomatik başlar)
```bash
bash deploy/install-service.sh
systemctl --user status neurovision     # durum
systemctl --user restart neurovision    # yeniden başlat
systemctl --user stop neurovision       # durdur
tail -f logs/robot.log                  # canlı log
```

## Yapı
| Dosya | Görevi |
|---|---|
| `robot.py` | Başlatma, log, `--check` |
| `config.toml` | Model, ses, kişilik, VAD, kamera ayarları |
| `nv/audio.py` | Mikrofon, hoparlör, mikrofon kazancı |
| `nv/vad.py` | Yerel konuşma kapısı |
| `nv/camera.py` | `rpicam-vid` MJPEG → son kare |
| `nv/live.py` | Gemini Live oturumu, yeniden bağlanma |
| `nv/tools.py` | Modelin çağırdığı araçlar (`look`) |

Yeni araç eklemek için `nv/tools.py` içindeki `DECLARATIONS` listesine bir tanım, `handle()` fonksiyonuna da bir dal ekle.
