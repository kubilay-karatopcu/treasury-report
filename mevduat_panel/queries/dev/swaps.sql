-- Dev SQLite source for the Tenor Analysis hedge overlay.
-- Prod ile aynı sözleşme: yalnızca "HEDGE UZUN VADELİ TRY MEVDUAT" amaçlı
-- işlemleri çeker (query sadece filtre atar; bucketleme + yaşayan-işlem seçimi
-- Python'da — bkz. SwapHedgeEngine). Dev tablosu seed_dev_db.py::_seed_swaps
-- tarafından sentetik üretilir (gerçek swap DEĞİL, dev placeholder).
SELECT
    REFERANSNO,
    VALORTARIHI,
    VADETARIHI,
    ALINANMIKTAR,
    ALINANDOVIZCINSI,
    VERILENMIKTAR,
    VERILENDOVIZCINSI,
    ALISFAIZORANI,
    SATISFAIZORANI,
    ISLEMAMACI
FROM swaps
WHERE ISLEMAMACI = 'HEDGE UZUN VADELİ TRY MEVDUAT'
