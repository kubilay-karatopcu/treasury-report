-- SQLite equivalent of queries/prod/new_production_analysis.sql
-- Reads from MEVDUAT_DONUSLER_FULLDATA (no schema prefix).
-- Columns missing in dev (RELATED_PC_CODE, YENI_PARA, EKSTREM, TCMB_WEEKLY_RATE)
-- are substituted with NULLs / reasonable proxies so the adapter can handle both.
WITH base AS (
    SELECT
        VAL_DT,
        CCY_CODE,
        CUST_TP,
        -- DEV PROXY: gerçek RELATED_PC_CODE dev tablosunda yok; deterministik
        -- olarak 5 sabit kod (BR, KR, TC, FB, OB) arasına dağıtılıyor.
        -- Prod'da bu satır gerçek RELATED_PC_CODE kolonu ile değişir.
        CASE (
            CAST(strftime('%j', VAL_DT) AS INTEGER)
            + COALESCE(DTM, 0)
            + LENGTH(COALESCE(KAMPANYA_ADI, ''))
        ) % 5
            WHEN 0 THEN 'BR'
            WHEN 1 THEN 'KR'
            WHEN 2 THEN 'TC'
            WHEN 3 THEN 'FB'
            ELSE        'OB'
        END                                              AS RELATED_PC_CODE,
        COALESCE(KAMPANYA_ADI, 'Kampanya Yok')           AS KAMPANYA_ADI,
        CASE
            WHEN COALESCE(TOTAL_AUM, 0) < 1000000        THEN 0
            WHEN TOTAL_AUM             < 2000000          THEN 1000000
            WHEN TOTAL_AUM             < 5000000          THEN 2000000
            WHEN TOTAL_AUM             < 10000000         THEN 5000000
            WHEN TOTAL_AUM             < 25000000         THEN 10000000
            WHEN TOTAL_AUM             < 50000000         THEN 25000000
            WHEN TOTAL_AUM             < 100000000        THEN 50000000
            WHEN TOTAL_AUM             < 200000000        THEN 100000000
            WHEN TOTAL_AUM             < 500000000        THEN 200000000
            WHEN TOTAL_AUM             < 1000000000       THEN 500000000
            ELSE                                               1000000000
        END                                              AS AUM_LOWER,
        CASE
            WHEN DTM BETWEEN 1   AND 3   THEN '01_1-3'
            WHEN DTM BETWEEN 4   AND 31  THEN '02_4-31'
            WHEN DTM BETWEEN 32  AND 35  THEN '03_32-35'
            WHEN DTM BETWEEN 36  AND 45  THEN '04_36-45'
            WHEN DTM BETWEEN 46  AND 60  THEN '05_46-60'
            WHEN DTM BETWEEN 61  AND 91  THEN '06_61-91'
            WHEN DTM BETWEEN 92  AND 181 THEN '07_92-181'
            WHEN DTM BETWEEN 182 AND 273 THEN '08_182-273'
            WHEN DTM BETWEEN 274 AND 365 THEN '09_274-365'
            WHEN DTM BETWEEN 366 AND 540 THEN '10_366-540'
            WHEN DTM > 540               THEN '11_540+'
            ELSE                              '99_DIGER'
        END                                              AS VADE_BUCKET,
        CASE
            WHEN CUST_TP = 'T' AND ISNPO     = 1 THEN 'Tuzel-NPO'
            WHEN CUST_TP = 'T'                   THEN 'Tuzel'
            WHEN CUST_TP = 'G' AND ISPRIVATE = 1 THEN 'Bireysel-Private'
            WHEN CUST_TP = 'G' AND ISAFFLUENT= 1 THEN 'Bireysel-Affluent'
            WHEN CUST_TP = 'G'                   THEN 'Bireysel-Mass'
            ELSE                                      'Diger'
        END                                              AS SUB_SEGMENT,
        TRY_BALANCE,
        ORIG_BALANCE,
        DTM,
        INTRST_RT,
        TRY_BALANCE                                      AS YENI_PARA,  -- proxy: dev has no YENI_PARA col
        TOTAL_AUM                                        AS MAX_AUM,
        TOTAL_AUM
    FROM MEVDUAT_DONUSLER_FULLDATA
    -- Sabit başlangıç (eski: son 6 ay) — prod ile aynı gerekçe: gün-gün
    -- outstanding 2025-10'dan başlar, akım geçmişi 2025-01'e uzanmalı.
    WHERE VAL_DT >= '2025-01-01'
)
SELECT
    VAL_DT,
    CCY_CODE,
    CUST_TP,
    KAMPANYA_ADI,
    AUM_LOWER,
    VADE_BUCKET,
    SUB_SEGMENT,
    SUM(TRY_BALANCE)                                            AS TRY_BAKIYE_TOPLAM,
    SUM(ORIG_BALANCE)                                           AS ORIG_BAKIYE_TOPLAM,
    SUM(TRY_BALANCE * DTM)   / NULLIF(SUM(TRY_BALANCE), 0)     AS WAVG_DTM,
    SUM(TRY_BALANCE * INTRST_RT) / NULLIF(SUM(TRY_BALANCE), 0) AS WAVG_INTRST_RT,
    COALESCE(SUM(YENI_PARA), 0)                                 AS YENI_PARA_TOPLAM,
    SUM(MAX_AUM)                                                AS MAX_AUM_TOPLAM,
    SUM(TOTAL_AUM)                                              AS TOTAL_AUM_TOPLAM,
    NULL                                                        AS WAVG_EKSTREM,
    NULL                                                        AS WAVG_TCMB_WEEKLY_RATE,
    SUM(TRY_BALANCE * DTM)                                      AS TRY_X_DTM,
    SUM(TRY_BALANCE * INTRST_RT)                                AS TRY_X_INTRST,
    NULL                                                        AS TRY_X_EKSTREM,
    NULL                                                        AS TRY_X_TCMB
FROM base
GROUP BY
    VAL_DT, CCY_CODE, CUST_TP, KAMPANYA_ADI,
    AUM_LOWER, VADE_BUCKET, SUB_SEGMENT
ORDER BY
    VAL_DT, CCY_CODE, CUST_TP, KAMPANYA_ADI, AUM_LOWER, VADE_BUCKET, SUB_SEGMENT
