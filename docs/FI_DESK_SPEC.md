# FI Masası Veri Giriş Modülü — Tasarım (Faz 0)

> Muhabir Bankacılık ve Yapılandırılmış Fonlama ("FI masası") bünyesindeki
> işlerin (borçlanma teklifleri, tahvil ihraçları vb.) veri girişi, onay akışı
> ve sonrasında PRISMA süreç/dashboard'larına beslenmesi. Kaynak gereksinim:
> `Dashboard_Data_Tools.xlsx` / Input sayfası (2026-07-30) + soru-cevap turu.
> Kararlar aşağıda; sapma gerekiyorsa önce bu dosya güncellenir.

## 1. Karar özeti (soru-cevap turu, 2026-07-30)

1. **Üst kimlik var:** bir borçlanma ihtiyacına (deal) birden fazla
   lender'dan ayrı teklif (offer) gelir; her teklifin kendi geçmişi olur.
2. **Piyasa istihbaratı da girilir:** Borrower Bank varsayılanı biziz
   (`FI_LU_BANK.IS_SELF=1`), kullanıcı değiştirebilir.
3. **İki rol:** `ENTRY` (veri girici) ve `APPROVER` (onaycı). Her giriş/edit
   PENDING doğar; onaycı APPROVED/REJECTED yapar. Raporlama yalnız onaylı
   son event'i görür.
4. ~~**Lookup tabloları uygulamadan EDİTLENMEZ**~~ — **KARAR DEĞİŞTİ
   (2026-08-05):** günlük bakım (banka/exporter/rol düzeltmeleri) için ofis
   script'i beklemek masayı yavaşlattı. Lookup'lar artık `/fi-desk/admin`
   YÖNETİM ekranından, yalnız **IS_ADMIN bayraklı** kullanıcılarca
   düzenlenir (aşağıda §5b). Ofis job'ları İLK KURULUM / excel'den toplu
   yükleme için durur; TAM YENİLEME yaptıklarından koşarlarsa ekran
   değişikliklerinin üzerine yazarlar (ekran bunu uyarıyla söyler).
5. **USD eqv. kur kaynağı AÇIK KONU** — netleşene kadar elle girilir;
   kaynak belli olunca `auto` kuralına çevrilecek.
