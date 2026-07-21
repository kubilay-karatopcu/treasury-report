-- Dev SQLite kaynağı — BDDK mevduat maliyeti (sektör).
-- Prod ile aynı kolon sözleşmesi (bkz. queries/prod/BDDK_MALIYET.sql).
-- Tablo seed_dev_db.py::_seed_bddk_maliyet ile sentetik üretilir (dev placeholder).
SELECT
    BANKA_TIPI,
    TARIH,
    EOMONTH,
    CCY_CODE,
    DATA_TIPI,
    BAKIYE_TL
FROM BDDK_MALIYET
WHERE TARIH >= '2025-12-31'
ORDER BY TARIH, BANKA_TIPI, CCY_CODE, DATA_TIPI
