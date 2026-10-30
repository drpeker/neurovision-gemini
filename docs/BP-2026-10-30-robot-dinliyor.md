# Breakpoint: bp-2026-10-30-robot-dinliyor

`bp-2026-10-30-seri-hareket` üstüne eklenenler. Robot artık yürürken konuşmayı algılayıp duruyor.

## Çalışan durum (sesli testte doğrulandı)
- Yürürken ve dönerken sesli durdurma: üç hareket kullanıcının "dur" sözüyle kesildi.
- Algılama 120–900 Hz bandına bakar: konuşma motor üstünde 15–22 dB yükselir, sessiz harekette en fazla 4 dB.
- Hareket başladıktan 0.8 sn sonra dinler, herhangi bir konuşma durdurur, sonra AI'a bildirir.
- Bana gel: look, turn_to, look, distance, move, kontrol akışı çalışıyor.
- Beni bulup takip et: AI görmezse 45° adımlarla arıyor, bulunca takibi başlatıyor.
- Kişi takibi: sonarla yaklaşıyor, kaybedince arıyor, 60 cm hedefli.

## Değişenler (`17e81f4` sonrası)
- `nv/vad.py`: `MotionBarge` bant enerjisine geçti.
- `nv/motors.py`: `jolting`, kalkış ve duruş sarsıntısı çarpma sayılmaz.
- `nv/senses.py`: sarsıntı eşiği `jolting`e göre.
- Yönerge: 10 cm'den küçük farkı düzeltme.
- `barge_db` ayarı kaldırıldı.

## Doğrulanmayanlar
- Düz giderken takılma (`stall_a = 1.0` tahmin).
- Yeni eşiklerde gerçek çarpma.
- Gerçek pil boşalmasında uyarılar ve kapanış, tam şarj döngüsü.

## Bilinen sorunlar
- Robot konuşurken seni duymaz.
- Hareketin ilk 0.8 sn'si dinlenmez.
- Durdurmayı tetikleyen söz çoğunlukla modele ulaşmaz; TV gibi başka sesler de durdurabilir.
- Sonar dar bakar, yandan gelen engeli görmez.
- `look` sırasında konuşunca uzun sessizlik görülebilir, kökü bulunmadı.
- AI nadiren dil değiştirebilir ("I am listening").
