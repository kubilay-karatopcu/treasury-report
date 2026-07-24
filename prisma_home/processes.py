"""Süreç kayıt defteri — uzman ``bound_content.processes`` id'lerini sayfalara çözer.

Faz P0 (docs/DASHBOARD_ADAPTATION_PLAN.md §6.5): manuel panolar ayrı bir
"Panolar" menüsü yerine uzmanın "Süreçler" bölümünde listelenir. Uzman
YAML'ında yalnız string id durur (Atölye form round-trip'i string listesi
bekler — routes_library._form_to_expert_dict); id → sayfa eşlemesi burada,
kodda yaşar. Modül izolasyonu: mevduat_panel import EDİLMEZ — endpoint adı
string olarak çözülür, modül kayıtlı değilse süreç sessizce gizlenir
(landing'in eski MEVDUAT_PANEL_ENABLED korumalı kart deseniyle aynı sözleşme).

Süreç Düzenlileştirme (docs/PROCESS_REGULARIZATION_PLAN.md): her süreç girdisi
artık ``source_kind`` + ``documentation`` (BlockDocumentation dili) + ``blocks``
(``kind:"custom"`` bileşen descriptor'ları) taşır. Böylece custom süreçler
kütüphanede "Süreçler" altında listelenir ve dökümante edilebilir hale gelir.
``PROCESS_REGISTRY`` bugün descriptor'ın tek kaynağıdır; versiyonlu store D1'de
gelecek (backward-compat: store yoksa bu dict okunur).
"""
from __future__ import annotations

import logging

from flask import current_app, url_for
from werkzeug.routing import BuildError

log = logging.getLogger(__name__)

_EP = "mevduat_panel.index"
_FLAG = "MEVDUAT_PANEL_ENABLED"


def _cblock(bid: str, title: str, page: str, anchor: str | None, purpose: str,
            *, business_context: str = "", decision_support: str = "",
            known_limitations: str = "") -> dict:
    """``kind:"custom"`` bileşen descriptor'ı (docs/PROCESS_REGULARIZATION_PLAN §2.2).

    Panonun interaktif bir bileşenini yeniden yazmadan "blok" olarak temsil eder;
    render hedefi SPA sayfası/anchor'ıdır, dökümantasyon dört alanla taşınır."""
    return {
        "id": bid,
        "title": title,
        "kind": "custom",
        "custom_render": {"endpoint": _EP, "page": page, "anchor": anchor},
        "documentation": {
            "purpose": purpose,
            "business_context": business_context or None,
            "decision_support": decision_support or None,
            "known_limitations": known_limitations or None,
        },
    }


