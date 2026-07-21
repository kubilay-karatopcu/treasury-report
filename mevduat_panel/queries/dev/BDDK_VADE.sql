-- Dev SQLite kaynağı — BDDK vade kırılımı (sektör).
-- Prod ile aynı kolon sözleşmesi (bkz. queries/prod/BDDK_VADE.sql).
-- Tablo seed_dev_db.py::_seed_bddk_vade ile sentetik üretilir (dev placeholder).
SELECT
    BANKA_TIPI,
    TARIH,
    CCY_CODE,
    DATA_TIPI,
    VADE_KIRILIM,
    CUST_INFO,
    BAKIYE_TL
FROM BDDK_VADE
WHERE TARIH >= '2025-09-30'
ORDER BY TARIH, BANKA_TIPI, CCY_CODE, DATA_TIPI
