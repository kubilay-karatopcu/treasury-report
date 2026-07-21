-- Production Oracle source for the Weekly Rollings (Mevduat Dönüşleri) report.
--
-- WeeklyRollingsEngine expects these output column names (exact, uppercase):
--   ROLL_DATE          DATE     vade dönüş günü (MTRTY_DT)
--   CURRENCY           VARCHAR  'TRY' veya 'FX' (CCY_CODE != 'TRY' → 'FX')
--   CCY_CODE           VARCHAR  ham para birimi kodu
--   CUST_TP            VARCHAR  müşteri tipi (G = Gerçek, T = Tüzel)
--   AUM_LOWER          NUMBER   AUM bandı alt sınırı (₺, 11 dilim — 0, 1M, 2M, 5M, 10M,
--                               25M, 50M, 100M, 200M, 500M, 1B)
--   ISLEM_SAYISI       NUMBER   transaction count (toplanabilir)
--   MUSTERI_SAYISI     NUMBER   distinct müşteri sayısı (TOPLANAMAZ — farklı kırılımda
--                               yeniden COUNT DISTINCT gerekir)
--   TRY_BAKIYE_TOPLAM  NUMBER   TL cinsinden bakiye toplamı (₺)
--   ORIG_BAKIYE_TOPLAM NUMBER   orijinal para birimi cinsinden bakiye toplamı
--   TRY_X_INTRST       NUMBER   Σ(TRY_BALANCE × INTRST_RT) — ağırlıklı faiz payı
--   TRY_X_DTM          NUMBER   Σ(TRY_BALANCE × DTM) — ağırlıklı vade (gün) payı
--
-- Bind parametreleri:
--   :DATE_START  rapor başlangıç tarihi (inclusive)
--   :DATE_END    rapor bitiş tarihi (inclusive)
--
-- Ağırlıklı ortalama prensibi: faiz / vade her zaman Σ(B×r)/ΣB ile hesaplanır.
-- .mean() YASAK — bkz. docs/weekly_rollings_veri_dokumantasyon.md.

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
    FROM A16438.MEVDUAT_DONUSLER_FULLDATA
    WHERE MTRTY_DT >= TO_DATE(:DATE_START, 'DD/MM/YYYY')
      AND MTRTY_DT <= TO_DATE(:DATE_END,   'DD/MM/YYYY')
      AND DTM    > 3                                       -- Standart vadeli mevduat: vade > 3 gün
      AND VAL_DT <  TO_DATE(:DATE_START, 'DD/MM/YYYY')     -- Geleceğe yönelik bakış: sadece geçmişte açılmış mevduatlar
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