#: id → süreç tanımı. ``page`` mevduat panel SPA'sının ?page= deep-link'i
#: (mevduat_panel.js boot'u sidebar'daki data-page id'lerine karşı doğrular).
PROCESS_REGISTRY: dict[str, dict] = {
    "mevduat.maliyet": {
        "label": "Outstanding Cost Analysis",
        "desc": "Monthly averages & daily evolution · bubble · rate heatmap",
        "endpoint": _EP, "page": "cost-analysis", "config_flag": _FLAG,
        "source_kind": "custom", "owner": "A16438",
        "documentation": {
            "purpose": "TL mevduat stoğunun ağırlıklı ortalama maliyetini (faiz) "
                       "aylık ortalama ve günlük evrim ekseninde izler; maliyetin "
                       "hangi ürün/segment/vade kırılımından geldiğini ayrıştırır.",
            "business_context": "Fonlama maliyeti hazine NIM'inin en oynak "
                       "kalemi. Bu süreç, maliyet artışının kampanya mı, mix "
                       "kayması mı, yoksa piyasa faizi mi kaynaklı olduğunu "
                       "waterfall ayrıştırmasıyla gösterir.",
            "decision_support": "Fiyatlama komitesine 'maliyet nereden bozuldu' "
                       "sorusunun kırılımlı cevabını verir; segment/vade bazında "
                       "aksiyon önceliklendirir.",
            "known_limitations": "Etkileşimli drill ve bubble split/merge SPA'ya "
                       "özgüdür; veri kaynağı bugün canlı Oracle sorgularıdır "
                       "(ETL mart devri docs/PROCESS_REGULARIZATION_PLAN D3'te).",
        },
        "blocks": [
            _cblock("camon_wf", "Deposit Rate Waterfall", "cost-analysis",
                    "acc-btn-ca-mon-wf",
                    "Dönem faiz maliyetini başlangıç→bitiş arasında bileşenlere "
                    "ayıran kümülatif waterfall (Detay Boyutu'na göre kırılır).",
                    business_context="Maliyet değişiminin fiyat etkisi mi (aynı "
                    "ürünün faizi değişti) mix etkisi mi (pahalı ürüne kayış) "
                    "olduğu, fiyatlama aksiyonunun hedefini belirler.",
                    decision_support="Yorum kuralı: en büyük pozitif çubuk maliyet "
                    "artışının ana sürükleyicisidir; fiyat kaynaklıysa oran "
                    "aksiyonu, mix kaynaklıysa ürün yönlendirmesi gerekir.",
                    known_limitations="Çubuklar seçili tarih aralığına ve aktif "
                    "boyut/filtre setine bağlıdır; tekil büyük müşteri hareketleri "
                    "çubuğu domine edebilir (drill ile doğrulanmalı)."),
            _cblock("camon_bubble", "Cost Bubble — Balance × Rate", "cost-analysis",
                    "ca-mon-bub-bal",
                    "Ürün×vade baloncuklarında bakiye (boyut) ile faiz (eksen) "
                    "ilişkisi; merge hafızalı chip filtresiyle gruplanır.",
                    business_context="Fonlamanın nerede pahalı yoğunlaştığını "
                    "gösterir: büyük VE yüksek-faizli baloncuk = en maliyetli küme.",
                    decision_support="Yorum kuralı: sağ-üstteki (yüksek faiz) "
                    "büyük baloncuklar yeniden fiyatlama adayıdır; küçük ama çok "
                    "sayıda yüksek-faizli baloncuk kampanya taramasını işaret eder.",
                    known_limitations="Split/merge animasyonu ve seçim etkileşimi "
                    "standart blok render'ında yok; baloncuk konumu ağırlıklı "
                    "ortalamadır, uç değerleri gizleyebilir."),
            _cblock("camon_ratehm", "Interest Rate Heatmap", "cost-analysis",
                    "acc-btn-ca-mon-rate-hm",
                    "Ayrıştırma × İkinci Boyut matrisinde faiz Δ/seviyesi ısı "
                    "haritası; hücre drill'i satır seviyesine iner.",
                    business_context="Faiz değişiminin hangi segment×boyut "
                    "kesişiminde yoğunlaştığını tek matriste gösterir.",
                    decision_support="Yorum kuralı: koyu (yüksek Δ) hücreler "
                    "anomali adayıdır; satır/sütun boyunca yayılmışsa yapısal, "
                    "tekil hücreyse müşteri-özel harekettir.",
                    known_limitations="Δ modu iki tarih arasındaki farktır — ara "
                    "dönem salınımını göstermez; seyrek hücrelerde küçük bakiye "
                    "büyük Δ üretebilir."),
        ],
    },
    "mevduat.bakiye": {
        "label": "Outstanding Balance Analysis",
        "desc": "Balance bridge · balance/customer heatmap · composition",
        "endpoint": _EP, "page": "balance-analysis", "config_flag": _FLAG,
        "source_kind": "custom", "owner": "A16438",
        "documentation": {
            "purpose": "TL mevduat stok bakiyesinin dönem içi köprüsünü ve "
                       "segment/müşteri kompozisyonunu izler.",
            "business_context": "Bakiye büyümesinin kaynağı (yeni müşteri, mevcut "
                       "müşteri derinleşmesi, kampanya) fonlama sürdürülebilirliği "
                       "için kritiktir.",
            "decision_support": "Büyümenin sağlıklı mı (yaygın) yoksa kırılgan mı "
                       "(yoğunlaşmış) olduğunu bakiye/müşteri heatmap'iyle gösterir.",
            "known_limitations": "Hover-linked heatmap ve kompozisyon drill'i "
                       "SPA etkileşimidir.",
        },
        "blocks": [
            _cblock("bamon_bridge", "Balance Bridge", "balance-analysis",
                    "acc-btn-ba-mon-bridge",
                    "Bakiye değişimini başlangıç→bitiş bileşenlerine ayıran köprü.",
                    business_context="Büyümenin kaynağını (hangi segment/ürün "
                    "ekledi, hangisi kaybetti) net değişimden ayrıştırır.",
                    decision_support="Yorum kuralı: net büyüme pozitifken büyük "
                    "negatif çubuk varsa churn maskeleniyor demektir — o segmente "
                    "tutundurma aksiyonu gerekir.",
                    known_limitations="Köprü nominal TL'dir; kur ve enflasyon "
                    "etkisi ayrıştırılmaz."),
            _cblock("bamon_heatmap", "Balance / Customer Heatmap", "balance-analysis",
                    "acc-btn-ba-mon-heatmap",
                    "Bakiye veya müşteri sayısı metriğiyle segment×boyut ısı "
                    "haritası (metrik slider'ıyla seçilir).",
                    business_context="Bakiye ve müşteri sayısı birlikte okununca "
                    "büyümenin yaygın mı (çok müşteri) yoğunlaşmış mı (az büyük "
                    "müşteri) olduğu görülür.",
                    decision_support="Yorum kuralı: bakiye artıp müşteri sayısı "
                    "düşen hücre yoğunlaşma riskidir; ikisi birlikte artıyorsa "
                    "sağlıklı büyümedir.",
                    known_limitations="Hover-bağlı ikiz heatmap etkileşimi SPA'ya "
                    "özgüdür; iki metrik farklı ölçeklerde renklenir."),
        ],
    },
    "mevduat.vade": {
        "label": "Outstanding Tenor Analysis",
        "desc": "Tenor ladder · WAT · term-structure curve · swap hedge",
        "endpoint": _EP, "page": "tenor-analysis", "config_flag": _FLAG,
        "source_kind": "custom", "owner": "A16438",
        "documentation": {
            "purpose": "Mevduat stoğunun vade yapısını (ladder), ağırlıklı "
                       "ortalama vadeyi (WAT) ve swap hedge örtüşmesini izler.",
            "business_context": "Vade uyumsuzluğu likidite ve faiz riskinin "
                       "kaynağıdır; hedge kararları buradan beslenir.",
            "decision_support": "Vade merdiveni + swap örtüsüyle net açık vade "
                       "pozisyonunu görünür kılar.",
            "known_limitations": "Maturity ladder ve vade yapısı eğrisi Plotly "
                       "etkileşimli bileşenlerdir.",
        },
        "blocks": [
            _cblock("tamon_ladder", "Balance vs Hedge Ladder", "tenor-analysis",
                    "acc-btn-ta-mon-ladder",
                    "Vade kovalarında bakiye vs swap hedge merdiveni + Δ bakiye.",
                    business_context="Mevduatın vade dağılımı ile hedge örtüsü "
                    "arasındaki açık, faiz ve likidite riskinin kaynağıdır.",
                    decision_support="Yorum kuralı: bakiyenin hedge'i belirgin "
                    "aştığı kova net açık pozisyondur — swap/vade uzatma aksiyonu "
                    "önceliği oraya verilir.",
                    known_limitations="Merdiven anlık stok fotoğrafıdır; dönüş "
                    "(rollover) davranışı varsayılmaz, Weekly Rollovers ile "
                    "birlikte okunmalıdır."),
            _cblock("tamon_curve", "Term-Structure Curve", "tenor-analysis",
                    "acc-btn-ta-mon-curve",
                    "Vade yapısı boyunca ağırlıklı ortalama faiz eğrisi.",
                    business_context="Bankanın kendi mevduat verim eğrisi — vade "
                    "uzadıkça ödenen prim fiyatlama disiplinini gösterir.",
                    decision_support="Yorum kuralı: eğride ters eğim ya da tümsek "
                    "(kısa vadeye uzundan fazla ödeme) fiyatlama anomalisidir; "
                    "kampanya/istisnaların taranmasını tetikler.",
                    known_limitations="Seyrek kovalar eğriyi oynatabilir; eğri "
                    "yeni üretimi değil stoğu yansıtır."),
        ],
    },
    "mevduat.donusler": {
        "label": "Weekly Deposit Rollovers",
        "desc": "Rollover tables · segment breakdown · customer drill",
        "endpoint": _EP, "page": "weekly-report", "config_flag": _FLAG,
        "source_kind": "custom", "owner": "A16438",
        "documentation": {
            "purpose": "Haftalık vadeli mevduat dönüşlerini (rollover) AUM bandı, "
                       "para birimi ve müşteri tipi kırılımında izler; hücre "
                       "drill'iyle müşteri seviyesine iner.",
            "business_context": "Dönüş haftası, yeniden fiyatlama fırsatı ve "
                       "çıkış riskinin yoğunlaştığı andır.",
            "decision_support": "Hangi band/segmentte dönüş yığıldığını ve "
                       "kaçının elde tutulduğunu gösterir; proaktif temas için "
                       "müşteri listesi çıkarır.",
            "known_limitations": "Hücre çift-tık drill modalı ve numaralı tablo "
                       "başlıkları SPA'ya özgüdür.",
        },
        "blocks": [
            _cblock("wr_rollovers", "Weekly Rollovers Table", "weekly-report",
                    "wr-grid-1",
                    "AUM bandı × tarih matrisinde haftalık dönüş tutarları "
                    "(mio TRY); hücre → müşteri drill.",
                    business_context="Dönüş günü hem yeniden fiyatlama fırsatı "
                    "hem çıkış riskidir; hacmin hangi band/güne yığıldığı haftanın "
                    "operasyon planını belirler.",
                    decision_support="Yorum kuralı: büyük bandlarda (200M+) "
                    "yoğunlaşan dönüş günleri proaktif müşteri teması ister; "
                    "hücre drill'i temas listesini verir.",
                    known_limitations="Tutarlar sözleşme vadesine göredir; erken "
                    "kapama/kısmi çekim öngörülmez."),
            _cblock("wr_dtm", "Maturity Bucket Distribution", "weekly-report",
                    "wr-s1-dtm",
                    "Bakiye bazında vade kovası dağılımı histogramı.",
                    business_context="Dönen hacmin hangi yeni vadeye yazıldığı, "
                    "ortalama vadenin yönünü (uzama/kısalma) gösterir.",
                    decision_support="Yorum kuralı: dağılım kısa kovalara "
                    "kayıyorsa fonlama kısalıyor demektir — vade teşviki "
                    "değerlendirilir.",
                    known_limitations="Histogram seçili tarih aralığının "
                    "toplamıdır; hafta içi kompozisyon değişimi görünmez."),
        ],
    },
    "mevduat.yeni_uretim": {
        "label": "New Production — Volume & Pricing",
        "desc": "Rate-volume heatmap · AUM combo · pricing curve",
        "endpoint": _EP, "page": "np-volume-pricing", "config_flag": _FLAG,
        "source_kind": "custom", "owner": "A16438",
        "documentation": {
            "purpose": "Yeni üretilen (booked) mevduatın faiz × hacim dağılımını "
                       "ve fiyatlama eğrisini izler; hücre üstünde gezerek zaman "
                       "serisi combo'sunu tazeler.",
            "business_context": "Yeni iş fiyatlaması, stok maliyetini yarının "
                       "yönüne çeken kaldıraçtır.",
            "decision_support": "Hangi faiz/hacim bölgesinde yoğunlaşma olduğunu "
                       "ve fiyatlamanın piyasaya göre nerede durduğunu gösterir.",
            "known_limitations": "Hover-linked heatmap + hücre çift-tık müşteri "
                       "drill modalı SPA etkileşimidir.",
        },
        "blocks": [
            _cblock("np_rvhm", "Rate × Volume Heatmap", "np-volume-pricing",
                    "np-rvhm-wrap",
                    "Faiz × kümülatif hacim ısı haritası; hover → 'Cell history' "
                    "combo + matris, çift-tık → müşteri drill.",
                    business_context="Yeni işin kanal×AUM kesişiminde hangi "
                    "faizden yazıldığını gösterir — yarının stok maliyetinin "
                    "erken göstergesi.",
                    decision_support="Yorum kuralı: koyu (yüksek Δ) hücreler "
                    "fiyat artışının yazıldığı yerdir; yüksek faiz + yüksek hacim "
                    "hücresi marj baskısının kaynağıdır, istisna onay listesiyle "
                    "karşılaştırılır.",
                    known_limitations="Yalnız yeni üretim (booked) — stok "
                    "yeniden fiyatlaması burada görünmez; hücre değeri ağırlıklı "
                    "ortalamadır."),
            _cblock("np_aumcombo", "AUM Volume & Rate Combo", "np-volume-pricing",
                    None,
                    "AUM bandı bazında hacim (bar) + ağırlıklı faiz (line) combo.",
                    business_context="Band büyüklüğü ile ödenen faiz ilişkisi "
                    "fiyatlama merdiveninin (büyüğe daha çok) çalışıp "
                    "çalışmadığını gösterir.",
                    decision_support="Yorum kuralı: küçük banda büyük banddan "
                    "yüksek faiz ödeniyorsa merdiven bozulmuştur — kampanya "
                    "sızıntısı aranır.",
                    known_limitations="Bandlar arası geçişkenlik (müşterinin "
                    "band değiştirmesi) seri kırılması yaratabilir."),
        ],
    },
    "mevduat.sektor": {
        "label": "Sector Comparison",
        "desc": "BDDK/TCMB rates · sector outstanding · mix attribution",
        "endpoint": _EP, "page": "sector-comparison", "config_flag": _FLAG,
        "source_kind": "custom", "owner": "A16438",
        "documentation": {
            "purpose": "Banka mevduat faiz/stok göstergelerini BDDK ve TCMB "
                       "sektör verileriyle karşılaştırır; mix attribution ile "
                       "banka-sektör faiz farkını bileşenlere ayırır.",
            "business_context": "Rekabet konumu ve piyasa payı kararları sektöre "
                       "göreli duruştan beslenir.",
            "decision_support": "Bankanın sektöre göre pahalı/ucuz fonlandığını ve "
                       "farkın mix mi fiyat mı kaynaklı olduğunu gösterir.",
            "known_limitations": "Sektör verisi BDDK/TCMB yayın takvimine bağlıdır "
                       "(gecikmeli olabilir).",
        },
        "blocks": [
            _cblock("sec_mix", "Maturity Mix — Bank vs Sector", "sector-comparison",
                    None,
                    "Banka-mix TCMB ile sektör-mix TCMB arasındaki farkın vade "
                    "kovası bazında attribution'ı.",
                    business_context="Banka-sektör faiz farkının 'pahalı mı "
                    "fonlanıyoruz yoksa vade mixi mi farklı' sorusunu ayrıştırır.",
                    decision_support="Yorum kuralı: fark mix kaynaklıysa fiyat "
                    "değil vade stratejisi tartışılır; fiyat kaynaklıysa banda/"
                    "kanala inen fiyatlama gözden geçirilir.",
                    known_limitations="BDDK/TCMB verisi yayın takvimiyle gecikir; "
                    "sektör ortalaması banka kompozisyon farklarını düzler."),
        ],
    },
    "mevduat.bsc": {
        "label": "BSC Presentation",
        "desc": "Full-screen presentation mode · deposit & sector slide set",
        "endpoint": _EP, "page": "bsc-presentation", "config_flag": _FLAG,
        "source_kind": "custom", "owner": "A16438",
        "documentation": {
            "purpose": "Mevduat ve sektör panolarının seçili görünümlerini tam "
                       "ekran sunum modunda slide seti olarak sunar.",
            "business_context": "Komite/yönetim sunumları için panolardan derlenen "
                       "hazır anlatı.",
            "decision_support": "Analiz ekranlarını karar toplantısına taşınabilir "
                       "sunum diline çevirir.",
            "known_limitations": "BSC sunum kabuğu ve slide geçişleri SPA'ya "
                       "özgüdür; snapshot/paylaşım yolu D4'te gelecek.",
        },
        "blocks": [],
    },
}


