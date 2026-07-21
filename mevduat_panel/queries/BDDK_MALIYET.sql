-- BDDK sektör verisi — mevduat MALİYETİ (bakiye + reeskont + faiz gideri).
-- Kaynak: BDDK_MALIYET. Site açılışında ham çekilir; engine/sector_data.py cache'ler.
-- Sektör efektif mevduat maliyeti ≈ Faiz Gideri (±Reeskont) / ortalama Bakiye
-- olarak (kullanım tarafında) türetilecek — burada YALNIZ ham çekim.
--
-- Kolonlar (aynen, büyük harf):
--   BANKA_TIPI  VARCHAR2   Yerli Özel | Kamu | Yabancı
--   TARIH       DATE       ay-sonu snapshot tarihi
--   EOMONTH     VARCHAR2   ay-sonu gün numarası ("31","30","28" ...)
--   CCY_CODE    VARCHAR2   TP (TL) | YP (yabancı para)
--   DATA_TIPI   VARCHAR2   Bakiye | Reeskont | Faiz Gideri (kalem tipi)
--   BAKIYE_TL   NUMBER     ilgili kalemin tutarı (BDDK yayın birimi; ham)
--
-- 31/12/2025'ten İTİBAREN tüm snapshot'lar.
SELECT
    BANKA_TIPI,
    TARIH,
    EOMONTH,
    CCY_CODE,
    DATA_TIPI,
    BAKIYE_TL
FROM A16438.BDDK_MALIYET
WHERE TARIH >= TO_DATE('31/12/2025', 'dd/mm/yyyy')
ORDER BY TARIH, BANKA_TIPI, CCY_CODE, DATA_TIPI
