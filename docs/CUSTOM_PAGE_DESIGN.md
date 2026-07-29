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
- **Sol-alt kontrol paneli = SPA'NIN DOCK'U (`mevduat_dock.js`) — elle panel
  stillemek YASAK** (3 tur ıskalandı; dock tek kaynak). Akış: sayfa
  kontrolleri (tarih/para birimi/dönem) ÜST ŞERİTTE (`mvp_controls` bloğu,
  `.mv-page-filters` içinde) `<label>Metin <input/select></label>` olarak
  tanımlanır; dock DOMContentLoaded'da bunları toplayıp sidebar'ın altına
  CANLI taşır. "Date Range / Dimensions & View" başlıkları, seçici ◀ ▶
  okları (hover'da boyanan, çerçevesiz `.mv-dock-arrow`), köşeli input
  görünümü ve tarih overlay'i DOCK'TAN gelir — sayfada yeniden yazılmaz.
- Kabuk `mevduat_dock.js`'i script olarak yükler (carousel.js'ten sonra).
- Durum metni (`#mvpStatus`) üst şeritte sağda durur.
- **Dock'ta yatay scrollbar ASLA (R10.1):** `overflow-y: auto` tek başına
  overflow-x'i de `auto` yapar (CSS hesaplama kuralı) ve Windows'ta klasik
  scrollbar 1-2px taşmada bile görünür. `.mv-dock-body`'de `overflow-x:
  hidden` ZORUNLU; ayrıca `.mv-dock-row label`'a `min-width: 0` — flex
  item'ın otomatik minimumu (min-width:auto) içindeki native date input'un
  intrinsic genişliğine kilitlenip daralmayı engelliyordu. Dock'a satır tipi
  eklerken bu iki kuralı koru.

## 2b. Tarih formatı — İSTİSNASIZ KURAL

- **Her tarih seçici GG.AA.YYYY gösterir; ABD formatı (AA/GG/YYYY) YASAKTIR.**
- mevduat kabuğunda BİRİNCİL mekanizma DOCK OVERLAY'idir: `mevduat_dock.js`
  native `<input type=date>`'in metnini şeffaflaştırıp üstüne GG.AA.YYYY yazar
  (takvim ikonu/picker aynen çalışır, `.value` ISO kalır). Sayfa JS'i flatpickr
  ÇAĞIRMAZ.
- Programatik değer yazma `MVP.setDateVal(el, iso, fire)` ile; `fire=true`
  change yayınlar → dock overlay'i VE sayfanın yükleme listener'ı TEK yoldan
  tetiklenir (manuel `loadDate` + setDateVal çifti YASAK — çift yükleme).
- **EDİT MODU (R10):** overlay yalnız GÖSTERİM içindir. Input odak alınca
  dock `mv-dock-datewrap`'e `is-editing` ekler → overlay gizlenir, gerçek
  input metni görünür olur (kullanıcı ne yazdığını görür; tarayıcı o an kendi
  yerel formatını gösterir — kabul edilen tek istisna). Blur'da sınıf kalkar,
  GG.AA.YYYY overlay'i güncel değerle geri gelir; `input` event'i başlıktaki
  tarih etiketini canlı tazeler. Overlay'li her tarih alanında bu üçlü
  (focus/blur/input) ZORUNLUDUR — yoksa yazarken metin üst üste biner.
- Dock'suz bağlamlar (mevduat kabuğu dışı) için `MVP.initDatePicker`
  (flatpickr, altInput d.m.Y) yedek olarak durur.

## 3. Filtreler

- Boyut filtreleri **Outstanding'in bubble-filter bileşeniyle** çizilir:
  `prisma/bubfilter.js` → `MVP.renderBubFilters` → SPA'nın **`bub-filter-*`**
  sınıfları (stiller mevduat_panel.css'ten; ayrı stil tanımı YASAK).
  Özellikler: All | None, "＋ Group Selected" birleştirme, grup bozma,
  `All (n)` / `None` / `k / n` buton etiketi.
- Host: `.mv-page-filters` satırındaki `#fDims.bub-filter-panel` (kabukta hazır).
- **ÜST ŞERİT TEK SATIRDIR (R10):** filtre butonları ve durum metni
  (`#mvpStatus`, "X işlem listeleniyor") AYNI satırda sıralanır — panel önce,
  status `margin-left:auto` ile en sağda. `flex-basis:100%` sarmalayıcı YASAK
  (paneli ikinci satıra atıyordu). **Yatay scroll ASLA çıkmaz:**
  `overflow-x:hidden` + kompakt padding + şeritteki dd butonlarında
  `min-width:0` (SPA'nın 160px sabiti şeridi taşırıyordu); yer kalmazsa
  sarma (wrap) kabul, kaydırma değil.
- **`body.prisma button` RESET TUZAĞI:** prisma.css'in global buton reset'i
  (`background:none;border:none` — 0,1,1 özgüllük) tek sınıflı
  `.bub-filter-dd-btn`'i (0,1,0) ezer → butonlar çıplak metin görünür.
  Çözüm mevduat_prisma.css'te İKİ sınıflı kural:
  `.mevduat-mount .bub-filter-dd-btn { border:1px solid var(--border-mid);
  border-radius:4px; background:var(--bg-panel); padding:5px 10px }`.
  Prisma kabuğunda buton stili tanımlarken özgüllüğü daima bu reset'e karşı
  kontrol et.
- "Güncelle" butonu YOK — değişiklik anında uygulanır (Outstanding deseni).
- Seçim sorgulama: `MVP.bubSelected` (grupları üyelerine açar), `MVP.bubIsAll`.
- Boş seçim = "hiçbiri" (satır geçmez); sabit listelerde (tutar/vade/revize)
  verilen sıra korunur, veriden gelenler Türkçe alfabetik.
- **VARSAYILAN FİLTRELER (tüm sayfalarda ortak):** Kaynak=**MYU**, Tutar=tümü,
  Para Birimi=**TRY**, Vade=**32-35**, Müşteri Tipi=**G**, Revize=**Son
  Revize**. (Değer veride yoksa o boyut tümü-seçili kalır.)
- Filtre bölgesi `.mv-page-filters` ÇITIR ÇERÇEVELİDİR: `border:1px solid
  var(--border-mid); border-radius:6px; background:var(--bg-panel)` — koyu
  temada iç içe görünmesin.

## 4. İçerik bölümleri

- Bölüm = `.accordion` + `.accordion-header.open` + `.accordion-body`
  (`max-height:none;overflow:visible;padding:0 16px 16px`).
- **Başlık tıklaması AÇ/KAPA DEĞİL, TAM EKRAN MODALDIR — MODAL SPEC'İ:**
  - Açılış: SPA `_open` deseni — accordion gövdesi placeholder bırakılarak
    `document.body`'deki `.chart-fs-overlay`'e TAŞINIR (chart örnekleri yaşar).
  - Yapı: overlay (`padding:4vh 5vw` → KENARLAR BOŞ) > topbar(✕) +
    `.chart-fs-inner` iç kutu (`margin:auto; width:min(1500px,100%);`
    `max-height:calc(100%-46px); overflow:auto; bg var(--bg-panel); border`).
  - İçeride `.plot-container` 68vh'ye çekilir (`_page.html` CSS'i).
  - Kapatma ÜÇ yol, hepsi ZORUNLU: **✕**, **Esc**, **iç kutunun DIŞINA
    (overlay boşluğuna) tıklama** (`closest('.chart-fs-inner')` kontrolü).
  - Açılış/kapanışta rAF içinde `resize` yayınlanır (Apex yeniden ölçer).
  - carousel.js sağlar (`MVP.openFullscreen/closeFullscreen`); accordion
    bağları `MVP.initCarousels()` ile kurulur → **karoseli olmayan sayfalar
    dahil HER sayfanın draw() sonunda `MVP.initCarousels()` çağrılır**
    (unutulursa başlık tıklaması ölü kalır — tarihselde yaşandı).
  - Overlay'ler DAİMA `document.body`'de yaşar (bkz. §6 portal notu).
