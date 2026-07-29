-- R10 Yenilik 1 — Parametreler ekranı "Ekstrem + Yetki" salt-okunur alanı.
-- Tablodaki EN SON tarihin (DAT) TRY / VADE_BASLANGIC=32 satırlarından iki
-- adayın büyüğü: EKSTREM + ZARAR_YETKISI/100 ve EKSTREM_LIMIT_ALTI +
-- ZARAR_YETKISI/100. Her sayfa yüklemesinde taze çekilir (ana df'ten GELMEZ).
SELECT DAT,
       MAX(GREATEST(EKSTREM + ZARAR_YETKISI / 100.0,
                    EKSTREM_LIMIT_ALTI + ZARAR_YETKISI / 100.0)) AS EKSTREM_YETKI
FROM A16438.MEVDUAT_YETKILER
WHERE DOVIZ = 'TRY'
  AND VADE_BASLANGIC = 32
  AND DAT = (SELECT MAX(DAT)
             FROM A16438.MEVDUAT_YETKILER
             WHERE DOVIZ = 'TRY'
               AND VADE_BASLANGIC = 32)
GROUP BY DAT
