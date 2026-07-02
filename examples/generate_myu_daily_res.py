"""generate_myu_daily_res.py — EDW.MYU_DAILY_RES için mock veri üretici.

Ofisteki mevduat faiz rezervasyon (MYU) ekstraktının kolon düzenini taklit
eder ve FakeDataClient'ın okuyabileceği temiz bir CSV üretir:
``examples/sample_data/MYU_DAILY_RES.csv``.

Ofis ekstraktından bilinçli farklar (uygulamada kullanılabilir olsun diye):

- Sayılar binlik ayraçsız yazılır (``1,040,000`` değil ``1040000``) —
  pandas/DuckDB tipleri doğru çıkarsın.
- ``[NULL]`` yerine boş hücre (pandas → NaN → JSON null).
- Tarihler ISO (``YYYY-MM-DD``), saat kolonu HHMMSS tamsayı (``84411`` = 08:44:11).
- Başlıkta iki kez geçen ``CCY_CODE`` ve ``DAT`` kolonlarının ikinci
  kopyaları ``CCY_CODE_2`` / ``DAT_2`` olarak adlandırıldı (SQL'de
  ``CCY_CODE.1`` gibi bozuk isimler oluşmasın).
- ``CREATE_DT`` son ``--days`` güne dağıtılır ki tarih filtreleri test
  edilebilsin; ``VADE`` / ``MIN_TERM`` / ``MAX_TERM`` çeşitlendirildi
  (1-3, 3-6, 6-12) ki tenor kırılımlı grafikler boş çıkmasın.

Kullanım:
    cd examples
    python generate_myu_daily_res.py                    # 500 satır, son 180 gün
    python generate_myu_daily_res.py --rows 2000 --days 90 --seed 42
"""
from __future__ import annotations

import argparse
import csv
import datetime
import random
from pathlib import Path

OUTPUT = Path(__file__).parent / "sample_data" / "MYU_DAILY_RES.csv"

HEADER = [
    "RES_ID", "TALEP_REVIZE_NO", "RESERVATION_NO", "CREATE_DT", "CREATE_TM",
    "RESERVATION_ID_RCL", "CUST_ID", "CCY_CODE", "CURRENTAMOUNT", "INCOMING_AMT",
    "PORTFOLIO_AMT", "RESERVATION_AMT", "CUST_REL", "RELATED_CUST_ID", "RSI_ID",
    "RESERVATION_ID_RIL", "MIN_TERM", "MAX_TERM", "VADE", "AIRATE",
    "AIRATETYPE", "ENTRYUSER_ORG_CD", "ENTRY_USERCODE", "RESERVATION_ID_ENTRY",
    "FIYATLAYAN_USERCODE", "RESERVATION_ID_PRCTRANSACTION", "INTRST_ID",
    "COMPETITOR_BANK_RTS", "CUST_ID_OUTCOME", "ACCOUNT_COUNT", "ORIG_TERM",
    "CCY_CODE_2", "ACCT_OPEN_RATE", "INTRST_ID_RIRL", "SUGGESTION_STATE_CODE",
    "DEMANDED_RATE", "OFFERED_RATE", "OFFER_DATE", "OFFER_TIME",
    "ROLL_CUST_ID", "ROLL_AMOUNT", "EDW_CUST_ID", "CUST_TP", "MAPPED_PC",
    "CONVERTUSD", "DAT", "DIM_CUST_ID", "BRTH_DT", "OCCP_CODE",
    "MRTL_ST_CODE", "EDUC_LVL_CODE", "GND_CODE", "START_DT", "END_DT",
    "MARKET_MAX_DATE", "MARKET_MAX_RT", "DAT_2", "DOVIZ", "VADE_BASLANGIC",
    "VADE_BITIS", "EKSTREM", "ZARAR_YETKISI", "EKSTREM_YETKI", "OUTCOME",
]

# (MIN_TERM, MAX_TERM, VADE) — tenor kırılımı çeşitliliği için.
TERM_BUCKETS = [("1", "3", "1-3"), ("3", "6", "3-6"), ("6", "12", "6-12")]

CCY_WEIGHTED = ["TRY"] * 8 + ["USD"] * 1 + ["EUR"] * 1
CUST_TP_OPTS = ["G", "T", "K"]
MAPPED_PC_OPTS = ["FB", "DB", "SB"]


def gen_big_id() -> int:
    """15-18 haneli rezervasyon/RSI kimliği."""
    return random.randint(10**14, 10**18 - 1)


def gen_date_within(days_back: int, today: datetime.date) -> datetime.date:
    return today - datetime.timedelta(days=random.randint(0, days_back - 1))


def gen_time_hhmmss() -> int:
    """Ofis formatındaki CREATE_TM (84411 = 08:44:11) — HHMMSS tamsayı."""
    return random.randint(8, 17) * 10000 + random.randint(0, 59) * 100 + random.randint(0, 59)


