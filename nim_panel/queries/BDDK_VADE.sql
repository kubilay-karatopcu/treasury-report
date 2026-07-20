-- BDDK sektör verisi — mevduatın VADE kırılımı (TP / YP / Maden Depo).
-- Kaynak: BDDK_VADE (BDDK herkese açık sektör tabloları yedeği).
-- Site açılışında ham çekilir; engine/sector_data.py cache'ler.
--
-- Kolonlar (aynen, büyük harf):
--   BANKA_TIPI    VARCHAR2   Yerli Özel | Kamu | Yabancı
--   TARIH         DATE       ay-sonu snapshot tarihi
--   CCY_CODE      VARCHAR2   TP (TL) | YP (DTH) | Ma (maden/kıymetli maden depo)
--   DATA_TIPI     VARCHAR2   bileşik: {TP|YP_DTH|MadenDepo}_{vadeToken}_{yerlesim}
--   VADE_KIRILIM  VARCHAR2   Vadesiz | 0-1_Ay | 1_3_Ay | 3_6_Ay | 6_12_Ay | 1_Yil+
--   CUST_INFO     VARCHAR2   Y_I (yurt içi) | Y_D (yurt dışı)
--   BAKIYE_TL     NUMBER     bakiye (BDDK yayın birimi; ham)
--
-- 31/12/2025'ten İTİBAREN tüm snapshot'lar.
SELECT
    BANKA_TIPI,
    TARIH,
    CCY_CODE,
    DATA_TIPI,
    VADE_KIRILIM,
    CUST_INFO,
    BAKIYE_TL
FROM A16438.BDDK_VADE
-- 2025-09'dan itibaren (eski: 31/12/2025): gün-gün outstanding'in BDDK-mix
-- varyantı 2025-10'dan başlar ve her gün için "son ay-sonu ≤ gün" sektör vade
-- ağırlığı gerekir → ilk gerekli ay-sonu 30/09/2025.
WHERE TARIH >= TO_DATE('30/09/2025', 'dd/mm/yyyy')
ORDER BY TARIH, BANKA_TIPI, CCY_CODE, DATA_TIPI
