"""deposits_etl.py — Deposits dashboard'unun (NIM_calculation reposu) 5 sayfasını
besleyen plot-hazır DataFrame'leri günlük üreten headless ETL.

Amaç: görselleştirme Prisma'ya taşınırken TÜM finansal matematik ve ETL
NIM_calculation reposundaki mevcut motorlarda kalır (sayı sadakati orada test
edilmiş durumda). Bu script motorları Flask'sız çağırır, her sayfanın grafiğini
besleyen SON tidy DataFrame sınırında keser ve çıktıları parquet + tazelik
manifesti olarak yazar. Prisma tarafı (Sunum) bu tabloları dataset olarak okur;
apply-filters/plot-refresh yalnız bu veriyi çeker, hesap tetiklemez.

Kapsam — 5 Deposits sayfası ve çıktı tabloları:

  Sayfa                        Çıktı tablosu                    Gren
  ─────────────────────────    ─────────────────────────────    ─────────────────────────
  Outstanding Cost (aylık)  ┐  dep_outstanding_monthly          MONTH × 5 DIM_*
  Outstanding Balance (ay.) ┘                                   → BALANCE, WR_SUM, CUST_COUNT
  Outstanding Cost (günlük) ┐  dep_outstanding_daily            DAT × 5 DIM_*
  Outstanding Balance (gün.)│                                   → BALANCE, WR_SUM, CUST_COUNT
  rate_drill / balance_drill┘
  Outstanding Tenor (aylık)    dep_tenor_monthly                MONTH × MODE × BUCKET × 5 DIM_*
  Outstanding Tenor (günlük)   dep_tenor_daily                  DAT × MODE × BUCKET × 5 DIM_*
                                                                → BALANCE, WR_SUM, WT_SUM
  Future Deposit Rollings      dep_rollings_window_agg          ROLL_DATE × CCY × CUST_TP × AUM_BAND
                               dep_rollings_window_detail       hesap-roll seviyesi (FULL_NM maskeli)
  New Business                 np_flow_daily                    DAT × CCY × CUST_TP × PC × AUM ×
                                                                TENOR_GRP × SUB_SEGMENT
                                                                → NP_HACIM, YENI_PARA, OS_BAKIYE,
                                                                  WC_SUM, WT_SUM
                               np_outstanding_daily             DAT × CHANNEL × CUST_TP ×
                                                                AUM_COMMON × TENOR_COMMON
                                                                → BAL_SUM, WR_SUM

Tasarım kuralları:
  * Oranlar ASLA oran olarak yazılmaz — pay/payda ayrı taşınır (WR_SUM=Σ B·r,
    WT_SUM=Σ B·vade, WC_SUM=Σ B·r_bileşik). Tüketici hangi seviyede gruplarsa
    gruplasın ağırlıklı ortalamayı kendisi böler.
  * Weekly Rollings dışında hiçbir kaynak SQL'e tarih bind'i geçmez; motorlar
    tam geçmişi tek seferde yükler. Günlük cron = tam yeniden üretim
    (idempotent). Rollings ileriye dönük pencere ister (MTRTY_DT∈[s,e] AND
    VAL_DT<s) — pencere konvansiyonu: bugün → bugün+ROLLINGS_DAYS_AHEAD.
  * Bir extractor'ın hatası diğerlerini durdurmaz; exit code toplu durumu
    yansıtır (0=hepsi tamam, 1=en az biri düştü).
  * Her koşuda OUT_DIR/_freshness.json güncellenir: tablo başına satır sayısı,
    tarih aralığı, üretim zamanı, kaynak repo commit'i. Prisma'daki bayatlık
    rozeti bu manifesti okur.

Kullanım:
    python jobs/deposits_etl.py --nim-repo /path/to/NIM_calculation \
        [--out DIR] [--env DEVELOPMENT|PRODUCTION_DB] [--only tablo1,tablo2] \
        [--rollings-start DD/MM/YYYY --rollings-end DD/MM/YYYY]

    Ortam değişkenleri (CLI arg'ları önceliklidir):
        DEPOSITS_ETL_NIM_REPO, DEPOSITS_ETL_OUT, DEPOSITS_ETL_ENV

Cron örneği (ofis makinesi, her gün 06:30):
    30 6 * * * cd /opt/treasury-report && \
        DEPOSITS_ETL_NIM_REPO=/opt/NIM_calculation DEPOSITS_ETL_ENV=PRODUCTION_DB \
        python jobs/deposits_etl.py >> /var/log/deposits_etl.log 2>&1

OpenShift CronJob'da aynı komut bir Job container'ında koşar; OUT_DIR'i kalıcı
volume'a ya da S3 sync'lenen bir dizine verin.
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("deposits_etl")

# ── Sabitler ────────────────────────────────────────────────────────────────

# Outstanding snapshot'larının boyut kolonları (aylık ve günlük normalize
# cache'lerde ortak adlar — bkz. NIM app.py _DD_CACHE / _DAILY_DD_CACHE).
DIM_COLS = ["DIM_PRODUCT", "DIM_SUBPRODUCT", "DIM_CUSTOMER", "DIM_AUM", "DIM_SEGMENT"]

# Tenor modu → (bucket kolonu, vade-günü kolonu). Aylık cache TENOR_RATE /
# DTM_RATE adlarını, günlük cache AGIRLIKLI_ORT_TENOR / AGIRLIKLI_ORT_DTM
# adlarını taşır; ikisini de dener, bulduğunu kullanır.
TENOR_MODES = {
    "tenor": ("DIM_BUCKET", ["TENOR_RATE", "AGIRLIKLI_ORT_TENOR"]),
    "dtm":   ("DIM_BUCKET_DTM", ["DTM_RATE", "AGIRLIKLI_ORT_DTM"]),
}

DEFAULT_ROLLINGS_DAYS_AHEAD = 28


# ── NIM reposunu headless yükleme ───────────────────────────────────────────

def load_nim_modules(nim_repo: Path, env_override: str | None):
    """NIM reposunun motorlarını Flask çalıştırmadan import et.

    Sıralama önemli: config.ENV/QUERIES_DIR import ANINDA hesaplanıp
    engine.db_source tarafından modül-lokali olarak yakalanıyor — bu yüzden
    önce config patch'lenir, db_source ve app ondan SONRA import edilir.
    app.py import'u veri YÜKLEMEZ (tüm prewarm'lar __main__ guard'ının
    arkasında) ama Flask app objesini kurar; zararsız.

    Göreli yollar (data/dev.db, queries/, *.xlsx) nedeniyle CWD repo köküne
    alınır ve öyle kalır.
    """
    nim_repo = nim_repo.resolve()
    if not (nim_repo / "app.py").exists() or not (nim_repo / "engine").is_dir():
        raise SystemExit(f"--nim-repo geçersiz: {nim_repo} (app.py / engine/ yok)")

    os.chdir(nim_repo)
    sys.path.insert(0, str(nim_repo))

    nim_config = importlib.import_module("config")
    if env_override:
        nim_config.ENV = env_override
        nim_config.QUERIES_DIR = (
            "queries/dev" if env_override == "DEVELOPMENT" else "queries/prod"
        )

    db_source = importlib.import_module("engine.db_source")
    if env_override:
        # db_source config değerlerini import anında kopyalar — senkronla.
        db_source.ENV = nim_config.ENV
        db_source.QUERIES_DIR = nim_config.QUERIES_DIR

    np_agg = importlib.import_module("engine.np_agg")
    outstanding_daily = importlib.import_module("engine.outstanding_daily")

    log.info("NIM motorları import ediliyor (app.py ~7k satır, veri yüklemez)…")
    nim_app = importlib.import_module("app")

    return nim_app, np_agg, outstanding_daily, nim_config


def _nim_git_sha(nim_repo: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(nim_repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


# ── Ortak yardımcılar ───────────────────────────────────────────────────────

def _present(df, cols):
    return [c for c in cols if c in df.columns]


def _first_present(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _snapshot_agg(df, date_col: str):
    """Outstanding snapshot indirgeme: date × DIM_* → BALANCE, WR_SUM, CUST_COUNT.

    WR_SUM = Σ(BALANCE·INTEREST_RATE) — motorların waterfall/heatmap/bar
    builder'larının içindeki `_wr` ile aynı formül. Oran tüketicide
    WR_SUM/BALANCE olarak geri türetilir.
    """
    dims = _present(df, DIM_COLS)
    g = df.copy()
    g["WR_SUM"] = g["BALANCE"] * g["INTEREST_RATE"]
    agg_cols = {"BALANCE": "sum", "WR_SUM": "sum"}
    if "CUST_COUNT" in g.columns:
        agg_cols["CUST_COUNT"] = "sum"
    out = (
        g.groupby([date_col] + dims, dropna=False, observed=True)
        .agg(agg_cols)
        .reset_index()
    )
    return out


def _tenor_agg(df, date_col: str):
    """Tenor snapshot: date × MODE × DIM_BUCKET × DIM_* → BALANCE, WR_SUM, WT_SUM.

    İki mod (kalan vade = tenor, vadeye-kalan-gün = dtm) tek tabloda MODE
    kolonuyla istiflenir — motorların mode-swap'inin (`_apply_tenor_mode`)
    veri karşılığı.
    """
    import pandas as pd

    dims = _present(df, DIM_COLS)
    frames = []
    for mode, (bucket_col, wt_candidates) in TENOR_MODES.items():
        wt_col = _first_present(df, wt_candidates)
        if bucket_col not in df.columns or wt_col is None:
            log.warning("tenor: %s modu atlandı (kolon yok: %s/%s)",
                        mode, bucket_col, wt_candidates)
            continue
        g = df.copy()
        g["WR_SUM"] = g["BALANCE"] * g["INTEREST_RATE"]
        g["WT_SUM"] = g["BALANCE"] * g[wt_col].fillna(0.0)
        g = (
            g.groupby([date_col, bucket_col] + dims, dropna=False, observed=True)
            .agg({"BALANCE": "sum", "WR_SUM": "sum", "WT_SUM": "sum"})
            .reset_index()
            .rename(columns={bucket_col: "DIM_BUCKET"})
        )
        g.insert(1, "MODE", mode)
        frames.append(g)
    if not frames:
        raise RuntimeError("tenor: hiçbir mod üretilemedi")
    return pd.concat(frames, ignore_index=True)


# ── Extractor'lar ───────────────────────────────────────────────────────────
# Her extractor (ctx) → DataFrame döner. ctx: load_nim_modules çıktısı + args.

def _load_df(engine_cls):
    """Motorların _load()'u (df, dates) tuple'ı döner — yalnız df'i al."""
    out = engine_cls._load()
    return out[0] if isinstance(out, tuple) else out


