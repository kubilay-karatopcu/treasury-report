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
                    decision_support="Maliyet değişiminin sürükleyici kırılımını "
                    "tek bakışta verir."),
            _cblock("camon_bubble", "Cost Bubble — Balance × Rate", "cost-analysis",
                    "ca-mon-bub-bal",
                    "Ürün×vade baloncuklarında bakiye (boyut) ile faiz (eksen) "
                    "ilişkisi; merge hafızalı chip filtresiyle gruplanır.",
                    known_limitations="Split/merge animasyonu ve seçim etkileşimi "
                    "standart blok render'ında yok."),
            _cblock("camon_ratehm", "Interest Rate Heatmap", "cost-analysis",
                    "ca-mon-rate-hm",
                    "Ayrıştırma × İkinci Boyut matrisinde faiz Δ/seviyesi ısı "
                    "haritası; hücre drill'i satır seviyesine iner."),
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
                    "Bakiye değişimini başlangıç→bitiş bileşenlerine ayıran köprü."),
            _cblock("bamon_heatmap", "Balance / Customer Heatmap", "balance-analysis",
                    "acc-btn-ba-mon-heatmap",
                    "Bakiye veya müşteri sayısı metriğiyle segment×boyut ısı "
                    "haritası (metrik slider'ıyla seçilir)."),
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
                    "Vade kovalarında bakiye vs swap hedge merdiveni + Δ bakiye."),
            _cblock("tamon_curve", "Term-Structure Curve", "tenor-analysis",
                    "acc-btn-ta-mon-curve",
                    "Vade yapısı boyunca ağırlıklı ortalama faiz eğrisi."),
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
                    "(mio TRY); hücre → müşteri drill."),
            _cblock("wr_dtm", "Maturity Bucket Distribution", "weekly-report",
                    "wr-s1-dtm",
                    "Bakiye bazında vade kovası dağılımı histogramı."),
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
                    "combo + matris, çift-tık → müşteri drill."),
            _cblock("np_aumcombo", "AUM Volume & Rate Combo", "np-volume-pricing",
                    None,
                    "AUM bandı bazında hacim (bar) + ağırlıklı faiz (line) combo."),
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
                    "kovası bazında attribution'ı."),
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


def list_processes() -> list[dict]:
    """Kütüphane 'Süreçler' listesi için tüm kayıtlı süreçlerin özeti.

    Config bayrağına bakılmaz (kütüphane, modül kapalı olsa da süreci
    dökümantasyon amacıyla gösterir); ``enabled`` alanı bayrağı yansıtır.
    """
    out: list[dict] = []
    for i, (pid, meta) in enumerate(PROCESS_REGISTRY.items(), start=1):
        flag = meta.get("config_flag")
        out.append({
            "id": pid,
            "num": f"{i:02d}",
            "label": meta.get("label", pid),
            "desc": meta.get("desc", ""),
            "source_kind": meta.get("source_kind", "custom"),
            "owner": meta.get("owner", ""),
            "block_count": len(meta.get("blocks") or []),
            "documented": _is_documented(meta),
            "doc_fields": _doc_filled_count(meta.get("documentation")),
            "enabled": bool(current_app.config.get(flag)) if flag else True,
        })
    return out


def get_process(pid: str) -> dict | None:
    """Detay/dökümantasyon ekranı için tek sürecin tam descriptor'ı (+ türev
    alanlar). Bilinmeyen id → None."""
    meta = PROCESS_REGISTRY.get(pid)
    if meta is None:
        return None
    doc = meta.get("documentation") or {}
    blocks = []
    for b in meta.get("blocks") or []:
        bdoc = b.get("documentation") or {}
        cr = b.get("custom_render") or {}
        blocks.append({
            **b,
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
        "documentation": {f: doc.get(f) for f in _DOC_FIELDS},
        "documented": _is_documented(meta),
        "doc_fields": _doc_filled_count(doc),
        "blocks": blocks,
        "page": meta.get("page"),
        "endpoint": meta.get("endpoint"),
        "enabled": bool(current_app.config.get(meta.get("config_flag")))
                   if meta.get("config_flag") else True,
    }