- Karosel: kontroller `.wf-carousel-nav` içinde `.wf-slide-label` (`n / N`) +
  `.wf-nav-btn` (◀ ▶); slaytlar `.mv-carousel > .mvc-slide` (`hidden` toggle).
  ApexCharts gizli kapta 0 genişlikle çizildiği için slayt/büyütme değişiminde
  rAF içinde `resize` yayınlanır (carousel.js halleder).
- Plot: `.card > .plot-container` + **inline açık yükseklik** (`height:350px`
  gibi); chart seçenekleri `height:'100%'` (büyütme ancak böyle çalışır).

## 5. Chart & tablo kütüphaneleri

- Chart: **ApexCharts**, daima `MVP.renderChart` üzerinden (tema flip'inde
  otomatik yeniden kurulur). `new ApexCharts(...)` doğrudan çağrılmaz.
- **Animasyon:** seriler TEK SEFERDE çizilir — `animateGradually` kapalıdır
  (common.js baseOptions; seri seri "pıt pıt" gelmesi yasak).
- **Stacked mixed chart:** kolon+çizgi kombinasyonunda yığılma isteniyorsa
  `chart.type: 'bar'` kullan (`'line'` + `stacked:true` Apex'te yan yana
  çizebiliyor — tarihsel hacim grafiğinde yaşandı).