def x_dep_outstanding_monthly(ctx):
    return _snapshot_agg(_load_df(ctx.nim_app.DepositDetailEngine), "MONTH")


def x_dep_outstanding_daily(ctx):
    return _snapshot_agg(_load_df(ctx.nim_app.DailyDepositEngine), "DAT")


def x_dep_tenor_monthly(ctx):
    return _tenor_agg(_load_df(ctx.nim_app.DepositDetailEngine), "MONTH")


def x_dep_tenor_daily(ctx):
    return _tenor_agg(_load_df(ctx.nim_app.DailyDepositEngine), "DAT")


def x_dep_rollings_window_agg(ctx):
    eng = ctx.nim_app.WeeklyRollingsEngine
    df = eng._load(ctx.rollings_start, ctx.rollings_end)
    out = df.copy()
    out.insert(0, "WINDOW_START", ctx.rollings_start)
    out.insert(1, "WINDOW_END", ctx.rollings_end)
    return out


def x_dep_rollings_window_detail(ctx):
    eng = ctx.nim_app.WeeklyRollingsEngine
    df = eng._load_full(ctx.rollings_start, ctx.rollings_end)
    out = df.copy()
    # PII: müşteri adını maskele — dashboard'un kendi maskesiyle aynı; helper
    # bulunamazsa kolonu tamamen düşür (asla düz metin yazma).
    if "FULL_NM" in out.columns:
        mask = getattr(ctx.nim_app, "_mask_full_nm", None)
        if callable(mask):
            out["FULL_NM"] = out["FULL_NM"].map(lambda v: mask(v) if v else v)
        else:
            out = out.drop(columns=["FULL_NM"])
    out.insert(0, "WINDOW_START", ctx.rollings_start)
    out.insert(1, "WINDOW_END", ctx.rollings_end)
    return out


