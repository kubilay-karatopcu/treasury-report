-- Source for the Deposit Detail tab (Realized NII).
-- Must return columns: MONTH, PRODUCT, DAYS, BALANCE, INTEREST_RATE, CUSTOMER_NUMBER,
-- SUB_PRODUCT, VADE_BUCKET, KALAN_VADE_BUCKET.
-- INTEREST_RATE is expected in percent form (e.g. 4.318 = 4.318%).
-- CUSTOMER_NUMBER: prod'da ortalama günlük müşteri adedi; dev tablosunda bu kolon
-- YOK → bakiyeden deterministik sentetik adet türetilir (dev placeholder, gerçek
-- müşteri sayısı DEĞİL).
-- SUB_PRODUCT: prod'da O/N SPEC_ACCT_CODE'a göre KGH/BTH/Other O/N. Dev'de
-- SPEC_ACCT_CODE YOK; TYPE2 = PRODUCT'ın ilk token'ı. O/N ise deterministik
-- (PRODUCT uzunluğu % 3) bölünür (dev placeholder); değilse TYPE2 ile aynı.
-- TENOR_RATE / DTM_RATE: prod'da TENOR / DTM'den bakiye-ağırlıklı gelir; dev
-- tablosunda YOK → TENOR-DTM switch KPI'ları test edilebilsin diye PRODUCT
-- uzunluğundan deterministik türetilir (dev placeholder, gerçek vade DEĞİL).
-- VADE_BUCKET / KALAN_VADE_BUCKET: prod'da MATURITY_* / REMAINING_MTRTY_*'dan gelir;
-- dev tablosunda YOK → monthly Tenor Breakdown (Cost/Balance) + monthly Tenor
-- Analysis TENOR/DTM modu test edilebilsin diye PRODUCT uzunluğundan deterministik
-- türetilir (dev placeholder, gerçek vade DEĞİL). Bucket etiketleri daily_deposit.sql
-- ile AYNI evrenden seçildi → engine/outstanding_daily.py::OS_TENOR_TO_COMMON haritası
-- ikisini de kapsar (bilinmeyen etiket _require_mapped'te ValueError fırlatır).
-- NOT: dev'de satır ÇOĞALTMAZ (her PRODUCT tek vade + tek kalan bucket alır); prod'da
-- KALAN_VADE_BUCKET GROUP BY'a dahil olduğundan gerçek grain bölünmesi orada olur.
SELECT
    MONTH,
    PRODUCT,
    DAYS,
    BALANCE,
    INTEREST_RATE,
    30.0 + (LENGTH(PRODUCT) * 17) % 300 AS TENOR_RATE,
    5.0  + (LENGTH(PRODUCT) * 11) % 150 AS DTM_RATE,
    MAX(1, CAST(ABS(BALANCE) / 2000000.0 AS INTEGER)) AS CUSTOMER_NUMBER,
    CASE
        WHEN SUBSTR(PRODUCT, 1, INSTR(PRODUCT, '_') - 1) = 'O/N' THEN
            CASE LENGTH(PRODUCT) % 3 WHEN 0 THEN 'KGH' WHEN 1 THEN 'BTH' ELSE 'Other O/N' END
        ELSE SUBSTR(PRODUCT, 1, INSTR(PRODUCT, '_') - 1)
    END AS SUB_PRODUCT,
    CASE (LENGTH(PRODUCT) % 5)
        WHEN 0 THEN '4-31' WHEN 1 THEN '46-60' WHEN 2 THEN '92-149'
        WHEN 3 THEN '182-273' ELSE '366-725'
    END AS VADE_BUCKET,
    CASE ((LENGTH(PRODUCT) + 2) % 5)
        WHEN 0 THEN '1-3' WHEN 1 THEN '4-31' WHEN 2 THEN '32-45'
        WHEN 3 THEN '61-91' ELSE '92-149'
    END AS KALAN_VADE_BUCKET
FROM TRY_DEPOSIT_DETAIL