def resolve_processes(process_ids: list[str] | None) -> list[dict]:
    """Uzmanın süreç id listesini render edilebilir kartlara çözer.

    Bilinmeyen id, kapalı config bayrağı veya kayıtlı olmayan endpoint →
    süreç listeden düşer (uzman sayfası hata vermez); bilinmeyen id ayrıca
    loglanır ki YAML yazım hatası sessiz kalmasın.
    """
    out: list[dict] = []
    for pid in process_ids or []:
        meta = PROCESS_REGISTRY.get(pid)
        if meta is None:
            log.warning("bilinmeyen süreç id'si atlandı: %r", pid)
            continue
        flag = meta.get("config_flag")
        if flag and not current_app.config.get(flag):
            continue
        try:
            kwargs = {"page": meta["page"]} if meta.get("page") else {}
            url = url_for(meta["endpoint"], **kwargs)
        except BuildError:
            continue
        out.append({
            "id": pid,
            "num": f"{len(out) + 1:02d}",
            "label": meta["label"],
            "desc": meta.get("desc", ""),
            "url": url,
            "documented": _is_documented(meta),
        })
    return out


# ── Süreç Düzenlileştirme: kütüphane listeleme + dökümantasyon ─────────────

_DOC_FIELDS = ("purpose", "business_context", "decision_support", "known_limitations")