6. **LOST tekrar BIDDING olabilir** (edit serbest); **WON → REALIZED geçişi
   value date ile otomatik** (view'da sorgu anında hesaplanır, cron yok).
7. **Amortized işlemler için ödeme planı** alt tablosu var
   (`FI_OFFER_SCHEDULE`: tarih, anapara, faiz).

## 2. Veri modeli

Şema: bağlanan kullanıcının kişisel şeması (deposit paneli deseni,
`A16438.DEP_SMALL_APP_PARAMS` gibi). DDL/seed: `jobs/fi_desk_schema.py`
(ofiste Spyder'dan tek koşu, KONFİG sabitleri, argparse yok).

```
FI_DEALS (üst kimlik)          1 ── n  FI_OFFERS (deal × lender thread'i)
  DEAL_ID, BORROWER_BANK,               OFFER_ID, DEAL_ID
  PRODUCT_TYPE, DEAL_LABEL                   │ 1 ── n
                                   FI_OFFER_EVENTS (APPEND-ONLY snapshot)
                                     EVENT_ID, EVENT_SEQ, EVENT_TYPE
                                     (ENTRY|EDIT|STATUS_CHANGE),
                                     EVENT_TS/USER, APPROVAL_STATUS
                                     (PENDING|APPROVED|REJECTED) + onay meta
                                     + Excel'deki ~37 veri kolonu
                                          │ 1 ── n (Amortized ise)
                                   FI_OFFER_SCHEDULE (EVENT_ID'ye bağlı plan)
```

- **Append-only:** event satırları asla UPDATE/DELETE edilmez; tek istisna
  onay alanlarıdır (`APPROVAL_STATUS`, `APPROVED_BY/TS`, `REJECT_REASON`).
  Edit = önceki snapshot'ın kopyası + değişiklik, `EVENT_SEQ+1`, PENDING.
  Zaman damgalı tam geçmiş ("kim ne zaman neyi değiştirdi") bedavaya gelir.
- **Current ilişkisi — view OPSİYONEL (2026-07-30 ofis koşusu):** EDW
  kişisel şemasında CREATE VIEW yetkisi yok (ORA-01031). Tek kaynak
  `jobs/fi_desk_schema.py::CURRENT_SELECT`tir (teklif başına en son ONAYLI
  event + deal başlığı + türetilenler): şema script'i view'ı yaratmayı
  DENER, yetki yoksa uyarıp geçer; uygulama (`db.current_relation()`) ve
  dashboard blokları aynı SQL'i HER ZAMAN inline alt-sorgu olarak koşar —
  davranış view'lı/view'sız birebir (test: `test_current_relation_matches_
  view`). Yetki alınırsa script yeniden koşularak view ad-hoc SQL kolaylığı
  için yaratılabilir; hiçbir bileşen buna bağımlı değildir. Geçmiş analizi
  (bidding→won süresi vb.) doğrudan events tablosundan.
- **Türetilen kolonlar (girilmez):**
  - `REPORTING_STATUS`: WON → value date geçtiyse REALIZED değilse PENDING;
    LOST → UNREALIZED; BIDDING → BIDDING; Deal Status taşımayan ürünler
    (Fiduciary, Eurobond, Sub Bonds) → value date'e göre REALIZED/PENDING.
  - `TENOR_DAYS` = MATURITY_DT − VALUE_DT (Amortized'da WAL raporlama
    katmanında ödeme planından hesaplanır).
- **Lookup'lar:** `FI_LU_BANK` (banka → ülke/region/group company; IS_SELF),
  `FI_LU_LIST` (genel kod listeleri; `COUNTRY` satırlarında `ATTR1` = region,
  `ESG_ELIGIBILITY` satırlarında `PARENT_CD` = ESG_TYPE kaskadı,
  `ADDITIONAL_COST_TYPE` = ek maliyet tipleri — genişletmek için schema
  job'undaki SEED_LISTS'e satır ekle, uygulamadan editlenmez),
  `FI_LU_USER` (sicil → rol). Seed'teki içerik YER TUTUCUDUR; gerçek
  mapping excelleri gelince tam setle yenilenir.
- **Additional cost + all-in (2026-08-04):** `ADDITIONAL_COSTS` kolonu
  `{"TIP": bps}` JSON map'idir (kalemler ayrı ayrı görünür; tipler
  `ADDITIONAL_COST_TYPE` listesinden, aynı tip iki kez giremez). All-in
  otomatiği artık `base + coverage + Σ(additional)` toplar;
  `ALL_IN_RATE_TXT` all-in'in string gösterimidir ve SUNUCUDA derlenir:
  FLOATING → `"{base rate etiketi} + {all-in} bps"` (ör. `3M SOFR + 245
  bps`), FIXED → `"{all-in} bps (Fixed)"`. Eski tek-sayı
  `ADDITIONAL_FEE_COST` kolonu var olan kurulumlarda durur, artık yazılmaz.

## 3. Alan matrisi — `fi_desk/field_matrix.json`

Excel'deki ürün × kolon zorunluluk matrisinin kodlanmış hali; **tek
otorite**. Hem formu dinamik çizer hem sunucu tarafı validasyonu besler.

- `fields`: alan → etiket, bölüm (deal/lender/offer/underlying/esg), girdi
  tipi (`lookup_bank`, `list` (+liste adı), `enum`, `date`, `number`,
  `text`, `auto`, `readonly`, `schedule`), depolama
  (`deal`/`event`/`subtable`/`derived`).
- `products`: 14 ürün × alan → `R` (zorunlu) / `O` (opsiyonel) / `-`
  (üründe yok) / `D` (girişte opsiyonel, sonradan tamamlanması beklenir —
  2026-08-04: trade-finance üçlüsünde Importer/Exporter/Reference No/Goods;
  eksikse işlem listesi satırı sarı boyanır). Excel'deki 14 satırın profilleri: trade-finance üçlüsü
  (underlying R), bilateral ikilisi (underlying O), sendikasyon ikilisi
  (underlying yok), sermaye piyasası dörtlüsü (coverage yok, all-in fixed
  USD R), bond ikilisi (ek olarak deal status ve lender country yok),
  Fiduciary (minimal, ESG yok).
- `rules`: koşullar ve otomatikler — `required_if` (Coverage=YES →
  provider; RATE_TYPE'a göre fixed/floating alanları; Amortized → ödeme
  planı; Sustainability=YES → ESG type/eligibility) ve `auto` (bankadan
  group company/ülke; ülkeden region; Coverage=NO → coverage rate 0;
  all-in = base+spread+coverage, kullanıcı ezebilir).
- Excel'de Notes kolonu çoğu üründe "x" işaretli; bilinçli sapma olarak
  her yerde opsiyonel yapıldı (not zorunlu tutulmaz).

`tests/test_fi_desk_matrix.py` matris ↔ DDL senkronunu ve kural
bütünlüğünü doğrular (jobs script'i metin olarak ayrıştırılır, oracledb
import edilmez).

## 4. Statü makinesi

```
girişte seçilir ──► BIDDING ──► WON ──► (value date) REALIZED | PENDING
                       │  ▲
                       ▼  │  (edit ile geri dönüş serbest)
                      LOST ──► UNREALIZED
statüsüz ürünler (Fiduciary/Eurobond/SubBonds): value date → REALIZED|PENDING
```

Deal Status kullanıcı girdisidir (BIDDING/WON/LOST); Reporting Status her
zaman türetilir, hiçbir yerde saklanmaz.

## 5. Onay akışı

- ENTRY rolü: yeni giriş, edit, statü değişikliği, SİLME TALEBİ → hepsi
  PENDING event.
- APPROVER rolü: bekleyen event listesini görür; APPROVED → current'a
  yansır, REJECTED → gerekçeyle geçmişte kalır (satır silinmez).
- **Silme (2026-08-04):** edit ekranındaki "Kaydı Sil" son snapshot'ın
  kopyasını `EVENT_TYPE='DELETE'` PENDING event'i olarak yazar (append-only
  bozulmaz). Onaylanınca teklif current ilişkisinden ve işlem listesinden
  düşer (CURRENT_SELECT'te onaylı-DELETE `NOT EXISTS` filtresi); tüm geçmiş
  events tablosunda okunur kalır. Red → hiçbir şey değişmez. Bekleyen ikinci
  silme talebi 409.
- Kendi girdiğini onaylama kısıtı v1'de YOK (masa küçük); gerekirse sonra.
- Rol kontrolü `FI_LU_USER` üzerinden; departman beyaz listesi (deposit
  panelindeki desen) ekran erişimi için ayrıca uygulanır.

## 5b. Yönetim ekranı (admin panel, 2026-08-05)

- **Yetki:** `TRESUARY_LDAP.IS_ADMIN` kolonu (NUMBER(1)) —
  `jobs/ldap_admins.py` kolonu ekler ve KONFİG'teki sicilleri işaretler
  (tam yenileme). `User.is_admin` login/load_user'da okunur (kolon yokken
  herkes 0; 5 dk kullanıcı cache'i vardır). Bu bayrak GENEL masa-admin
  bayrağıdır: ileride diğer masa admin ekranları da aynı bayrağı kullanır.
- **Ekran:** `/fi-desk/admin` (İşlemler başlığındaki "Yönetim" butonu;
  admin olmayana 403). Üç bölüm: Bankalar (`FI_LU_BANK`, IS_SELF tekliği
  korunur, self banka silinemez), Kullanıcı Rolleri (`FI_LU_USER`, sicil
  bazında tam yenileme; ENTRY/APPROVER), Listeler (`FI_LU_LIST` — EXPORTER
  varsayılan, tüm listeler seçilebilir: ADDITIONAL_COST_TYPE, BASE_RATE...).
- **Silme HARD DELETE'tir:** lookup satırı yalnız seçim listesini besler;
  geçmiş event'ler değerleri metin taşıdığından tarihçe bozulmaz.
- **Job etkileşimi:** `fi_desk_lookup_import.py` (BANK+COUNTRY+EXPORTER) ve
  `fi_desk_users.py` (FI_LU_USER) tam yenilemedir → ekran değişikliklerini
  ezerler. Bunlar artık ilk kurulum/toplu yükleme aracıdır; günlük bakım
  Yönetim ekranındadır.

## 6. Ekranlar (Faz 1–2, PRISMA Uygulamalar altı)

`fi_desk` blueprint'i; kabuk `docs/CUSTOM_PAGE_DESIGN.md`'e birebir uyar
(prisma `_page.html`, dock, bub-filter, AG-Grid, GG.AA.YYYY). `processes.py`'a
`uygulamalar.fi_veri_girisi` ve `uygulamalar.fi_islemler` kayıtları eklenir
(rezervasyon takibi modeli: kendi `config_flag`'i, masa dönüş linki).

1. **Veri girişi:** Product Type seçilir → matris o ürünün bölümlerini ve
   alanlarını açar; koşullu alanlar cevaba göre belirir; auto alanlar
   lookup'tan dolar; Amortized'da satır-ekle ödeme planı. Kaydet → ENTRY
   event (PENDING).
2. **İşlem listesi / detay / onay:** AG-Grid liste (`V_FI_OFFER_CURRENT` +
   bekleyenler ayrı sekme/rozet), bub-filter'larla ürün/statü/ccy/lender.
   Satır → detay + event timeline. "Düzenle" formu dolu açar → EDIT event.
   Onaycıya Approve/Reject butonları. Satırlar duruma göre boyanır
   (2026-08-04): MAVİ = onay aşamasında (yeni giriş / bekleyen güncelleme /
   silme talebi), SARI = onaylı ama `D` alanları eksik (Durum kolonu
   "EKSİK ALAN VAR" + eksik alan listesi ayrı kolonda), YEŞİL = onaylı ve
   tam.

Yazma yolu deposit panelindeki `_execute_dml` deseni (bind değişkenli,
transaction'lı, rollback'li); okuma `dc.get_data`. ID üretimi uygulama
tarafında: `FID-`/`FIO-` önekli, `NVL(MAX(EVENT_ID),0)+1` event id (masa
hacmi düşük, advisory yeterli).

## 7. Fazlar

- **Faz 0 (tamam):** şema + matris + seed script + testler.
- **Faz 1 (tamam):** `fi_desk` blueprint + veri giriş formu
  (`/fi-desk/entry`) + bootstrap/entries API'leri + ENTRY event yazımı.
  Kayıt zinciri: `processes.py` `uygulamalar.fi_veri_girisi`,
  `prisma_nav.json` FI Masası bölümü, `app.py` korumalı blueprint kaydı
  (`FI_DESK_ENABLED`). DEV_MODE'da `fi_desk/db.py` yerel DuckDB'ye düşer
  (şema `jobs/fi_desk_schema.py` sabitlerinden çevrilir) — form lokalde
  uçtan uca çalışır, testler bu yolu kullanır.
- **Faz 2 (tamam):** `/fi-desk/records` — AG-Grid liste (teklif başına
  onaylı current ya da "ONAY BEKLİYOR" satırı + bekleyen-güncelleme
  sayacı), satır detayı overlay'i (güncel durum, ödeme planı, event
  timeline), edit (`/fi-desk/entry?offer=...` → yeni PENDING event;
  yalnız statü değiştiyse `STATUS_CHANGE`), onay/red uçları
  (`/api/events/<id>/approval`, APPROVER rolü, PENDING-koşullu UPDATE).
  Kayıt: `uygulamalar.fi_islemler` + nav "İşlemler".
- **Faz 3 (tamam):** `jobs/fi_desk_dashboards.py` (ofiste tek koşu,
  KONFİG sabitleri) — (1) V_FI_OFFER_CURRENT / FI_OFFER_EVENTS /
  FI_OFFER_SCHEDULE tablo dokümanlarını S3'e upsert eder (tablolar sunum
  editörünün katalog/LLM akışında görünür olur); (2) `p_fi_desk`
  dashboard'unu (3 sayfa: Özet — huni/hacim/lender/region/son kayıtlar;
  Fiyatlama — vade×all-in haritası, ağırlıklı all-in, WON vs LOST;
  Vade & ESG — maturity profili, itfa takvimi, ESG payı) ürün/statü/
  ccy/lender enum_multi filtreleriyle üretip S3 manifest'ine yazar.
  Yalnız onaylı kayıtlar raporlanır; duck_cache açık olduğundan blok
  SQL'leri oracle_duck çevirmeni kapsamında tutulur (test bunu üretim
  resolver/binder + çevirmeniyle dev DuckDB'de koşarak sabitler).

