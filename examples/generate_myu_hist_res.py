"""generate_myu_hist_res.py — EDW.MYU_HIST_RES için mock veri üretici.

Ofisteki mevduat faiz rezervasyon TARİHSEL (MYU hist) ekstraktının kolon
düzenini taklit eder ve FakeDataClient'ın okuyabileceği temiz bir CSV üretir:
``examples/sample_data/MYU_HIST_RES.csv``.

MYU_DAILY_RES'ten farklı olarak bu ekstrakt MBF fiyatlama bloğunu
(MBF_LOG_DT..MBF_RATE), rakip banka oranlarını pipe formatında
(``42.5|43``) ve strateji kolonlarını (INDICATIVE..STRATEGY_PRICES,
kaynakta hep NULL) taşır; çift kolon adı yoktur (CURRENCY_CODE ve
CCY_CODE ayrı kolonlardır).

Ofis ekstraktından bilinçli farklar (uygulamada kullanılabilir olsun diye):

- Sayılar binlik ayraçsız yazılır (``1,040,000`` değil ``1040000``) —
  pandas/DuckDB tipleri doğru çıkarsın.
- ``[NULL]`` yerine boş hücre (pandas → NaN → JSON null).
- Tarihler ISO (``YYYY-MM-DD``), saat kolonları HHMMSS tamsayı
  (``84411`` = 08:44:11).
- ``CREATE_DT`` son ``--days`` güne dağıtılır (varsayılan 730 —
  tarihsel tablo) ki tarih filtreleri test edilebilsin.
- ``CUST_ID`` MYU_DAILY_RES ile aynı aralıktan (9 haneli) üretilir ki
  iki tablo arasında join denemeleri anlamlı olsun.

Kullanım:
    cd examples
    python generate_myu_hist_res.py                     # 1000 satır, son 730 gün
    python generate_myu_hist_res.py --rows 5000 --days 365 --seed 42
"""
from __future__ import annotations

import argparse
import csv
import datetime
import random
import uuid
from pathlib import Path

OUTPUT = Path(__file__).parent / "sample_data" / "MYU_HIST_RES.csv"

HEADER = [
    "RES_ID", "TALEP_REVIZE_NO", "RESERVATION_NO", "CREATE_DT", "CREATE_TM",
    "RESERVATION_ID_RCL", "CUST_ID", "CURRENCY_CODE", "CURRENTAMOUNT", "INCOMING_AMT",
    "PORTFOLIO_AMT", "RESERVATION_AMT", "CUST_REL", "RELATED_CUST_ID", "RSI_ID",
    "RESERVATION_ID_RIL", "MIN_TERM", "MAX_TERM", "VADE", "AIRATE", "AIRATETYPE",
    "ENTRYUSER_ORG_CD", "ENTRY_USERCODE", "RESERVATION_ID_ENTRY", "FIYATLAYAN_USERCODE",
    "RESERVATION_ID_PRCTRANSACTION", "INTRST_ID", "COMPETITOR_BANK_RTS", "CUST_ID_OUTCOME",
    "ACCOUNT_COUNT", "ORIG_TERM", "CCY_CODE", "ACCT_OPEN_RATE", "INTRST_ID_RIRL",
    "SUGGESTION_STATE_CODE", "DEMANDED_RATE", "OFFERED_RATE", "OFFER_DATE", "OFFER_TIME",
    "ROLL_CUST_ID", "ROLL_AMOUNT", "MBF_LOG_DT", "MBF_LOG_TIME", "MBF_ACCT_ID", "MBF_TERM",
    "MBF_SOURCE", "MBF_ACCT_OPN_RT", "BRANCHMAX", "MBF_COEFF", "MBF_RATE", "ACCT_CUST_ID",
    "DAT", "DOVIZ", "VADE_BASLANGIC", "VADE_BITIS", "EKSTREM", "ZARAR_YETKISI",
    "EKSTREM_YETKI", "OUTCOME", "INDICATIVE", "SELECTED_POINTS", "SENSITIVTY_CURVE",
    "STRATEGY_PRICES", "UNIQUE_ID",
]

CCY_WEIGHTED = ["TRY"] * 8 + ["USD"] * 1 + ["EUR"] * 1
AIRATETYPE_OPTS = ["NO SERVICE", "ANALYTICAL", "FIXED", "FLOATING"]
CUST_REL_OPTS = ["AILE_PORTFOYU", "KORPORATIF", "BIREYSEL"]
MBF_SOURCE_OPTS = ["MMSG0017", "MMSG0020", "WEB", "MOBILE"]


def gen_big_id() -> int:
    """15-18 haneli rezervasyon/RSI kimliği."""
    return random.randint(10**14, 10**18 - 1)


def gen_date_within(days_back: int, today: datetime.date) -> datetime.date:
    return today - datetime.timedelta(days=random.randint(0, days_back - 1))


def gen_time_hhmmss() -> int:
    """Ofis formatındaki saat kolonu (84411 = 08:44:11) — HHMMSS tamsayı."""
    return random.randint(8, 17) * 10000 + random.randint(0, 59) * 100 + random.randint(0, 59)


def gen_rate(lo: float, hi: float) -> float:
    return round(random.uniform(lo, hi), 2)


def maybe(value, null_prob: float = 0.2):
    """null_prob olasılıkla boş hücre (CSV'de gerçek NULL), yoksa değer."""
    return "" if random.random() < null_prob else value


def gen_competitor_rate() -> str:
    """Rakip banka oranları pipe formatında: '42.5|43' — %30 boş."""
    if random.random() < 0.3:
        return ""
    return f"{gen_rate(38, 44)}|{gen_rate(38, 44)}"