def _is_documented(meta: dict) -> bool:
    """Süreç 'documented' sayılır mı? — en az ``purpose`` dolu olmalı."""
    doc = meta.get("documentation") or {}
    return bool((doc.get("purpose") or "").strip())


def _doc_filled_count(doc: dict | None) -> int:
    doc = doc or {}
    return sum(1 for f in _DOC_FIELDS if (doc.get(f) or "").strip())


def _safe_url(endpoint: str | None, page: str | None) -> str | None:
    """endpoint → URL; blueprint kayıtlı değilse (BuildError) None döner."""
    if not endpoint:
        return None
    try:
        return url_for(endpoint, **({"page": page} if page else {}))
    except BuildError:
        return None


def _snapshot_url(sid: str) -> str | None:
    try:
        return url_for("presentations.view_snapshot", sid=sid)
    except BuildError:
        return None


def _load_overlay(pid: str) -> dict | None:
    """W1: PROCESS_STORE'daki dökümantasyon overlay'i (kullanıcı metni).

    Store yoksa/hata verirse None — registry seed'i tek başına kullanılır
    (purely additive; store'suz ortam eski davranışta)."""
    store = current_app.config.get("PROCESS_STORE")
    if store is None:
        return None
    try:
        return store.load_latest(pid)
    except Exception:
        log.exception("process overlay okunamadı: %s", pid)
        return None


