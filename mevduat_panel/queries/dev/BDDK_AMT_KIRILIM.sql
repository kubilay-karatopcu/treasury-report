-- Dev SQLite kaynağı — BDDK müşteri-tipi × AUM kırılımı (sektör).
-- Prod ile aynı kolon sözleşmesi (bkz. queries/prod/BDDK_AMT_KIRILIM.sql).
-- Tablo seed_dev_db.py::_seed_bddk_amt_kirilim ile sentetik üretilir (gerçek
-- BDDK verisi DEĞİL; dev placeholder). TARIH ISO string ('YYYY-MM-DD').
SELECT
    BANKA_TIPI,
    TARIH,
    CCY_CODE,
    DATA_TIPI,
    CUST_TIP,
    BAKIYE_KIRILIM,
    CUST_INFO,
    BAKIYE_TL
FROM BDDK_AMT_KIRILIM
WHERE TARIH >= '2025-12-31'
ORDER BY TARIH, BANKA_TIPI, CCY_CODE, DATA_TIPI
