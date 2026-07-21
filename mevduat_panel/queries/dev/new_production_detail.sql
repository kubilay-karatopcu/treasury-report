-- New Production MÜŞTERİ-SEVİYESİ detay (heatmap hücre drill-down).
-- queries/dev/new_production_analysis.sql'in row-level karşılığı: GROUP BY YOK,
-- müşteri kolonları (CUST_ID/ACCT_ID/FULL_NM) korunur, VAL_DT penceresine filtreli
-- (bağlanan mevduat akımı). Bind: :DATE_START, :DATE_END (VAL_DT aralığı).
--
-- Dev tabloda OLMAYAN kolonlar (RELATED_PC_CODE, YENI_PARA, EKSTREM,
-- TCMB_WEEKLY_RATE) NULL/'Bilinmiyor' ile doldurulur → prod'da gerçek kolonlarla
-- değişir. SUB_SEGMENT, AUM_LOWER, VADE_BUCKET new_production_analysis ile aynı
-- mantıkla türetilir (tutarlılık).
SELECT
    VAL_DT,
    MTRTY_DT,
    CCY_CODE,
    CUST_TP,
    CUST_ID,
    ACCT_ID,
    FULL_NM,
    TOTAL_AUM,
    TRY_BALANCE,
    ORIG_BALANCE,
    INTRST_RT,
    EFF_INTRST,
    DTM,
    KAMPANYA_ADI,
    'Bilinmiyor'                                     AS RELATED_PC_CODE,
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
    NULL                                             AS YENI_PARA,
    NULL                                             AS EKSTREM,
    NULL                                             AS TCMB_WEEKLY_RATE
FROM MEVDUAT_DONUSLER_FULLDATA
WHERE VAL_DT >= :DATE_START
  AND VAL_DT <= :DATE_END
ORDER BY VAL_DT, TRY_BALANCE DESC