def _merged_doc(meta: dict, overlay: dict | None) -> dict:
    """Süreç dökümantasyonu: overlay'de dolu alan seed'i (registry) ezer."""
    seed = meta.get("documentation") or {}
    ov = (overlay or {}).get("documentation") or {}
    return {f: (ov.get(f) or seed.get(f)) for f in _DOC_FIELDS}


def _merged_block_doc(block: dict, overlay: dict | None) -> dict:
    seed = block.get("documentation") or {}
    ov = ((overlay or {}).get("blocks_documentation") or {}).get(block.get("id"), {})
    return {f: (ov.get(f) or seed.get(f)) for f in _DOC_FIELDS}


def list_processes() -> list[dict]:
    """Kütüphane 'Süreçler' listesi için tüm kayıtlı süreçlerin özeti.

    Config bayrağına bakılmaz (kütüphane, modül kapalı olsa da süreci
    dökümantasyon amacıyla gösterir); ``enabled`` alanı bayrağı yansıtır.
    W1: store'daki dökümantasyon overlay'i seed'in üstüne bindirilir.
    """
    out: list[dict] = []
    for i, (pid, meta) in enumerate(PROCESS_REGISTRY.items(), start=1):
        flag = meta.get("config_flag")
        overlay = _load_overlay(pid)
        doc = _merged_doc(meta, overlay)
        out.append({
            "id": pid,
            "num": f"{i:02d}",
            "label": meta.get("label", pid),
            "desc": meta.get("desc", ""),
            "source_kind": meta.get("source_kind", "custom"),
            "owner": meta.get("owner", ""),
            "block_count": len(meta.get("blocks") or []),
            "documented": bool((doc.get("purpose") or "").strip()),
            "doc_fields": _doc_filled_count(doc),
            "doc_version": (overlay or {}).get("version"),
            "enabled": bool(current_app.config.get(flag)) if flag else True,
        })
    return out


