# mevduat_panel/tools — kaynak SPA port araçları

Kaynak: `doguctan/NIM_calculation @ bs_evolution5`. Kaynak repo canlı
geliştiği için taşıma sonunda tek diff turu planlandı
(docs/DASHBOARD_ADAPTATION_PLAN.md §8); bu araçlar o turu tekrarlanabilir
kılar.

Kullanım (kaynak repo klonunun yolu `NIM_SRC` ile verilir):

```bash
NIM_SRC=/path/to/NIM_calculation python3 mevduat_panel/tools/transform_a0.py
python3 mevduat_panel/tools/excise_nii_boot.py
node --check mevduat_panel/static/mevduat_panel.js
```

- `transform_a0.py` — kaynak `templates/index.html`'i üçe ayırır
  (template / CSS / JS), NII markup'ını kırpar, CDN'leri `static/vendor`'a
  çevirir, port eklerini (masa linki, MEVDUAT_CONFIG, fetch shim, tema köprüsü)
  enjekte eder. Idempotent.
- `excise_nii_boot.py` — `static/mevduat_panel.js`'ten NII boot bağlama
  bloklarını söker; boot'u `setPage("cost-analysis")` yapar;
  `updatePageVisibility`/`updateTitle`'ı deposit-only hâle getirir.
  Her silme marker çiftiyle tanımlı, bulunamazsa hata verir; >80 satırlık
  silmeler şüpheli sayılıp reddedilir.
- `sweep_nii_dead.py` — Faz A7 ölü NII süpürmesi: acorn AST çağrı-grafiği
  analiziyle erişilemeyen 59 fonksiyonun gövdesini (52 top-level span)
  `static/mevduat_panel.js`'ten siler. Her span başlangıç+bitiş satırı içeriğiyle
  doğrulanır; satır kayarsa benzersiz eşleşme aranır, doğrulanamazsa hata
  verir. `transform_a0.py` yeniden koşulursa span listesi yeniden üretilmeli.
- `extract_a2.py` — kaynak `app.py`'den Outstanding üçlüsünü (Cost + Tenor +
  Balance motorları, NIMChartBuilder, ortak yardımcılar, parserlar,
  endpoint'ler) satır-aralığı referanslarıyla `engine/` + `routes_*.py`
  dosyalarına çıkarır. Kod gövdeleri birebirdir; yalnız modül bağları
  uyarlanır (`@app.route`→blueprint+`@login_required`, `print`→logging,
  `_ENV`/`_DEMAND_SUBPRODUCTS` sabitleri). Kaynak satır aralıkları script
  içinde sabittir — kaynak repo değişirse aralıklar güncellenmelidir.

  ```bash
  NIM_SRC=/path/to/NIM_calculation python3 mevduat_panel/tools/extract_a2.py
  python3 -m pyflakes mevduat_panel/engine/*.py mevduat_panel/routes_*.py
  ```

DİKKAT: Bu araçlar `static/mevduat_panel.{js,css}` ve
`templates/mevduat_panel/index.html`'i SIFIRDAN üretir. A2+ fazlarında bu
dosyalara yapılan elle düzenlemeler varsa, yeniden koşmadan önce diff alın
ya da elle düzenlemeleri bu scriptlere taşıyın.