## 7b. Masa yayını ve yetki modeli (deposits ile aynı)

Sicil klasörü (`prisma-treasury/presentations/{sicil}/{pid}/`) YETKİ değil
DEPOLAMA modelidir: canlı/düzenlenebilir manifest daima sahibinin "Rapor
Şablonları" klasöründe durur (deposits importer'ı da `p_dep_*`'ı böyle
yazar). Ekibe/masaya açılım UZMAN katmanından geçer:

1. **`jobs/seed_fi_expert.py`** FI uzmanını (id `fi`, kaynak kopya
   `dev_data/experts/fi.yaml`) S3'e yazar. **Erişim buradadır:**
   `access_scope.read` departmanları uzmanı görür; uygulama başına ek
   kısıt `applications[].departments`. FI masasının LDAP departman adı
   netleşince KONFİG'e eklenir.
2. Uygulamalar (veri girişi + işlemler) uzmanın **Uygulamalar** bölümünde
   çıkar (süreç kaydı `processes.py`'da, bağ YAML'da).
3. **Pano = snapshot bağı:** `fi_desk_dashboards.py` (`PUBLISH_SNAPSHOT`)
   p_fi_desk'i dondurup `bound_experts=["fi"]` snapshot'ı yazar → uzmanı
   görebilen herkes panoyu uzman sayfasındaki ızgaradan açar (cross-owner;
   Phase 10B mekanizması). Job önceki otomatik snapshot'ını silip yenisini
   yazar (birikme yok). Canlı/filtreli sürüm sahibinin şablon listesinde
   kalır; masadaki görünüm donmuş okuma kopyasıdır.