def create_mock_row(today: datetime.date, days_back: int) -> dict:
    res_id = gen_big_id()
    rsi_id = res_id + random.randint(1, 100)
    cust_id = random.randint(10**8, 10**9 - 1)

    create_dt = gen_date_within(days_back, today)
    create_tm = gen_time_hhmmss()

    ccy = random.choice(CCY_WEIGHTED)
    current_amount = round(random.uniform(100_000, 50_000_000), 2)
    portfolio_amt = round(current_amount * random.uniform(1.0, 1.15), 2)

    min_term = random.randint(1, 12)
    max_term = random.randint(12, 36)
    demanded = gen_rate(35, 45)
    offered = gen_rate(35, 45)

    # MBF fiyatlama bloğu — ya komple dolu ya komple boş olsun ki
    # blok bazlı analizler tutarlı test edilebilsin.
    mbf_null = random.random() < 0.2
    mbf_log_dt = "" if mbf_null else create_dt.isoformat()
    mbf_log_time = "" if mbf_null else create_tm

    return {
        "RES_ID": res_id,
        "TALEP_REVIZE_NO": random.randint(1, 5),
        "RESERVATION_NO": random.randint(1_000_000, 9_999_999),
        "CREATE_DT": create_dt.isoformat(),
        "CREATE_TM": create_tm,
        "RESERVATION_ID_RCL": res_id,
        "CUST_ID": cust_id,
        "CURRENCY_CODE": ccy,
        "CURRENTAMOUNT": current_amount,
        "INCOMING_AMT": 0,
        "PORTFOLIO_AMT": portfolio_amt,
        "RESERVATION_AMT": current_amount,
        "CUST_REL": maybe(random.choice(CUST_REL_OPTS), 0.5),
        "RELATED_CUST_ID": maybe(random.randint(10**8, 10**9 - 1), 0.2),
        "RSI_ID": rsi_id,
        "RESERVATION_ID_RIL": res_id,
        "MIN_TERM": min_term,
        "MAX_TERM": max_term,
        "VADE": f"{min_term}-{max_term}",
        "AIRATE": gen_rate(35, 45),
        "AIRATETYPE": random.choice(AIRATETYPE_OPTS),
        "ENTRYUSER_ORG_CD": random.randint(100, 999),
        "ENTRY_USERCODE": f"A{random.randint(10_000, 99_999)}",
        "RESERVATION_ID_ENTRY": res_id,
        "FIYATLAYAN_USERCODE": f"A{random.randint(10_000, 99_999)}",
        "RESERVATION_ID_PRCTRANSACTION": res_id,
        "INTRST_ID": maybe(rsi_id, 0.2),
        "COMPETITOR_BANK_RTS": gen_competitor_rate(),
        "CUST_ID_OUTCOME": maybe(cust_id, 0.2),
        "ACCOUNT_COUNT": random.randint(1, 10),
        "ORIG_TERM": random.randint(1, 12),
        "CCY_CODE": ccy,
        "ACCT_OPEN_RATE": gen_rate(40, 43),
        "INTRST_ID_RIRL": maybe(rsi_id, 0.2),
        "SUGGESTION_STATE_CODE": random.randint(1, 10),
        "DEMANDED_RATE": demanded,
        "OFFERED_RATE": offered,
        "OFFER_DATE": create_dt.isoformat(),
        "OFFER_TIME": create_tm,
        "ROLL_CUST_ID": maybe(cust_id, 0.2),
        "ROLL_AMOUNT": current_amount,
        "MBF_LOG_DT": mbf_log_dt,
        "MBF_LOG_TIME": mbf_log_time,
        "MBF_ACCT_ID": "" if mbf_null else random.randint(10**8, 10**9 - 1),
        "MBF_TERM": "" if mbf_null else random.randint(1, 12),
        "MBF_SOURCE": "" if mbf_null else random.choice(MBF_SOURCE_OPTS),
        "MBF_ACCT_OPN_RT": "" if mbf_null else gen_rate(40, 44),
        "BRANCHMAX": "" if mbf_null else gen_rate(40, 45),
        "MBF_COEFF": "" if mbf_null else round(random.uniform(1.0, 1.2), 2),
        "MBF_RATE": "" if mbf_null else gen_rate(38, 43),
        "ACCT_CUST_ID": maybe(cust_id, 0.2),
        "DAT": create_dt.isoformat(),
        "DOVIZ": ccy,
        "VADE_BASLANGIC": min_term,
        "VADE_BITIS": max_term,
        "EKSTREM": demanded,
        "ZARAR_YETKISI": gen_rate(45, 50),
        "EKSTREM_YETKI": gen_rate(45, 50),
        "OUTCOME": random.randint(0, 1),
        # Strateji kolonları kaynak ekstraktta hep NULL — boş bırakılır.
        "INDICATIVE": "",
        "SELECTED_POINTS": "",
        "SENSITIVTY_CURVE": "",
        "STRATEGY_PRICES": "",
        "UNIQUE_ID": str(uuid.uuid4()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="EDW.MYU_HIST_RES mock verisi üret")
    parser.add_argument("--rows", type=int, default=1000, help="satır sayısı (varsayılan 1000)")
    parser.add_argument("--days", type=int, default=730, help="CREATE_DT bugünden geriye kaç güne dağılsın")
    parser.add_argument("--seed", type=int, default=None, help="tekrarlanabilirlik için random seed")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    today = datetime.date.today()
    rows = [create_mock_row(today, args.days) for _ in range(args.rows)]
    rows.sort(key=lambda r: (r["CREATE_DT"], r["CREATE_TM"]))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{args.rows} satır mock veri yazıldı: {OUTPUT}")
    print(f"Tarih aralığı: son {args.days} gün ({rows[0]['CREATE_DT']} → {rows[-1]['CREATE_DT']})")


if __name__ == "__main__":
    main()