def list_pipeline_processes() -> list[dict]:
    """W2: yayınlanmış sunumlar (uzmana bağlı snapshot'lar) = pipeline süreçleri.

    'Her şey bir süreçtir' kararı (plan §3.5 W2): eski Snapshot'lar sayfası
    kaldırıldı; yayınlar Süreçler kataloğunda ``pipeline`` rozetiyle custom
    süreçlerin yanında listelenir. Kart, süreç görünümüne (view_snapshot —
    W2b'de 'süreç görünümü' olarak yeniden adlandırılacak) gider.
    Store yoksa/hata verirse boş liste (katalog custom'larla yaşar)."""
    store = current_app.config.get("SNAPSHOT_STORE")
    if store is None:
        return []
    try:
        metas = [m for m in (store.list_all_meta() or []) if m.get("bound_experts")]
    except Exception:
        log.exception("pipeline süreç listesi: snapshot meta okunamadı")
        return []
    metas.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    out = []
    for m in metas:
        sid = m.get("snapshot_id")
        if not sid:
            continue
        out.append({
            "id": f"pipeline.{sid}",
            "num": "",                      # numara katalogda birleşik atanır
            "label": m.get("title") or "Başlıksız yayın",
            "desc": m.get("description") or "",
            "source_kind": "pipeline",
            "owner": m.get("owner_id", ""),
            "block_count": None,
            "documented": False,            # pipeline doc ekranı W2b/W4
            "doc_fields": 0,
            "enabled": True,
            "href": _snapshot_url(sid),
            "created_at": m.get("created_at", ""),
        })
    return out


