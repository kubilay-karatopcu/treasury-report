-- Production Oracle source for the Daily Deposit Detail tab.
--
-- DailyDepositEngine expects these output column names (exact, uppercase):
--   DAT                 DATE          daily granularity
--   GRUP_KEY            VARCHAR2      composite product key (matches monthly PRODUCT format)
--   TYPE2               VARCHAR2      PRODUCT dimension
--   CUST_TP             VARCHAR2      CUSTOMER_TYPE dimension
--   AUM_TYPE            VARCHAR2      AUM dimension (NULL for non-Vadeli/Kasa/O/N types)
--   SEGMENT             VARCHAR2      SEGMENT dimension
--   VADE_BUCKET         VARCHAR2      maturity bucket MATURITY_INFIMUM-MATURITY_SUPREMUM
--                                     (NULL for non-Vadeli/Kasa/O/N types)
--   KALAN_VADE_BUCKET   VARCHAR2      REMAINING maturity bucket
--                                     REMAINING_MTRTY_INFIMUM-REMAINING_MTRTY_SUPREMUM
--                                     (vadeye KALAN güne göre; Tenor Analysis DTM modu)
--   GUNLUK_TRY_BAKIYE   NUMBER        daily TRY balance
--   AGIRLIKLI_ORT_FAIZ  NUMBER        weighted-avg rate in PERCENT form
--   AGIRLIKLI_ORT_TENOR NUMBER        weighted-avg tenor in days (informational)
--   AGIRLIKLI_ORT_DTM   NUMBER        weighted-avg REMAINING days to maturity (DTM)
--   CUSTOMER_NUMBER     NUMBER        müşteri adedi (gruplanmış alanlardaki toplam)
--   SUB_PRODUCT         VARCHAR2      O/N alt-ürünü (SPEC_ACCT_CODE 'K'→KGH, '9'→BTH,
--                                     diğer→'Other O/N'); O/N değilse TYPE2 ile aynı
--
-- GRUP_KEY format matches monthly PRODUCT key so drill-down endpoint can
-- resolve monthly-bubble clicks to daily time-series rows via GRUP_KEY fallback.
-- NOT: KALAN_VADE_BUCKET GROUP BY'a dahil — aynı GRUP_KEY bir günde birden çok
-- satıra bölünebilir (kalan-vade kovası farklıysa). Tüketen engine'ler additive
-- toplama/ağırlıklı-ortalama yaptığından bu granülerlik artışı güvenlidir.

SELECT
    DAT,
    CASE
        WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N')
            THEN TYPE2 || '_' || CUST_TP || '_' || AUM_TYPE || '_' || SEGMENT
                 || '_' || MATURITY_INFIMUM || '-' || MATURITY_SUPREMUM
        ELSE TYPE2 || '_' || CUST_TP || '_' || SEGMENT
    END                                                AS GRUP_KEY,
    TYPE2,
    CUST_TP,
    CASE WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N') THEN AUM_TYPE END AS AUM_TYPE,
    SEGMENT,
    CASE
        WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N')
            THEN MATURITY_INFIMUM || '-' || MATURITY_SUPREMUM
    END                                                AS VADE_BUCKET,
    CASE
        WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N')
            THEN REMAINING_MTRTY_INFIMUM || '-' || REMAINING_MTRTY_SUPREMUM
    END                                                AS KALAN_VADE_BUCKET,
    SUM(TRY_BALANCE)                                   AS GUNLUK_TRY_BAKIYE,
    SUM(INTEREST_RATE * TRY_BALANCE)
        / NULLIF(SUM(TRY_BALANCE), 0)                  AS AGIRLIKLI_ORT_FAIZ,
    SUM(TENOR * TRY_BALANCE)
        / NULLIF(SUM(TRY_BALANCE), 0)                  AS AGIRLIKLI_ORT_TENOR,
    SUM(DTM * TRY_BALANCE)
        / NULLIF(SUM(TRY_BALANCE), 0)                  AS AGIRLIKLI_ORT_DTM,
    SUM(CUSTOMER_NUMBER)                               AS CUSTOMER_NUMBER,
    CASE
        WHEN TYPE2 = 'O/N' AND SPEC_ACCT_CODE = 'K' THEN 'KGH'
        WHEN TYPE2 = 'O/N' AND SPEC_ACCT_CODE = '9' THEN 'BTH'
        WHEN TYPE2 = 'O/N'                          THEN 'Other O/N'
        ELSE TYPE2
    END                                                AS SUB_PRODUCT
FROM A16438.DEPOSITUSAGE_NEW
WHERE DAT >= TO_DATE('31/12/2025', 'dd/mm/yyyy')
  AND CUR = 'TRY'
  AND TYPE2 <> 'vadesiz'
  AND TYPE <> 'demand'
  AND TO_CHAR(DAT, 'DY', 'NLS_DATE_LANGUAGE=ENGLISH') NOT IN ('SAT', 'SUN')
GROUP BY
    DAT,
    TYPE2,
    CUST_TP,
    CASE WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N') THEN AUM_TYPE END,
    SEGMENT,
    CASE
        WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N')
            THEN MATURITY_INFIMUM || '-' || MATURITY_SUPREMUM
    END,
    CASE
        WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N')
            THEN REMAINING_MTRTY_INFIMUM || '-' || REMAINING_MTRTY_SUPREMUM
    END,
    CASE
        WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N')
            THEN TYPE2 || '_' || CUST_TP || '_' || AUM_TYPE || '_' || SEGMENT
                 || '_' || MATURITY_INFIMUM || '-' || MATURITY_SUPREMUM
        ELSE TYPE2 || '_' || CUST_TP || '_' || SEGMENT
    END,
    CASE
        WHEN TYPE2 = 'O/N' AND SPEC_ACCT_CODE = 'K' THEN 'KGH'
        WHEN TYPE2 = 'O/N' AND SPEC_ACCT_CODE = '9' THEN 'BTH'
        WHEN TYPE2 = 'O/N'                          THEN 'Other O/N'
        ELSE TYPE2
    END
ORDER BY
    DAT, GRUP_KEY
