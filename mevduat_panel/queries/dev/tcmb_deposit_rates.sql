-- Dev SQLite kaynağı — TCMB ağırlıklı ortalama mevduat faizleri (sektör).
-- Prod ile aynı kolon sözleşmesi (bkz. queries/prod/tcmb_deposit_rates.sql).
-- Tablo seed_dev_db.py::_seed_tcmb_deposit_rates ile sentetik üretilir (dev
-- placeholder; gerçek TCMB faizi DEĞİL). TCMB_DATE ISO string ('YYYY-MM-DD').
SELECT
    CUR,
    TCMB_DATE,
    TIP,
    ORT_FAIZ
FROM tcmb_deposit_rates
WHERE TCMB_DATE >= '2025-01-01'
ORDER BY TCMB_DATE, CUR, TIP