def maybe(value, null_prob: float = 0.3):
    """null_prob olasılıkla boş hücre (CSV'de gerçek NULL), yoksa değer."""
    return "" if random.random() < null_prob else value


def create_mock_row(today: datetime.date, days_back: int) -> dict:
    res_id = gen_big_id()
    rsi_id = res_id + random.randint(1, 100)
    cust_id = random.randint(10**8, 10**9 - 1)

    create_dt = gen_date_within(days_back, today)
    create_tm = gen_time_hhmmss()

    ccy = random.choice(CCY_WEIGHTED)
    current_amount = round(random.uniform(10_000, 50_000_000), 2)
    portfolio_amt = round(current_amount * random.uniform(1.0, 1.15), 2)

    min_term, max_term, vade = random.choice(TERM_BUCKETS)
    demanded = round(random.uniform(30, 50), 2)
    offered = round(max(0.0, demanded - random.uniform(0, 5)), 2)
    market_max = round(random.uniform(40, 46), 2)
    ekstrem = round(random.uniform(30, 50), 2)
    convert_usd = round(random.uniform(0.020, 0.025), 10)

    roll_null = random.random() < 0.4

    return {
        "RES_ID": res_id,
        "TALEP_REVIZE_NO": 1,
        "RESERVATION_NO": random.randint(1_000_000, 9_999_999),
        "CREATE_DT": create_dt.isoformat(),
        "CREATE_TM": create_tm,
        "RESERVATION_ID_RCL": res_id,
        "CUST_ID": cust_id,
        "CCY_CODE": ccy,
        "CURRENTAMOUNT": current_amount,
        "INCOMING_AMT": 0,
        "PORTFOLIO_AMT": portfolio_amt,
        "RESERVATION_AMT": current_amount,
        "CUST_REL": "",
        "RELATED_CUST_ID": 0,
        "RSI_ID": rsi_id,
        "RESERVATION_ID_RIL": res_id,
        "MIN_TERM": min_term,
        "MAX_TERM": max_term,
        "VADE": vade,
        "AIRATE": 0,
        "AIRATETYPE": "",
        "ENTRYUSER_ORG_CD": random.randint(10_000, 99_999),
        "ENTRY_USERCODE": f"A{random.randint(10_000, 99_999)}",
        "RESERVATION_ID_ENTRY": res_id,
        "FIYATLAYAN_USERCODE": maybe(f"A{random.randint(10_000, 99_999)}", 0.7),
        "RESERVATION_ID_PRCTRANSACTION": "",
        "INTRST_ID": "",
        "COMPETITOR_BANK_RTS": maybe(round(random.uniform(35, 50), 2), 0.5),
        "CUST_ID_OUTCOME": "",
        "ACCOUNT_COUNT": maybe(random.randint(1, 10), 0.3),
        "ORIG_TERM": maybe(random.randint(1, 12), 0.3),
        "CCY_CODE_2": ccy,
        "ACCT_OPEN_RATE": convert_usd,
        "INTRST_ID_RIRL": rsi_id,
        "SUGGESTION_STATE_CODE": "01",
        "DEMANDED_RATE": demanded,
        "OFFERED_RATE": offered,
        "OFFER_DATE": create_dt.isoformat(),
        "OFFER_TIME": create_tm,
        "ROLL_CUST_ID": "" if roll_null else cust_id,
        "ROLL_AMOUNT": "" if roll_null else current_amount,
        "EDW_CUST_ID": cust_id,
        "CUST_TP": random.choice(CUST_TP_OPTS),
        "MAPPED_PC": random.choice(MAPPED_PC_OPTS),
        "CONVERTUSD": convert_usd,
        "DAT": create_dt.isoformat(),
        "DIM_CUST_ID": cust_id,
        "BRTH_DT": gen_date_within(365 * 50, today - datetime.timedelta(days=365 * 20)).isoformat(),
        "OCCP_CODE": random.randint(1, 100),
        "MRTL_ST_CODE": random.randint(1, 3),
        "EDUC_LVL_CODE": random.randint(1, 10),
        "GND_CODE": random.choice(["K", "E"]),
        "START_DT": gen_date_within(365 * 6, today).isoformat(),
        "END_DT": "2400-01-01",
        "MARKET_MAX_DATE": create_dt.isoformat(),
        "MARKET_MAX_RT": market_max,
        "DAT_2": create_dt.isoformat(),
        "DOVIZ": ccy,
        "VADE_BASLANGIC": min_term,
        "VADE_BITIS": max_term,
        "EKSTREM": ekstrem,
        "ZARAR_YETKISI": 0,
        "EKSTREM_YETKI": ekstrem,
        "OUTCOME": random.choice([0] * 7 + [1] * 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="EDW.MYU_DAILY_RES mock verisi üret")
    parser.add_argument("--rows", type=int, default=500, help="satır sayısı (varsayılan 500)")
    parser.add_argument("--days", type=int, default=180, help="CREATE_DT bugünden geriye kaç güne dağılsın")
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
