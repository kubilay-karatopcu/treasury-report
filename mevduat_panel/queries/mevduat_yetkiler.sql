-- R10 Yenilik 2 — Ekstrem/Ekstrem+Yetki kural tablosu (rezervasyon sayfaları).
-- reservation_data._apply_mevduat_yetkileri bu tabloyu satır bazında kurala
-- bağlar: müşteri AUM'u eşiğin ALTINDAysa EKSTREM_LIMIT_ALTI, üstünde EKSTREM;
-- CUST_TP='G' normal kolonlar, 'F'/'T' _TUZEL kolonlar; +ZARAR_YETKISI/100.
-- Tarih alt sınırı rezervasyon sorgularıyla aynı (25/11/2025).
SELECT DAT,
       DOVIZ,
       VADE_BASLANGIC,
       VADE_BITIS,
       MAKSIMUM,
       EKSTREM,
       EKSTREM_LIMIT_ALTI,
       ZARAR_YETKISI,
       EKSTREM_TUZEL,
       EKSTREM_LIMIT_ALTI_TUZEL,
       ZARAR_YETKISI_TUZEL
FROM A16438.MEVDUAT_YETKILER
WHERE DAT >= TO_DATE('25/11/2025', 'DD/MM/YYYY')
