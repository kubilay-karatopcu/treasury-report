-- Source for the Daily Deposit Detail tab (Realized NII).
-- Must return columns: DAT, GRUP_KEY, TYPE2, CUST_TP, AUM_TYPE, SEGMENT,
-- VADE_BUCKET, KALAN_VADE_BUCKET, GUNLUK_TRY_BAKIYE, AGIRLIKLI_ORT_FAIZ,
-- AGIRLIKLI_ORT_TENOR, AGIRLIKLI_ORT_DTM, CUSTOMER_NUMBER, SUB_PRODUCT.
-- AGIRLIKLI_ORT_FAIZ is expected in percent form (e.g. 4.485 = 4.485%).
-- CUSTOMER_NUMBER: prod'da DEPOSITUSAGE_NEW.CUSTOMER_NUMBER toplamı; dev tablosunda
-- bu kolon YOK → pipeline/heatmap test edilebilsin diye bakiyeden deterministik
-- sentetik adet türetilir (gerçek müşteri sayısı DEĞİL, yalnız dev placeholder).
-- SUB_PRODUCT: prod'da O/N SPEC_ACCT_CODE'a göre KGH/BTH/Other O/N. Dev'de
-- SPEC_ACCT_CODE YOK → O/N satırları deterministik (GRUP_KEY uzunluğu % 3) bölünür
-- (dev placeholder; O/N değilse TYPE2 ile aynı).
-- VADE_BUCKET / KALAN_VADE_BUCKET / AGIRLIKLI_ORT_TENOR / AGIRLIKLI_ORT_DTM:
-- prod'da MATURITY_* / REMAINING_MTRTY_* / TENOR / DTM kolonlarından gelir; dev
-- tablosunda bunlar YOK → Tenor Analysis + TENOR-DTM switch test edilebilsin
-- diye GRUP_KEY uzunluğundan deterministik türetilir (dev placeholder). Bucket
-- etiketleri engine/outstanding_daily.py::OS_TENOR_TO_COMMON haritasında OLMAK
-- ZORUNDA (bilinmeyen etiket _require_mapped'te ValueError fırlatır).
SELECT
    DAT,
    GRUP_KEY,
    TYPE2,
    CUST_TP,
    AUM_TYPE,
    SEGMENT,
    -- Kasa/O/N: prod'da gerçek MATURITY kovası '1-3'tür (gecelik/kasa ≤3 gün) →
    -- dev de aynı semantiği taşır ki '1-3' bucket'ına dayanan hesaplar (ör.
    -- Sector Blotter) dev'de test edilebilsin. Vadeli: eski deterministik CASE.
    CASE
        WHEN TYPE2 IN ('Kasa', 'O/N') THEN '1-3'
        ELSE CASE (LENGTH(GRUP_KEY) % 5)
            WHEN 0 THEN '4-31' WHEN 1 THEN '46-60' WHEN 2 THEN '92-149'
            WHEN 3 THEN '182-273' ELSE '366-725'
        END
    END AS VADE_BUCKET,
    CASE
        WHEN TYPE2 IN ('Kasa', 'O/N') THEN '1-3'
        ELSE CASE ((LENGTH(GRUP_KEY) + 2) % 5)
            WHEN 0 THEN '1-3' WHEN 1 THEN '4-31' WHEN 2 THEN '32-45'
            WHEN 3 THEN '61-91' ELSE '92-149'
        END
    END AS KALAN_VADE_BUCKET,
    GUNLUK_TRY_BAKIYE,
    AGIRLIKLI_ORT_FAIZ,
    -- Kasa/O/N efektif tenor/DTM 1-3 gün (compound dönüşümü gerçekçi olsun);
    -- Vadeli için eski deterministik placeholder.
    CASE
        WHEN TYPE2 IN ('Kasa', 'O/N') THEN 1.0 + (LENGTH(GRUP_KEY) % 3)
        ELSE 30.0 + (LENGTH(GRUP_KEY) * 13) % 330
    END AS AGIRLIKLI_ORT_TENOR,
    CASE
        WHEN TYPE2 IN ('Kasa', 'O/N') THEN 1.0 + (LENGTH(GRUP_KEY) % 3)
        ELSE 5.0 + (LENGTH(GRUP_KEY) * 7) % 180
    END AS AGIRLIKLI_ORT_DTM,
    MAX(1, CAST(ABS(GUNLUK_TRY_BAKIYE) / 2000000.0 AS INTEGER)) AS CUSTOMER_NUMBER,
    CASE
        WHEN TYPE2 = 'O/N' THEN
            CASE LENGTH(GRUP_KEY) % 3 WHEN 0 THEN 'KGH' WHEN 1 THEN 'BTH' ELSE 'Other O/N' END
        ELSE TYPE2
    END AS SUB_PRODUCT
FROM daily_deposit
