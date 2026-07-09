-- Production Oracle source for the Deposit Detail tab (Realized NII).
--
-- DepositDetailEngine expects these output column names (exact, uppercase):
--   MONTH         DATE          native Oracle DATE — pandas parses automatically
--   PRODUCT       VARCHAR2      composite key TYPE2_CUST_TP_AUM_TYPE_SEGMENT
--                               (Vadeli/Kasa/O/N also append _MATURITY_INF-MATURITY_SUP)
--   SEGMENT       VARCHAR2      segment dimension (independent of PRODUCT parse)
--   BALANCE       NUMBER        average daily TRY balance (₺, not millions)
--   INTEREST_RATE NUMBER        weighted-avg rate in PERCENT form (engine /100 internally)
--   TENOR_RATE    NUMBER        weighted-avg tenor in days (informational; not used in
--                               decomposition calculations)
--   DTM_RATE      NUMBER        weighted-avg REMAINING days to maturity (DTM) —
--                               TENOR_RATE'in kalan-vade muadili (Tenor Analysis
--                               DTM modu)
--   VADE_BUCKET   VARCHAR2      maturity bucket MATURITY_INFIMUM-MATURITY_SUPREMUM
--                               (NULL for non-Vadeli/Kasa/O/N types). PRODUCT'a gömülü
--                               token ile aynı; artık AYRI kolon olarak da veriliyor ki
--                               DepositDetailEngine DIM_BUCKET'ı token-parse'a bağlı
--                               kalmadan doğrudan okusun (daily_deposit.sql ile parite).
--   KALAN_VADE_BUCKET VARCHAR2  REMAINING maturity bucket
--                               REMAINING_MTRTY_INFIMUM-REMAINING_MTRTY_SUPREMUM
--                               (vadeye KALAN güne göre; Tenor Analysis DTM modu +
--                               Cost/Balance "Tenor" kırılımının DTM tarafı). GROUP BY'a
--                               dahil → aynı PRODUCT bir ayda kalan-vade kovasına göre
--                               birden çok satıra bölünür (daily_deposit.sql ile aynı
--                               desen; tüketen engine'ler additive topladığından güvenli).
--   CUSTOMER_NUMBER NUMBER      ortalama günlük müşteri adedi (BALANCE ile aynı yöntem:
--                               günlük adet toplamı / o ayın gün sayısı)
--   SUB_PRODUCT   VARCHAR2      O/N alt-ürünü (SPEC_ACCT_CODE 'K'→KGH, '9'→BTH,
--                               diğer→'Other O/N'); O/N değilse TYPE2 ile aynı
--
-- Aliases are intentionally UNQUOTED so Oracle returns them in uppercase,
-- which matches the column names the engine reads by exact name.

WITH daily AS (
    SELECT
        TRUNC(DAT, 'MM') AS ay,
        DAT,
        TYPE2,
        CUST_TP,
        CASE WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N') THEN AUM_TYPE END AS AUM_TYPE,
        SEGMENT,
        CASE
            WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N')
                THEN TYPE2 || '_' || CUST_TP || '_' || AUM_TYPE || '_' || SEGMENT
                     || '_' || MATURITY_INFIMUM || '-' || MATURITY_SUPREMUM
            ELSE TYPE2 || '_' || CUST_TP || '_' || SEGMENT
        END AS grup_key,
        CASE
            WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N')
                THEN MATURITY_INFIMUM || '-' || MATURITY_SUPREMUM
        END AS vade_bucket,
        CASE
            WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N')
                THEN REMAINING_MTRTY_INFIMUM || '-' || REMAINING_MTRTY_SUPREMUM
        END AS kalan_vade_bucket,
        CASE
            WHEN TYPE2 = 'O/N' AND SPEC_ACCT_CODE = 'K' THEN 'KGH'
            WHEN TYPE2 = 'O/N' AND SPEC_ACCT_CODE = '9' THEN 'BTH'
            WHEN TYPE2 = 'O/N'                          THEN 'Other O/N'
            ELSE TYPE2
        END AS sub_product,
        SUM(TRY_BALANCE) AS daily_balance,
        SUM(INTEREST_RATE * TRY_BALANCE) / NULLIF(SUM(TRY_BALANCE), 0) AS daily_war,
        SUM(TENOR * TRY_BALANCE)         / NULLIF(SUM(TRY_BALANCE), 0) AS daily_wat,
        SUM(DTM * TRY_BALANCE)           / NULLIF(SUM(TRY_BALANCE), 0) AS daily_wdtm,
        SUM(CUSTOMER_NUMBER)                                          AS daily_cust
    FROM A16438.DEPOSITUSAGE_NEW
    WHERE DAT >= TO_DATE('01/01/2025', 'dd/mm/yyyy')
      AND CUR = 'TRY'
      AND TYPE2 <> 'vadesiz'
      AND TYPE <> 'demand'
      AND TO_CHAR(DAT, 'DY', 'NLS_DATE_LANGUAGE=ENGLISH') NOT IN ('SAT', 'SUN')
    GROUP BY
        TRUNC(DAT, 'MM'),
        DAT,
        TYPE2,
        CUST_TP,
        CASE WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N') THEN AUM_TYPE END,
        SEGMENT,
        CASE
            WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N')
                THEN TYPE2 || '_' || CUST_TP || '_' || AUM_TYPE || '_' || SEGMENT
                     || '_' || MATURITY_INFIMUM || '-' || MATURITY_SUPREMUM
            ELSE TYPE2 || '_' || CUST_TP || '_' || SEGMENT
        END,
        CASE
            WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N')
                THEN MATURITY_INFIMUM || '-' || MATURITY_SUPREMUM
        END,
        CASE
            WHEN TYPE2 IN ('Vadeli', 'Kasa', 'O/N')
                THEN REMAINING_MTRTY_INFIMUM || '-' || REMAINING_MTRTY_SUPREMUM
        END,
        CASE
            WHEN TYPE2 = 'O/N' AND SPEC_ACCT_CODE = 'K' THEN 'KGH'
            WHEN TYPE2 = 'O/N' AND SPEC_ACCT_CODE = '9' THEN 'BTH'
            WHEN TYPE2 = 'O/N'                          THEN 'Other O/N'
            ELSE TYPE2
        END
),
ay_gun AS (
    SELECT ay, COUNT(DISTINCT DAT) AS toplam_gun
    FROM daily
    GROUP BY ay
)
SELECT
    d.ay                                        AS MONTH,
    d.grup_key                                  AS PRODUCT,
    d.SEGMENT                                   AS SEGMENT,
    SUM(d.daily_balance) / g.toplam_gun         AS BALANCE,
    SUM(d.daily_war * d.daily_balance)
        / NULLIF(SUM(d.daily_balance), 0)       AS INTEREST_RATE,
    SUM(d.daily_wat * d.daily_balance)
        / NULLIF(SUM(d.daily_balance), 0)       AS TENOR_RATE,
    SUM(d.daily_wdtm * d.daily_balance)
        / NULLIF(SUM(d.daily_balance), 0)       AS DTM_RATE,
    SUM(d.daily_cust) / g.toplam_gun            AS CUSTOMER_NUMBER,
    d.sub_product                               AS SUB_PRODUCT,
    d.vade_bucket                               AS VADE_BUCKET,
    d.kalan_vade_bucket                         AS KALAN_VADE_BUCKET
FROM daily d
JOIN ay_gun g ON g.ay = d.ay
GROUP BY d.ay, d.grup_key, d.SEGMENT, g.toplam_gun, d.sub_product,
         d.vade_bucket, d.kalan_vade_bucket
ORDER BY d.ay, d.grup_key
