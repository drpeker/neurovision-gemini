# Breakpoint: bp-2026-10-30-sonar-gel

Bir önceki kayıt `bp-2026-10-30-imu-batarya` üstüne eklenenler. Geri dönüş adımları aynıdır, etiket adı değişir.

## Çalışan durum
- "Bana gel" sesli testte çalıştı: AI `look`, `distance` çağırdı, süreyi hesaplayıp tek `move` ile yürüdü,
  35 cm yakın engel olayında kendi kararıyla `stop` çağırdı.
- HC-SR04 bağlı: TRIG GPIO4, ECHO GPIO17. Otomatik durdurmaz, AI'a ölçüm verir.
- `look` sonucu YOLO konumlarını (`görülenler`, `x`) içerir; AI `turn_to` ile döner.
- Beden algıları (`nv/senses.py`): çarpma 1.2 g, eğim 20°, kaldırılma, zorlanma 1.0 A. Karar AI'da.
- Hareket sınırı: tek hareket 15 sn, konuşmadan 15 sn, 60 sn'de 45 sn.
- Gerçek hız (2 sn sürüş): yavaş 6.5, normal 8, hızlı 13 cm/sn.
- Yerde akım: ileri 0.30–0.38 A, dönüş 0.52 A. Sarsıntı en fazla 0.81 g.

## Gerçek testte doğrulananlar
Kaldırılma, eğim, çarpma (2.0 g), dönüş takılması, "bana gel", `turn_to` (sapma 0.5° altı).

## Doğrulanmayanlar ve açık sorunlar
1. Düz giderken takılma (`stall_a = 1.0` tahmin).
2. 22:48:12'de takip sırasında "motor dönmüyor" alarmı: temas mı, halıda zayıf sağ dönüş mü bilinmiyor.
3. AI son kontrolü atlıyor ve bazen olmayan şeyler söylüyor.
4. Takip her olayda AI'ı konuşturuyor.
5. `look` sırasında konuşunca 20–30 sn sessizlik.
6. Konuşma algılama: dil kodu sabit değil, yabancı dil koruması komut reddedebilir.
7. Hareket sırasında "dur" duyulmuyor; otomatik engel durması yok.
8. Sonar tek okumada sapabilir (162 → 217 cm).