**Rol yönetimi (ENTRY / APPROVER):** birincil yol artık Yönetim ekranıdır
(§5b — IS_ADMIN'li kullanıcı FI_LU_USER'ı ekrandan yönetir).
`jobs/fi_desk_users.py` toplu/ilk kurulum içindir ve TAM YENİLEME yapar
(koşarsa ekran değişikliklerini ezer). Ekran erişimi (uzman departman
yetkisi) ile rol ayrıdır: rolü olmayan kullanıcı ekranı görür ama form
salt-okunur kalır, onay butonları çıkmaz.

**Lookup yüklemesi:** `jobs/fi_desk_lookup_import.py` — "Dropdown
Listeler.xlsx"i okur: "Group, Country, Region" sheet'i → `FI_LU_BANK`
tam yenileme (grup şirketinin ülke/bölgesi, grup adı Bank kolonunda ayrı
satırsa oradan taşınır) + uniq ülke→region çiftleri `COUNTRY` listesine;
"Lehtar" sheet'i → yalnız `EXPORTER` listesi (2026-08-04 kararı: Importer
bizim müşterimizdir, lehtar listesinden DOLDURULMAZ — aşağıdaki "Importer
araması"na bak). Tam yenileme, schema seed'inin yer tutucu kayıtlarını
otomatik temizler; CURRENCY/BASE_RATE/ADDITIONAL_COST_TYPE gibi excelde
olmayan listeler korunur. Form dropdown'ları bootstrap API'siyle bu
tablolardan okunduğu için koşu sonrası sayfa yenilemek yeterlidir.

