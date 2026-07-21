-- TCMB sektör verisi — bankacılık sektörü ağırlıklı ortalama mevduat FAİZLERİ.
-- Kaynak: tcmb_deposit_rates (TCMB herkese açık haftalık faiz yayınları yedeği).
-- Site açılışında ham çekilir; engine/sector_data.py cache'ler.
--
-- Kolonlar (aynen):
--   CUR        VARCHAR2   EUR | TRY | USD (para birimi)
--   TCMB_DATE  DATE       haftalık yayın tarihi
--   TIP        VARCHAR2   vade dilimi + para birimi:
--                         "{1 Aya Kadar|3 Aya Kadar|6 Aya Kadar|1 Yıla Kadar|
--                           1 Yıl ve Daha Uzun} Vadeli_{CUR}" | "Toplam_{CUR}"
--   ORT_FAIZ   NUMBER     ağırlıklı ortalama faiz (yüzde; ör. 46.93 = %46.93)
--
-- 2025-01'den İTİBAREN tüm haftalık gözlemler (eski: 31/12/2025). NP akım
-- geçmişi 2025-01'e uzandığından, gün-gün outstanding'in TCMB tarafı (akımın
-- GİRİŞ haftası TCMB oranı) tüm 2025 valörleri için eşleşme bulabilmeli.
SELECT
    CUR,
    TCMB_DATE,
    TIP,
    ORT_FAIZ
FROM A16438.tcmb_deposit_rates
WHERE TCMB_DATE >= TO_DATE('01/01/2025', 'dd/mm/yyyy')
ORDER BY TCMB_DATE, CUR, TIP
