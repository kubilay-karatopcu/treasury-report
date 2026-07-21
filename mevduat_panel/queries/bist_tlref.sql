-- BIST TLREF — O/N repo referans faizi + bileşik endeks.
-- Kaynak: bist_tlref (BIST TLREF günlük yayınının yedeği).
-- Site açılışında ham çekilir; engine/sector_data.py cache'ler.
--
-- Kolonlar (aynen):
--   ASOFDATE     DATE     yayın günü
--   INDEX_VALUE  NUMBER   TLREF bileşik endeksi — bir başlangıç noktasından
--                         itibaren her gün O/N TLREF ile compound edilerek
--                         büyür; iki tarih arası işleyen faiz endeks oranından
--                         bulunur: INDEX(t1)/INDEX(t0) - 1
--   RATE         NUMBER   O/N TLREF (repo) faizi, yüzde puan (39.50 = %39.50)
--
-- 2025-01-01'den itibaren tüm günlük gözlemler.
SELECT
    ASOFDATE,
    INDEX_VALUE,
    RATE
FROM A16438.bist_tlref
WHERE ASOFDATE >= TO_DATE('01/01/2025', 'dd/mm/yyyy')
ORDER BY ASOFDATE