- **datetime ekseninde `labels.datetimeUTC: false` ZORUNLU** — API tarihleri
  tz'siz ISO gelir, JS yerel gece yarısı olarak parse eder; Apex varsayılanı
  UTC'de etiketler → T günü T-1 görünür (2026-07-29 params bug'ı).
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

- İçerik alanı geniş ve responsive: `body.prisma .canvas
  { max-width: min(97vw, 1800px) }` (kabuğun 1280px sınırı masada gevşetilir).
- Süreç bölümünün başlığı sadece **"Süreçler"** (ek etiket yok).
- Masa modunda (PRISMA_MASA_MODE) topbar'da Atölye pili yerine landing hariç
  her sayfada **"← Masaya dön"** görünür (hide_mode_switch'ten bağımsız) ve
  hedefi **bulunduğun sürecin UZMANIDIR**: `folders.folder_menu` menüye
  `expert_url` koyar, sayfa şablonu `{% set masa_back_url =
  folder_menu['expert_url'] ... %}` ile topbar'a geçirir; yoksa landing.
  **Uygulama sayfaları da (R10):** deposit_panel route'ları
  `FOLDER_MENU_PROVIDER`'ı kendi süreç id'siyle
  (`uygulamalar.panel_parametreler` / `uygulamalar.panel_rezervasyon`) çağırıp
  `masa_back_url`'i render context'inde geçirir — prisma_home İMPORT EDİLMEZ,
  sağlayıcı yoksa landing'e düşülür.
- **Uygulama sayfaları TAM GENİŞLİKTİR (R10.1) — FLEX AUTO-MARGIN TUZAĞI:**
  `.app` column-flex konteynerdir ve prisma.css'in `.canvas { margin: 0 auto }`
  kuralı flex'te CROSS-AXIS AUTO MARGIN'dır → stretch'i iptal edip canvas'ı
  içerik genişliğine (shrink-to-fit) büzerek ortalar. Bu yüzden
  `max-width: none` TEK BAŞINA SAYFAYI GENİŞLETMEZ (R10'da yaşandı: kural
  eklendi, sayfa yine ~700px kaldı). Doğru kural (şablonun <style> bloğunda):
  `body.prisma .canvas { max-width: none !important; margin: 0 !important;
  width: 100%; padding: 40px clamp(20px,2.5vw,48px) 80px }`.
  Headless Chromium ölçümüyle doğrulandı (1909px viewport → canvas 1909).
  Masa (expert.html) `min(97vw, 1800px)`'de KALIR (içeriği zaten geniş; kart
  ızgarası aşırı genişlikte dağılıyor).
- Süreç kartının TAMAMI tıklanabilir (delegasyon `.proc-cta` href'ine gider;
  iç buton/link/metin seçimi hariç) + `cursor:pointer`.
