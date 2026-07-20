# nim_panel/tools — kaynak SPA port araçları

Kaynak: `doguctan/NIM_calculation @ bs_evolution5`. Kaynak repo canlı
geliştiği için taşıma sonunda tek diff turu planlandı
(docs/DASHBOARD_ADAPTATION_PLAN.md §8); bu araçlar o turu tekrarlanabilir
kılar.

Kullanım (kaynak repo klonunun yolu `NIM_SRC` ile verilir):

```bash
NIM_SRC=/path/to/NIM_calculation python3 nim_panel/tools/transform_a0.py
python3 nim_panel/tools/excise_nii_boot.py
node --check nim_panel/static/nim_panel.js
```

- `transform_a0.py` — kaynak `templates/index.html`'i üçe ayırır
  (template / CSS / JS), NII markup'ını kırpar, CDN'leri `static/vendor`'a
  çevirir, port eklerini (masa linki, NIM_CONFIG, fetch shim, tema köprüsü)
  enjekte eder. Idempotent.
- `excise_nii_boot.py` — `static/nim_panel.js`'ten NII boot bağlama
  bloklarını söker; boot'u `setPage("cost-analysis")` yapar;
  `updatePageVisibility`/`updateTitle`'ı deposit-only hâle getirir.
  Her silme marker çiftiyle tanımlı, bulunamazsa hata verir; >80 satırlık
  silmeler şüpheli sayılıp reddedilir.

DİKKAT: Bu araçlar `static/nim_panel.{js,css}` ve
`templates/nim_panel/index.html`'i SIFIRDAN üretir. A2+ fazlarında bu
dosyalara yapılan elle düzenlemeler varsa, yeniden koşmadan önce diff alın
ya da elle düzenlemeleri bu scriptlere taşıyın.
