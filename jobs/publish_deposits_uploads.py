"""publish_deposits_uploads.py — deposits ETL parquet'lerini Prisma'ya
"arayüzden yüklenmiş gibi" upload olarak yayınlar.

jobs/deposits_etl.py'nin ürettiği tabloları alır ve commit_upload
endpoint'inin (POST /<pid>/uploads) S3'te bıraktığı artefaktların BİREBİR
aynısını üretir — HTTP/auth olmadan, uygulamanın kendi modülleriyle:

  1. Veri:  xlsx olarak ``prisma-treasury/uploads/<sicil>/<upload_id>.xlsx``
     (app.config["S3_PUT"] — DEV'de fake store, ofiste gerçek S3).
  2. Metadata: hedef sunumun manifest'ine ``uploads[]`` girdisi —
     sheet/kolon şeması uploads.parse_xlsx ile, UI ile aynı şekilde.
  3. Erişim: bloklar/basket ``upload__<upload_id>__<sheet>`` referansıyla
     okur (DuckDB, Excel S3'ten) — keşif/hazırlık/sunum akışının tamamında
     kullanıcı upload'ı gibi görünür.

UI'dan tek bilinçli sapma: upload_id'ler RASTGELE değil, tablo adından
türetilen SABİT id'lerdir (``u_etl<hash>``). Böylece günlük cron aynı
tabloyu yeniden yayınladığında S3 key ve blok referansları değişmez —
dashboard'lar kırılmadan taze veriyi okur. (Id gövdesi alfanümeriktir;
``upload__<id>__<sheet>`` ayrıştırıcısı ilk ``__``'ye böldüğünden id çift
alt çizgi içeremez — new_upload_id ile aynı kural.)

Kullanım (deposits_etl'den SONRA, aynı makinede):
    python jobs/publish_deposits_uploads.py --sicil A16438 \
        [--pid p_deposits_data] [--etl-dir dev_data/deposits_etl] \
        [--only tablo1,tablo2] [--dry-run]

Cron örneği (ETL ile zincirli, her gün 06:30):
    30 6 * * * cd /opt/treasury-report && \
        python jobs/deposits_etl.py ... && \
        python jobs/publish_deposits_uploads.py --sicil A16438 \
        >> /var/log/deposits_publish.log 2>&1
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

log = logging.getLogger("publish_deposits_uploads")

DEFAULT_PID = "p_deposits_data"
DEFAULT_ETL_DIR = REPO_ROOT / "dev_data" / "deposits_etl"


def stable_upload_id(table: str) -> str:
    """Tablo adı → deterministik upload id (``u_etl`` + 8 hex).

    Gövde alfanümerik → ref ayrıştırıcının ilk-``__`` kuralıyla uyumlu;
    deterministik → cron her koşuda aynı id'yi üretir, referanslar sabit kalır.
    """
    digest = hashlib.sha1(table.encode("utf-8")).hexdigest()[:8]
    return f"u_etl{digest}"


def publish(args) -> int:
    etl_dir = Path(args.etl_dir).resolve()
    if not etl_dir.is_dir():
        raise SystemExit(f"--etl-dir bulunamadı: {etl_dir} (önce deposits_etl koşmalı)")

    freshness = {}
    fpath = etl_dir / "_freshness.json"
    if fpath.exists():
        try:
            freshness = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:
            log.warning("_freshness.json okunamadı — etl bilgisi girilmeyecek")

    parquets = sorted(etl_dir.glob("*.parquet"))
    only = {t.strip() for t in args.only.split(",")} if args.only else None
    if only:
        parquets = [p for p in parquets if p.stem in only]
        missing = only - {p.stem for p in parquets}
        if missing:
            raise SystemExit(f"--only eşleşmeyen tablo(lar): {sorted(missing)}")
    if not parquets:
        raise SystemExit(f"{etl_dir} altında parquet yok")

    # Uygulamanın kendisini import et — S3_PUT / SESSION_REGISTRY / upload
    # yardımcıları UI handler'ının kullandıklarının TA KENDİSİ olsun (DEV'de
    # fake store'a, ofiste gerçek S3'e aynı kodla yazar).
    log.info("Flask app import ediliyor (DEV/PROD ortamını kendisi seçer)…")
    from app import app as flask_app
    from presentations.uploads import df_to_xlsx_bytes, parse_xlsx, upload_s3_key

    import pandas as pd

    with flask_app.app_context():
        s3_put = flask_app.config["S3_PUT"]
        registry = flask_app.config["SESSION_REGISTRY"]
        session = registry.get_or_create(args.sicil, args.pid)
        manifest = session.get_manifest() or {
            "id": args.pid,
            "version": 0,
            "owner_id": args.sicil,
            "meta": {"title": "Deposits ETL Veri Alanı"},
            "blocks": [],
        }
        uploads = list(manifest.get("uploads") or [])
        by_id = {u.get("id"): i for i, u in enumerate(uploads)}

        now = datetime.now(timezone.utc).isoformat()
        published = []
        for path in parquets:
            table = path.stem
            df = pd.read_parquet(path)
            # xlsx sheet adı ≤31 karakter (Excel kuralı) — tablo adlarımız uyar.
            xlsx = df_to_xlsx_bytes(df, sheet_name=table[:31])
            sheets = parse_xlsx(xlsx)

            uid = stable_upload_id(table)
            s3_key = upload_s3_key(args.sicil, uid)
            if args.dry_run:
                log.info("[dry-run] %s → %s (%d satır, %d KB)",
                         table, s3_key, len(df), len(xlsx) // 1024)
            else:
                s3_put(s3_key, xlsx)

            entry = {
                "id":          uid,
                "filename":    f"{table}.xlsx",
                "s3_key":      s3_key,
                "size":        len(xlsx),
                "uploaded_at": now,
                "uploaded_by": args.sicil,
                "sheets":      sheets,
                # UI sözleşmesine ek bilgi alanı: bu upload'ın cron-ETL kaynaklı
                # olduğu ve tazeliği. build_upload_lookup yalnız id/s3_key/sheets
                # okur — fazladan anahtar akışı etkilemez.
                "etl": {
                    "source":      "jobs/deposits_etl.py",
                    "etl_run_at":  freshness.get("last_run_at"),
                    "nim_git_sha": freshness.get("nim_git_sha"),
                    "table_info":  (freshness.get("tables") or {}).get(table),
                },
            }
            if uid in by_id:
                uploads[by_id[uid]] = entry
            else:
                by_id[uid] = len(uploads)
                uploads.append(entry)

            ref = f"upload__{uid}__{sheets[0]['name']}"
            published.append((table, ref, len(df)))
            log.info("   ✓ %s → %s (%d satır)", table, ref, len(df))

        if args.dry_run:
            log.info("[dry-run] manifest yazılmadı (%d tablo hazırdı)", len(published))
            return 0

        manifest["uploads"] = uploads
        manifest["version"] = int(manifest.get("version", 0)) + 1
        manifest["updated_at"] = now
        session.set_manifest(manifest)

    log.info("Manifest güncellendi: pid=%s v%s, %d upload",
             args.pid, manifest["version"], len(uploads))
    print("\nBloklarda kullanılacak tablo referansları:")
    for table, ref, rows in published:
        print(f"  {table:<30} → {ref}   ({rows} satır)")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sicil", required=True,
                   help="Upload sahibi sicil (upload'lar bu kullanıcının alanına yazılır)")
    p.add_argument("--pid", default=DEFAULT_PID,
                   help=f"Upload'ların bağlanacağı sunum id'si (varsayılan: {DEFAULT_PID})")
    p.add_argument("--etl-dir", default=str(DEFAULT_ETL_DIR),
                   help="deposits_etl çıktı dizini")
    p.add_argument("--only", default=None,
                   help="Virgülle ayrılmış tablo alt kümesi")
    p.add_argument("--dry-run", action="store_true",
                   help="S3'e/manifest'e yazmadan ne yapılacağını göster")
    return publish(p.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