def x_np_flow_daily(ctx):
    """New Business akışı: motorun iç şemasındaki tam-geçmiş df'in günlük
    indirgemesi. WC_SUM bileşik-oran payı = Σ(bileşik(NP_FAIZ, TENOR_DAYS)·hacim)
    — np_agg'ın pencere aggregation'ları ile aynı dönüşüm, aynı fonksiyonla.
    """
    np_agg = ctx.np_agg
    df = np_agg.load_np_data().copy()

    # Motorun _agg_window'u ile BİREBİR aynı dönüşüm (app.py ~L6119-6129):
    # NP_FAIZ iç şemada YÜZDE'dir; bileşik çevrim yüzde-vektörize fonksiyonla
    # yapılır, NaN satırlar (TENOR_DAYS<=0) toplamda doğal olarak düşer.
    to_compound = getattr(np_agg, "simple_to_compound_pct_series", None)
    if to_compound is None:
        raise RuntimeError("np_agg.simple_to_compound_pct_series bulunamadı — "
                           "ETL kesimi motor sürümüyle uyumsuz, WC_SUM üretilemez")
    df["_comp"] = to_compound(df["NP_FAIZ"], df["TENOR_DAYS"])
    df["WC_SUM"] = df["_comp"] * df["NP_HACIM"]
    df["WT_SUM"] = df["TENOR_DAYS"] * df["NP_HACIM"]

    dims = _present(df, ["CCY_CODE", "CUST_TP", "RELATED_PC", "AUM_BAND",
                         "TENOR_GRP", "SUB_SEGMENT"])
    measures = {c: "sum" for c in
                _present(df, ["NP_HACIM", "YENI_PARA", "OS_BAKIYE", "WC_SUM", "WT_SUM"])}
    return (
        df.groupby(["DAT"] + dims, dropna=False, observed=True)
        .agg(measures)
        .reset_index()
    )


