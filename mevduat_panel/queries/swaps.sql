-- Production Oracle source for the Tenor Analysis hedge overlay.
--
-- A16438.SWAPS'tan yalnızca "HEDGE UZUN VADELİ TRY MEVDUAT" amaçlı işlemleri
-- ÇEKER (query sadece filtre atar; bucketleme + "yaşayan işlem" seçimi Python
-- tarafında yapılır — bkz. SwapHedgeEngine).
--
-- Downstream (app.py::SwapHedgeEngine) beklenen kolonlar (exact, uppercase):
--   REFERANSNO         işlem referansı (aynı deal'in tekrarını elemek için)
--   VALORTARIHI        DATE   swap başlangıç (value) tarihi
--   VADETARIHI         DATE   swap vade (maturity) tarihi
--   ALINANMIKTAR       NUMBER alınan bacak nominal
--   ALINANDOVIZCINSI   VARCHAR2 alınan bacak döviz cinsi (TRY ise TRY leg budur)
--   VERILENMIKTAR      NUMBER verilen bacak nominal
--   VERILENDOVIZCINSI  VARCHAR2 verilen bacak döviz cinsi (TRY ise TRY leg budur)
--   ALISFAIZORANI      NUMBER alış (ALINAN) bacak faiz oranı (% nominal)
--   SATISFAIZORANI     NUMBER satış (VERILEN) bacak faiz oranı (% nominal)
--   ISLEMAMACI         VARCHAR2 işlem amacı (filtre kolonu)
--
-- Yaşayan işlem = VALORTARIHI <= snapshot <= VADETARIHI (Python'da). TRY bacak
-- nominali VE faizi ALINAN/VERILEN'den hangisi TRY ise o alınır. Tenor (orijinal
-- vade) = VADETARIHI - VALORTARIHI; DTM (kalan) = VADETARIHI - snapshot.
-- Faiz: swap kuponu QUARTERLY → mevduatla kıyas için yıllık EFFECTIVE'e compound
-- edilir ((1+r/4)^4-1); Python::SwapHedgeEngine içinde yapılır.

SELECT
    REFERANSNO,
    VALORTARIHI,
    VADETARIHI,
    ALINANMIKTAR,
    ALINANDOVIZCINSI,
    VERILENMIKTAR,
    VERILENDOVIZCINSI,
    ALISFAIZORANI,
    SATISFAIZORANI,
    ISLEMAMACI
FROM A16438.SWAPS
WHERE ISLEMAMACI = 'HEDGE UZUN VADELİ TRY MEVDUAT'
