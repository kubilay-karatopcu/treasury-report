# Custom Page Tasarım Kuralları (PRISMA / mevduat_panel)

> **Bağlayıcı sözleşme.** Legacy'den taşınan ya da sıfırdan yazılan HER custom
> sayfa (rezervasyon sayfaları gibi) bu kurallara göre yapılır. Kaynağı
> 2026-07-29 revizyon turudur (PR #26–#35 + R6 düzeltmeleri); referans
> uygulama `mevduat_panel/templates/mevduat_panel/prisma/` altındaki 4 sayfadır.
> Kurallardan sapma gerekiyorsa önce bu dosya güncellenir.

## 1. Kabuk — Outstanding SPA'sının aynası

- Şablon `mevduat_panel/prisma/_page.html`'i extend eder; o da `home/_base_prisma.html`
  üzerine `canvas_bleed=True` + `hide_mode_switch=True` kurar (index.html deseni).
- Stylesheet'ler: **`mevduat_panel.css` + `mevduat_prisma.css`** (bu sırayla).
  Ayrı bir "kit" görünümü YASAK — sayfalar Outstanding'den yalnız İÇERİKLE ayrılır.
- Layout: `.mevduat-mount > aside.sidebar (250px, tam boy) + main.main`
  (geniş, kendi scroll'u; `html,body{overflow:hidden}`).
- Başlık: `h2.dashboard-title`. Tabler'a düşmek yasak; `light_content` set edilmez.

## 2. Sol menü + kontrol paneli (sidebar)

- Menü **klasör bazlı** üretilir: `prisma_home/folders.py` →
  `app.config["FOLDER_MENU_PROVIDER"]` → `_folder_nav_inner.html`.
  Elle yazılmış nav listesi YASAK. İzolasyon: `mevduat_panel`, `prisma_home`'u
  import ETMEZ; sağlayıcı config üzerinden gelir, yoksa/patlarsa menü çizilmez
  ve sayfa çalışmaya devam eder.
- **Tarih / para birimi / dönem kontrolleri SOL ALTTA, menünün parçasıdır**
  (`mvp_controls` bloğu; aside içinde `margin-top:auto` ile altta, üstünde
  `--mv-border` ayracı). Ana içerik alanına tarih/ccy kontrolü KONMAZ.
- Kontrol satırı SPA filtre satırıyla AYNI dildedir: **label YANDA**
  (`display:flex;align-items:center;gap:6px`), input/select SPA stiliyle
  (`.sidebar-controls input/select` — _page.html CSS'i). Dikey label YASAK.
  Tarih ileri/geri okları `.wf-nav-btn`. Aralıklar iki ayrı satır:
  "Başlangıç" / "Bitiş" (SPA'nın Date (Start)/(End) karşılığı).
- Durum metni (`#mvpStatus`) kontrol panelinin altındadır.

## 2b. Tarih formatı — İSTİSNASIZ KURAL

- **Her tarih seçici GG.AA.YYYY gösterir; ABD formatı (AA/GG/YYYY) YASAKTIR.**
- Native `<input type="date">` tarayıcı locale'ine göre ABD formatı
  gösterebildiği için doğrudan KULLANILMAZ: `MVP.initDatePicker(el)` çağrılır
  (flatpickr, vendored) — altInput GG.AA.YYYY gösterir, asıl input ISO
  (`Y-m-d`) kalır, sayfa JS'i `.value`'yu değiştirmeden okur.
- Programatik değer yazma `MVP.setDateVal(el, iso)` ile (flatpickr görünümünü
  senkronlar). flatpickr yoksa native'e zarifçe düşer.

## 3. Filtreler

- Boyut filtreleri **Outstanding'in bubble-filter bileşeniyle** çizilir:
  `prisma/bubfilter.js` → `MVP.renderBubFilters` → SPA'nın **`bub-filter-*`**
  sınıfları (stiller mevduat_panel.css'ten; ayrı stil tanımı YASAK).
  Özellikler: All | None, "＋ Group Selected" birleştirme, grup bozma,
  `All (n)` / `None` / `k / n` buton etiketi.
- Host: `.mv-page-filters` satırındaki `#fDims.bub-filter-panel` (kabukta hazır).
- "Güncelle" butonu YOK — değişiklik anında uygulanır (Outstanding deseni).
- Seçim sorgulama: `MVP.bubSelected` (grupları üyelerine açar), `MVP.bubIsAll`.
- Boş seçim = "hiçbiri" (satır geçmez); sabit listelerde (tutar/vade/revize)
  verilen sıra korunur, veriden gelenler Türkçe alfabetik.

## 4. İçerik bölümleri

- Bölüm = `.accordion` + `.accordion-header.open` + `.accordion-body`
  (`max-height:none;overflow:visible;padding:0 16px 16px`).
- **Başlık tıklaması AÇ/KAPA DEĞİL, TAM EKRAN'dır**: SPA'nın `_open` deseni —
  gövde placeholder bırakılarak `document.body`'ye eklenen
  `.chart-fs-overlay`'e TAŞINIR (chart örnekleri yaşar), ✕ / Esc / boşluğa
  tıklama geri koyar. Sınıflar mevduat_panel.css'ten (`chart-fs-*`);
  carousel.js `MVP.openFullscreen/closeFullscreen` sağlar. Overlay'de
  `.chart-fs-body .plot-container` 72vh'ye çekilir.
- Karosel: kontroller `.wf-carousel-nav` içinde `.wf-slide-label` (`n / N`) +
  `.wf-nav-btn` (◀ ▶); slaytlar `.mv-carousel > .mvc-slide` (`hidden` toggle).
  ApexCharts gizli kapta 0 genişlikle çizildiği için slayt/büyütme değişiminde
  rAF içinde `resize` yayınlanır (carousel.js halleder).
- Plot: `.card > .plot-container` + **inline açık yükseklik** (`height:350px`
  gibi); chart seçenekleri `height:'100%'` (büyütme ancak böyle çalışır).

## 5. Chart & tablo kütüphaneleri

- Chart: **ApexCharts**, daima `MVP.renderChart` üzerinden (tema flip'inde
  otomatik yeniden kurulur). `new ApexCharts(...)` doğrudan çağrılmaz.
- Renkler `MVP.palette()` / `MVP.token()` — palet **6 renklidir**; sınır dışı
  indeks Apex'te "undefined color" üretir, daima `pal[i % pal.length]` ya da
  bilinen indeks kullan. İSTİSNA: anlamlı sabit renkler
  (ör. rakip bankaların kurumsal renkleri `BANK_COLORS`) legacy'den birebir
  taşınır, paletten türetilmez.
- Tablo: **AG-Grid** (gruplama gerekiyorsa Enterprise). Düz HTML tablo YASAK.
  Vendor CSS'leri (`ag-grid.css` + `ag-theme-alpine.css`) **HEAD'de,
  mevduat_panel.css'ten ÖNCE** yüklenir — dark override'ları
  (`.ag-theme-alpine{--ag-*}`) ancak böyle kazanır; sonda yüklenirse grid
  beyaz kalır. Enterprise JS `page_scripts`'te kalabilir.

## 6. Legacy taşıma (parite) kuralları

- Kart/karosel/slayt yapısı, plot tipleri ve id'ler, seri adları, filtre
  varsayılanları kaynakla **birebir**; ekstra kart/blok EKLENMEZ.
- Sayı üreten her fonksiyon kaynaktan birebir portlanır — kaynaktaki tuhaflık
  bile korunur (düzeltme ayrı karar ister).
- Kaynak sayfada popup/uyarı varsa (ör. rakip sayfasının kaynak bilgilendirme
  popup'ı: 3 sn geri sayım + "Anladım") PRISMA diliyle geri getirilir.
  Overlay'ler JS ile `document.body`'ye PORTALLANIR — `.mevduat-mount`
  ataları `position:fixed`'in referansını değiştirebilir (kayık merkez +
  kenarda aydınlık şerit belirtisi). Yasal bilgilendirme içeriyorsa (kaynak
  linkleri) linkler popup'ta DAİMA doldurulur (her veri güncellemesinde).
- LLM'e bağımlı parçalar (ör. Piyasa Özeti) masa modunun sıfır-LLM sözleşmesine
  takılır: uç yapılandırılmadıkça panel gizli.

## 7. Masa (uzman sayfası) kuralları

- İçerik alanı geniş: `body.prisma .canvas { max-width: min(96vw, 1600px) }`
  (kabuğun 1280px sınırı masada gevşetilir).
- Süreç kartının TAMAMI tıklanabilir (delegasyon `.proc-cta` href'ine gider;
  iç buton/link/metin seçimi hariç) + `cursor:pointer`.
- Süreçler klasörlere (`department_views[].topics[]`) gruplanır; klasörsüz
  süreç masada görünmez (gizleme mekanizması). Uygulamalar ayrı bölümdedir ve
  uygulama başına departman yetkisi taşır.

## 8. Jinja/test tuzakları (yaşandı, tekrarlama)

- Sözlükte `.items` Jinja'da `dict.items` METODUNU döndürür → şablonda daima
  `obj['items']` köşeli parantez erişimi.
- Yapısal testler ham şablon metnine DEĞİL **render çıktısına** bakar
  (markup makrolarla üretilir); kabuk stub'lanır, `url_for` stub'ı `filename`
  döndürür (vendor asset adları doğrulanabilsin). Bkz. `tests/test_page_parity.py`.
- `jobs/` script'leri argparse KULLANMAZ (Spyder); KONFİG sabitleri + string-
  güvenli liste çevirici (CLAUDE.md'deki ofis kuralı).
