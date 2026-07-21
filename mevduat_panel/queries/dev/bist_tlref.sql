-- Dev SQLite kaynağı — BIST TLREF O/N repo faizi + bileşik endeks.
-- Prod ile aynı kolon sözleşmesi (bkz. queries/prod/bist_tlref.sql).
-- Tablo seed_dev_db.py::_seed_bist_tlref ile sentetik üretilir (dev
-- placeholder; gerçek TLREF DEĞİL). ASOFDATE ISO string ('YYYY-MM-DD').
SELECT
    ASOFDATE,
    INDEX_VALUE,
    RATE
FROM bist_tlref
WHERE ASOFDATE >= '2025-01-01'
ORDER BY ASOFDATE
