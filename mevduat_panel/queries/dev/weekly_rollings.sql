-- DEVELOPMENT (SQLite) source for the Weekly Rollings report.
-- Mirror of queries/prod/weekly_rollings.sql sans Oracle schema prefix.
--
-- The dev SQLite table MEVDUAT_DONUSLER_FULLDATA is built by seed_dev_db.py
-- and holds synthetic rows with the same raw columns as the prod source:
--   MTRTY_DT, CCY_CODE, CUST_TP, CUST_ID, TOTAL_AUM,
--   TRY_BALANCE, ORIG_BALANCE, INTRST_RT, DTM
--
-- Bind parametreleri:  :DATE_START, :DATE_END

WITH base AS (
    SELECT
        MTRTY_DT AS ROLL_DATE,
        CASE WHEN CCY_CODE = 'TRY' THEN 'TRY' ELSE 'FX' END AS CURRENCY,
        CCY_CODE,
        CUST_TP,
        CUST_ID,
        CASE
            WHEN COALESCE(TOTAL_AUM, 0) < 1000000   THEN 0
            WHEN TOTAL_AUM < 2000000                THEN 1000000
            WHEN TOTAL_AUM < 5000000                THEN 2000000
            WHEN TOTAL_AUM < 10000000               THEN 5000000
            WHEN TOTAL_AUM < 25000000               THEN 10000000
            WHEN TOTAL_AUM < 50000000               THEN 25000000
            WHEN TOTAL_AUM < 100000000              THEN 50000000
            WHEN TOTAL_AUM < 200000000              THEN 100000000
            WHEN TOTAL_AUM < 500000000              THEN 200000000
            WHEN TOTAL_AUM < 1000000000             THEN 500000000
            ELSE 1000000000
        END AS AUM_LOWER,
        TRY_BALANCE,
        ORIG_BALANCE,
        INTRST_RT,
        DTM
    FROM MEVDUAT_DONUSLER_FULLDATA
    WHERE MTRTY_DT >= :DATE_START
      AND MTRTY_DT <= :DATE_END
      AND DTM    > 3              -- Standart vadeli mevduat: vade > 3 gün
      AND VAL_DT <  :DATE_START   -- Geleceğe yönelik bakış: sadece geçmişte açılmış mevduatlar
)
SELECT
    ROLL_DATE,
    CURRENCY,
    CCY_CODE,
    CUST_TP,
    AUM_LOWER,
    COUNT(*)                       AS ISLEM_SAYISI,
    COUNT(DISTINCT CUST_ID)        AS MUSTERI_SAYISI,
    SUM(TRY_BALANCE)               AS TRY_BAKIYE_TOPLAM,
    SUM(ORIG_BALANCE)              AS ORIG_BAKIYE_TOPLAM,
    SUM(TRY_BALANCE * INTRST_RT)   AS TRY_X_INTRST,
    SUM(TRY_BALANCE * DTM)         AS TRY_X_DTM
FROM base
GROUP BY ROLL_DATE, CURRENCY, CCY_CODE, CUST_TP, AUM_LOWER
ORDER BY ROLL_DATE, CURRENCY, CCY_CODE, CUST_TP, AUM_LOWER