- Süreçler klasörlere (`department_views[].topics[]`) gruplanır; klasörsüz
  süreç masada görünmez (gizleme mekanizması). Uygulamalar ayrı bölümdedir ve
  uygulama başına departman yetkisi taşır.

## 7b. Kabuk genel kuralları (R10)

- **Tarayıcı çeviri balonu kapalı:** her iki base'de (`home/_base_prisma.html`
  ve legacy `templates/base.html`) `<meta name="google" content="notranslate">`
  bulunur — TR/EN karışık metin Chrome'un dil algısını şaşırtıp her sayfada
  "çevir?" önerisi çıkarıyordu. Yeni base yazılırsa meta'yı taşı.

## 7c. Rezervasyon veri kuralları — MEVDUAT_YETKILER (R10)

- **Params ekranı "Ekstrem + Yetki" alanı:** New Funding Rate'in hemen altında
  salt-okunur input. Değer `queries/dep_ekstrem_yetki.sql` ile HER sayfa
  yüklemesinde taze çekilir (ana df'ten GELMEZ): tablodaki en son DAT'ın
  TRY / VADE_BASLANGIC=32 satırlarından
  `MAX(GREATEST(EKSTREM + ZARAR_YETKISI/100, EKSTREM_LIMIT_ALTI +
  ZARAR_YETKISI/100))`. Tarih etiketi GG.AA.YYYY.
- **Rezervasyon sayfalarının EKSTREM / EKSTREM_YETKI metrikleri kural
  bazlıdır** (`reservation_data.derive_ekstrem_columns`):
  - Eşleşme: satır günü = `DAT`, `CCY_CODE` = `DOVIZ`, satır vadesinin İLK
    sayısı ("32-35" → 32) yetki bandının `[VADE_BASLANGIC, VADE_BITIS]`
    aralığında.
  - `CUST_TP` `F`/`T` (tüzel) → `*_TUZEL` kolon seti; `G` → normal set.
  - AUM (`PORTFOLIO_AMT`) < **`AUM_LIMIT_ALTI_ESIK` (PARAMETRİK, 100M)** →
    `EKSTREM_LIMIT_ALTI`; değilse (AUM bilinmiyorsa dahil) `EKSTREM`.
  - `EKSTREM_YETKI = seçilen + ZARAR_YETKISI/100` (tüzelde `_TUZEL`).
  - Kural tablosu yüklenemezse / gün eşleşmezse satırın SQL'den gelen değeri
    aynen kalır — sayfa asla kırılmaz.

## 8. Jinja/test tuzakları (yaşandı, tekrarlama)

- Sözlükte `.items` Jinja'da `dict.items` METODUNU döndürür → şablonda daima
  `obj['items']` köşeli parantez erişimi.
- Yapısal testler ham şablon metnine DEĞİL **render çıktısına** bakar
  (markup makrolarla üretilir); kabuk stub'lanır, `url_for` stub'ı `filename`
  döndürür (vendor asset adları doğrulanabilsin). Bkz. `tests/test_page_parity.py`.
- `jobs/` script'leri argparse KULLANMAZ (Spyder); KONFİG sabitleri + string-
  güvenli liste çevirici (CLAUDE.md'deki ofis kuralı).
- **İlk açılışta veri boş görünebilir:** rezervasyon ETL'i pod açılışında
  arka planda ısınır (`prewarm.py` `_warm_steps` → `load_reservation_df`);
  Oracle sorguları bitene dek sayfalar boş döner — hata değildir. Elle
  tazeleme: `/mevduat-panel/admin/refresh` (`refresh_all` tüm cache'leri
  boşaltıp yeniden ısıtır). Sayfa status'u ısınma ihtimalini söyler.
- **oracledb dtype tuzağı:** legacy DataClient S3 parquet önbelleğinden okurdu
  (int/str korunur); taze oracledb NUMBER'ı float64 döndürür → zaman kolonları
  ('93015' → '93015.0') string parse'ı sessizce kırar ve satırlar DÜŞER
  (2026-07-29: Hazine tamamen, MYU 10:00 öncesi kayboldu). Zaman/ID kolonları
  daima rakama indirgenip pad'lenir; bkz. reservation_data.py DATE_TIME blokları.