def list_component_block_summaries(
    *, team: str | None = None, tag: str | None = None,
    viz_type: str | None = None, search: str | None = None,
) -> list[dict]:
    """W1: süreçlerin ``kind:"custom"`` bileşen bloklarını Bloklar kütüphanesi
    listesine ek satır olarak üretir (BlockSummary.to_dict şekli + ``custom_href``).

    BLOCK_STORE'a KOPYALANMAZ (drift yok) — liste birleştirme (listing-merge,
    plan §3.5 W1). Kart tıklaması ``custom_href`` ile süreç detayına gider.
    Filtre semantiği blok store'unkiyle hizalı (team substring, tag üyelik,
    viz_type eşitlik, q başlık/açıklama/tag araması).
    """
    if viz_type and viz_type != "custom":
        return []
    out: list[dict] = []
    for pid, meta in PROCESS_REGISTRY.items():
        p_team = pid.split(".", 1)[0]
        if team and team.lower() not in p_team:
            continue
        overlay = _load_overlay(pid)
        for b in meta.get("blocks") or []:
            bdoc = _merged_block_doc(b, overlay)
            tags = ["custom", pid]
            if tag and tag not in tags:
                continue
            title = b.get("title", b.get("id", ""))
            desc = bdoc.get("purpose") or ""
            if search:
                hay = " ".join([title, desc, " ".join(tags)]).lower()
                if search.lower() not in hay:
                    continue
            out.append({
                "team": p_team,
                "id": b.get("id", ""),
                "version": (overlay or {}).get("version") or 1,
                "title": title,
                "description": desc,
                "tags": tags,
                "visualization_type": "custom",
                "owner": meta.get("owner", ""),
                "created_at": "",
                "updated_at": (overlay or {}).get("updated_at"),
                "deprecated": False,
                # Blok kartı bloğa ODAKLI açılır (?blok=): başlıkta bloğun kendi
                # adı görünür (süreç adı değil — kullanıcı geri bildirimi).
                "custom_href": url_for("presentations.atolye_surec_detay",
                                       pid=pid, blok=b.get("id")),
            })
    return out


def get_process(pid: str) -> dict | None:
    """Detay/dökümantasyon ekranı için tek sürecin tam descriptor'ı (+ türev
    alanlar). Bilinmeyen id → None."""
    meta = PROCESS_REGISTRY.get(pid)
    if meta is None:
        return None
    overlay = _load_overlay(pid)
    doc = _merged_doc(meta, overlay)
    blocks = []
    for b in meta.get("blocks") or []:
        bdoc = _merged_block_doc(b, overlay)
        cr = b.get("custom_render") or {}
        blocks.append({
            **b,
            "documentation": bdoc,
            "documented": bool((bdoc.get("purpose") or "").strip()),
            "doc_fields": _doc_filled_count(bdoc),
            # Render hedefi URL'i burada güvenle çözülür: mevduat_panel blueprint
            # kayıtlı değilse BuildError yutulur (template url_for'da patlamasın).
            "render_url": _safe_url(cr.get("endpoint"), cr.get("page")),
        })
    return {
        "id": pid,
        "label": meta.get("label", pid),
        "desc": meta.get("desc", ""),
        "source_kind": meta.get("source_kind", "custom"),
        "owner": meta.get("owner", ""),
        "documentation": doc,
        "documented": bool((doc.get("purpose") or "").strip()),
        "doc_fields": _doc_filled_count(doc),
        "doc_version": (overlay or {}).get("version"),
        "doc_updated_by": (overlay or {}).get("updated_by"),
        "doc_updated_at": (overlay or {}).get("updated_at"),
        "blocks": blocks,
        "page": meta.get("page"),
        "endpoint": meta.get("endpoint"),
        "enabled": bool(current_app.config.get(meta.get("config_flag")))
                   if meta.get("config_flag") else True,
    }
