-- DEV (SQLite) detay seviyesinde dönüş satırları.
--
-- WeeklyRollingsEngine.{calendar,segments,pricing,actions,drilldown} bu
-- tek sorgu üzerinde pandas ile aggregation yapar. Slide 1 hala aggregate
-- weekly_rollings.sql kullanır (zaten test edilmiş pivot identity).
--
-- Bind parametreleri:
--   :DATE_START  rapor başlangıç tarihi (inclusive)
--   :DATE_END    rapor bitiş tarihi (inclusive)

SELECT
    CUST_ID,
    ACCT_ID,
    FULL_NM,
    MTRTY_DT AS ROLL_DATE,
    VAL_DT,
    CASE WHEN CCY_CODE = 'TRY' THEN 'TRY' ELSE 'FX' END AS CURRENCY,
    CCY_CODE,
    CUST_TP,
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
    TOTAL_AUM,
    TRY_BALANCE,
    ORIG_BALANCE,
    INTRST_RT,
    EFF_INTRST,
    DTM,
    KAMPANYA_ADI,
    ISPRIVATE,
    ISAFFLUENT,
    ISMAASLI,
    ISNPO
FROM MEVDUAT_DONUSLER_FULLDATA
WHERE MTRTY_DT >= :DATE_START
  AND MTRTY_DT <= :DATE_END
  AND DTM    > 3
  AND VAL_DT <  :DATE_START
ORDER BY MTRTY_DT, CURRENCY, AUM_LOWER, CUST_ID