def x_np_outstanding_daily(ctx):
    """Outstanding (stok) AS-OF serisi — NB heatmap/bubble'ın payda tarafı.
    OS tarafı point-in-time olduğu için gren günlük snapshot'tır; pencere
    seçimi tüketicide 'end gününün satırları' olarak çözülür.
    """
    od = ctx.outstanding_daily.load_outstanding_daily().copy()
    od["WR_SUM"] = od["OS_BAKIYE"] * od["OS_FAIZ"]
    dims = _present(od, ["CHANNEL", "CUST_TP", "AUM_COMMON", "TENOR_COMMON"])
    return (
        od.groupby(["DAT"] + dims, dropna=False, observed=True)
        .agg(BAL_SUM=("OS_BAKIYE", "sum"), WR_SUM=("WR_SUM", "sum"))
        .reset_index()
    )


EXTRACTORS = {
    "dep_outstanding_monthly":     x_dep_outstanding_monthly,
    "dep_outstanding_daily":       x_dep_outstanding_daily,
    "dep_tenor_monthly":           x_dep_tenor_monthly,
    "dep_tenor_daily":             x_dep_tenor_daily,
    "dep_rollings_window_agg":     x_dep_rollings_window_agg,
    "dep_rollings_window_detail":  x_dep_rollings_window_detail,
    "np_flow_daily":               x_np_flow_daily,
    "np_outstanding_daily":        x_np_outstanding_daily,
}


# ── Koşucu ──────────────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, nim_app, np_agg, outstanding_daily, rollings_start, rollings_end):
        self.nim_app = nim_app
        self.np_agg = np_agg
        self.outstanding_daily = outstanding_daily
        self.rollings_start = rollings_start
        self.rollings_end = rollings_end


def _date_range_of(df):
    """Tablodaki doğal tarih kolonunun (varsa) min/max'ı — tazelik manifesti için."""
    for c in ("DAT", "MONTH", "ROLL_DATE", "WINDOW_START"):
        if c in df.columns and len(df):
            s = df[c].astype(str)
            return {"column": c, "min": s.min(), "max": s.max()}
    return None


