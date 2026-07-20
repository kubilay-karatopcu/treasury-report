-- BDDK sektör verisi — mevduatın MÜŞTERİ TİPİ × BAKİYE BÜYÜKLÜĞÜ (AUM) kırılımı.
-- Kaynak: BDDK_AMT_KIRILIM (BDDK'nın herkese açık sektör tabloları yedeği).
-- Site açılışında ham (transform'suz) çekilir; engine/sector_data.py cache'ler.
--
-- Kolonlar (aynen, büyük harf):
--   BANKA_TIPI      VARCHAR2   Yerli Özel | Kamu | Yabancı (banka grubu)
--   TARIH           DATE       ay-sonu snapshot tarihi
--   CCY_CODE        VARCHAR2   TP (TL) | YP (yabancı para / DTH)
--   DATA_TIPI       VARCHAR2   bileşik etiket:
--                              {TP|YP_DTH}_{G|Ticari|Resmi}_{bandToken}_{yerlesim}
--   CUST_TIP        VARCHAR2   Gercek | Ticari | Resmi (müşteri tipi)
--   BAKIYE_KIRILIM  VARCHAR2   0-10K | 10K-50K | 50K-250K | 250K_1MIO | 1MIO+ (AUM bandı)
--   CUST_INFO       VARCHAR2   Y_I (yurt içi yerleşik) | Y_D (yurt dışı yerleşik)
--   BAKIYE_TL       NUMBER     bakiye (bin/milyon TL — BDDK yayın birimi; ham)
--
-- 31/12/2025'ten İTİBAREN tüm snapshot'lar çekilir (zaman serisi kaynağı).
SELECT
    BANKA_TIPI,
    TARIH,
    CCY_CODE,
    DATA_TIPI,
    CUST_TIP,
    BAKIYE_KIRILIM,
    CUST_INFO,
    BAKIYE_TL
FROM A16438.BDDK_AMT_KIRILIM
WHERE TARIH >= TO_DATE('31/12/2025', 'dd/mm/yyyy')
ORDER BY TARIH, BANKA_TIPI, CCY_CODE, DATA_TIPI
