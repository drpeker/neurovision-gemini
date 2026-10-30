# Breakpoint: bp-2026-10-30-seri-hareket

`bp-2026-10-30-sonar-gel` üstüne eklenenler. Motor regülatörü 5 V'tan 6 V'a çıkarıldı, ayarlar buna göre yapıldı.

## Durum
- Hız: normal ~24 cm/sn, yavaş ~18, hızlı ~30 (yavaş ve hızlı yaklaşık).
- Dönüş: `duty_fast 70`, `duty 50`, 90° ~1 sn, 180° ~1.4 sn, hata en fazla 1.7°.
- Rampa: 300/200 ms. `max_speed 90`.
- Engel refleksi: ileri giderken 25 cm'de anında durur, engel varken ileri gitmez (`stop_cm`).
- Olay gelirse robot önce durur, sonra AI karar verir.
- Çarpma: düz 1.5 g, dönüş 3 g (`turning` kalkışta hemen geçerli).
- Konuşunca durma açık: `barge_db -15`, 7 dB yükselme, 80 ms, ilk 800 ms dinlenmez.
- Dil `tr-TR`, yazıya bakan yabancı dil koruması kaldırıldı.
- Takip: ara bildirimler sessiz, sonarla kişi takibi açık (`follow_cm 60`), hata olursa AI'a bildirilir.
- "Bana gel" sesli testte tam akışla çalıştı: look, turn_to, look, distance, move, look, distance, move, look.

## Doğrulanmayanlar
- Hareket sırasında sesli "dur" (`barge`).
- Yürüyen kişi takibi (`follow_*`), kişiyle ileri sürüş.
- Düz giderken takılma (`stall_a = 1.0` tahmin).
- Gerçek pil boşalmasında uyarılar ve kapanış.
- Yeni çarpma eşiklerinde gerçek çarpma.

## Bilinen sorunlar
- Sonar dar bakar, yandan gelen engeli görmez.
- Hız değerleri zemin ve çevreye göre değişir; AI varınca yeniden ölçer.
- Robot kendi konuşurken seni duymaz.
- `look` sırasında konuşunca uzun sessizlik görüldü, kökü bulunmadı.