def run(args) -> int:
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    nim_app, np_agg, od, nim_config = load_nim_modules(Path(args.nim_repo), args.env)
    ctx = _Ctx(nim_app, np_agg, od, args.rollings_start, args.rollings_end)

    only = {t.strip() for t in args.only.split(",")} if args.only else None
    unknown = (only or set()) - set(EXTRACTORS)
    if unknown:
        raise SystemExit(f"--only bilinmeyen tablo(lar): {sorted(unknown)} "
                         f"(geçerli: {sorted(EXTRACTORS)})")

    manifest_path = out_dir / "_freshness.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    manifest.setdefault("tables", {})

    run_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    manifest["last_run_at"] = run_at
    manifest["env"] = getattr(nim_config, "ENV", "?")
    manifest["nim_repo"] = str(Path(args.nim_repo).resolve())
    manifest["nim_git_sha"] = _nim_git_sha(Path(args.nim_repo))
    manifest["rollings_window"] = {"start": args.rollings_start, "end": args.rollings_end}

    failures = []
    for name, fn in EXTRACTORS.items():
        if only and name not in only:
            continue
        log.info("── %s üretiliyor…", name)
        try:
            df = fn(ctx)
            if df is None:
                raise RuntimeError("extractor DataFrame döndürmedi")
            # Boş sonuç yalnız pencere-bağımlı rollings için meşru (sakin
            # pencere); snapshot/seri tablolarında veri kaybı işaretidir.
            if len(df) == 0 and not name.startswith("dep_rollings"):
                raise RuntimeError("extractor boş DataFrame döndürdü")
            path = out_dir / f"{name}.parquet"
            df.to_parquet(path, index=False)
            if len(df) == 0:
                log.warning("   ! %s: pencere boş (%s → %s) — boş parquet yazıldı",
                            name, ctx.rollings_start, ctx.rollings_end)
            entry = {
                "rows": int(len(df)),
                "columns": [str(c) for c in df.columns],
                "date_range": _date_range_of(df),
                "generated_at": run_at,
                "status": "ok" if len(df) else "ok_empty",
            }
            manifest["tables"][name] = entry
            log.info("   ✓ %s: %d satır → %s", name, len(df), path.name)
        except Exception as exc:
            failures.append(name)
            manifest["tables"][name] = {
                "status": "error",
                "error": str(exc).splitlines()[0][:300],
                "failed_at": run_at,
            }
            log.error("   ✗ %s düştü: %s", name, exc)
            log.debug("%s", traceback.format_exc())

    manifest["ok"] = not failures
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Manifest yazıldı: %s (ok=%s, hata=%s)",
             manifest_path, manifest["ok"], failures or "yok")
    return 1 if failures else 0


def _default_rollings_window() -> tuple[str, str]:
    """Rollings penceresi konvansiyonu: bugün → bugün+N gün (DD/MM/YYYY —
    dashboard'un kendi bind formatı; DEV'de _to_bind ISO'ya çevirir)."""
    start = date.today()
    end = start + timedelta(days=DEFAULT_ROLLINGS_DAYS_AHEAD)
    return start.strftime("%d/%m/%Y"), end.strftime("%d/%m/%Y")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    d_start, d_end = _default_rollings_window()

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--nim-repo",
                   default=os.environ.get("DEPOSITS_ETL_NIM_REPO"),
                   help="NIM_calculation repo kökü (env: DEPOSITS_ETL_NIM_REPO)")
    p.add_argument("--out",
                   default=os.environ.get(
                       "DEPOSITS_ETL_OUT",
                       str(Path(__file__).resolve().parents[1] / "dev_data" / "deposits_etl")),
                   help="Parquet çıktı dizini (env: DEPOSITS_ETL_OUT)")
    p.add_argument("--env",
                   default=os.environ.get("DEPOSITS_ETL_ENV"),
                   choices=["DEVELOPMENT", "PRODUCTION_EXC", "PRODUCTION_DB"],
                   help="NIM config.ENV override'ı (verilmezse reponun kendi ayarı)")
    p.add_argument("--only", default=None,
                   help="Virgülle ayrılmış tablo listesi (varsayılan: hepsi)")
    p.add_argument("--rollings-start", default=d_start,
                   help=f"Rollings pencere başı DD/MM/YYYY (varsayılan: bugün = {d_start})")
    p.add_argument("--rollings-end", default=d_end,
                   help=f"Rollings pencere sonu DD/MM/YYYY (varsayılan: +{DEFAULT_ROLLINGS_DAYS_AHEAD}g = {d_end})")
    args = p.parse_args(argv)

    if not args.nim_repo:
        p.error("--nim-repo (ya da DEPOSITS_ETL_NIM_REPO) zorunlu")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