**Importer araması (2026-08-04):** form alanı sunucu destekli typeahead'dir
(`GET /fi-desk/api/customers?q=`, en az 3 karakter, en çok 30 sonuç,
250 ms debounce). Kaynak `FI_DESK_CUSTOMER_TABLE` /
`FI_DESK_CUSTOMER_NAME_COL` env'leriyle verilen EDW müşteri tablosudur;
konfigüre edilmemişse (ve dev'de) `FI_LU_LIST IMPORTER` fallback'i çalışır.
32M satırlık tabloda arama **UPPER-önek LIKE + ROWNUM erken kesme** ile
yapılır (`WHERE UPPER(col) LIKE 'ABC%' AND ROWNUM <= 120`): önek araması
`UPPER(col)` fonksiyon indeksiyle range-scan'dir, `%q%` (contains) ise
indeks kullanamayıp full scan olur — bilinçli olarak desteklenmez. Ofiste
yapılacaklar: tablo/kolon adını env'e yaz + DBA'dan
`CREATE INDEX ... ON <tablo>(UPPER(<isim kolonu>))` iste; indeks alınamazsa
alternatif, gece işiyle (id, isim) kolonlarının kişisel şemaya kopyalanıp
indekslenmesi (kardeş job deseni). Contains/fuzzy arama gerekirse Oracle
Text (CTXSYS.CONTEXT) ayrı karar konusudur.

Ofis koşu sırası (ilk kurulum): `fi_desk_schema.py` → `fi_desk_users.py` →
`fi_desk_lookup_import.py` → `ldap_admins.py` (IS_ADMIN bayrağı) →
`seed_fi_expert.py` → `fi_desk_dashboards.py`. Sonrasında günlük bakım
Yönetim ekranından (§5b); users/lookup_import yalnız toplu yenileme
gerektiğinde tekrar koşulur.

## 8. Açık konular

- **USD eqv. kur kaynağı ve tarihi** (offer date mi value date mi; EDW kur
  tablosu hangisi) — kullanıcı öğrenecek; şimdilik elle giriş.
- **Müşteri tablosu kimliği** (Importer typeahead'i için): EDW'deki 32M
  satırlık müşteri tablosunun adı + isim kolonu netleşince
  `FI_DESK_CUSTOMER_TABLE` / `FI_DESK_CUSTOMER_NAME_COL` env'lerine
  yazılacak ve `UPPER(isim)` fonksiyon indeksi istenecek (§7b "Importer
  araması"). O güne kadar arama FI_LU_LIST IMPORTER fallback'inde.
- **Mapping excelleri** (banka/ülke/region/group, importer/exporter, base
  rate tam listesi) — gelince lookup yükleme script'i yazılacak; mevcut
  seed yer tutucu.
- **Fee'nin yıllara bölünmesi** raporlama katmanının işi (ham fee saklanır).
- Fiduciary'de RATE_TYPE zorunlu ama floating alanları üründe yok — masa
  pratikte fixed girer; formda Floating seçilirse doğrulama uyarır.
