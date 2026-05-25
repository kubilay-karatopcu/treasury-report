"""Seed sample library blocks for the Atölye / Bloklar grid.

Run from repo root:
    python examples/seed_library_blocks.py

Idempotent — wipes examples/library/ first, then writes 16 blocks
across 4 categories so the user can browse the grid against realistic
content. Uses Phase 9.e fixture data: tables in EDW/HIST/ODS_TREASURY/
ODS_RISK/ALM/CDM.

NOT a runtime artifact — call once after pulling new fixtures.
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LIBRARY_DIR = Path(__file__).parent / "library"

# Category-keyed catalogue of demo blocks. Each entry becomes a full
# library record (block.json + meta.json). The `block` shape is the
# manifest leaf shape Sunum expects.

CATEGORIES = {
    "mevduat": {
        "label": "Mevduat Yönetimi",
        "blocks": [
            {
                "title": "Toplam mevduat bakiyesi",
                "type": "kpi",
                "description": "Anlık toplam mevduat — TL ve döviz konsolide.",
                "tags": ["mevduat", "kpi"],
                "used_tables": ["EDW.DEPOSITS_DAILY"],
                "data_source": {
                    "original_sql": "SELECT SUM(BALANCE_TRY) AS value FROM EDW.DEPOSITS_DAILY WHERE DATE = TO_DATE('2025-12-31','YYYY-MM-DD')",
                },
                "config": {
                    "value": 487.2,
                    "unit": "B TRY",
                    "delta": 4.8,
                    "delta_label": "vs Q3 2025",
                    "period": "Q4 2025",
                },
            },
            {
                "title": "Şube bazında mevduat (Top 10)",
                "type": "bar_chart",
                "description": "En yüksek mevduat hacmine sahip 10 şube.",
                "tags": ["mevduat", "şube"],
                "used_tables": ["EDW.DEPOSITS_BY_BRANCH", "EDW.DIM_BRANCH"],
                "data_source": {
                    "original_sql": "SELECT b.BRANCH_NAME, SUM(d.DEPOSITS_TRY) AS total FROM EDW.DEPOSITS_BY_BRANCH d JOIN EDW.DIM_BRANCH b ON d.BRANCH_CODE = b.BRANCH_CODE GROUP BY b.BRANCH_NAME ORDER BY total DESC FETCH FIRST 10 ROWS ONLY",
                },
                "config": {
                    "categories": ["Etiler", "Kadıköy", "Levent", "Beşiktaş", "Kozyatağı"],
                    "series": [{"name": "Mevduat (M TL)", "values": [4200, 3850, 3640, 3120, 2880]}],
                },
            },
            {
                "title": "Aylık mevduat trendi",
                "type": "line_chart",
                "description": "Son 24 ay mevduat bakiyesi — toplam ve segment kırılımıyla.",
                "tags": ["mevduat", "trend"],
                "used_tables": ["HIST.DEPOSITS_HIST"],
                "data_source": {
                    "original_sql": "SELECT TO_CHAR(SNAPSHOT_MONTH,'YYYY-MM') AS ay, SEGMENT, SUM(BALANCE_TRY) AS bakiye FROM HIST.DEPOSITS_HIST WHERE SNAPSHOT_MONTH >= ADD_MONTHS(SYSDATE, -24) GROUP BY TO_CHAR(SNAPSHOT_MONTH,'YYYY-MM'), SEGMENT ORDER BY 1, 2",
                },
                "config": {
                    "x_axis": ["2024-01", "2024-04", "2024-07", "2024-10", "2025-01", "2025-04", "2025-07", "2025-10"],
                    "series": [
                        {"name": "Retail", "values": [180, 188, 195, 210, 220, 235, 248, 260]},
                        {"name": "Corporate", "values": [140, 145, 150, 158, 165, 172, 180, 188]},
                        {"name": "SME", "values": [38, 40, 42, 45, 47, 49, 51, 53]},
                    ],
                },
            },
            {
                "title": "Segment bazında mevduat dağılımı",
                "type": "pie_chart",
                "description": "Retail / Corporate / SME — mevduat hacim payı.",
                "tags": ["mevduat", "segment"],
                "used_tables": ["EDW.DEPOSITS_DAILY"],
                "data_source": {
                    "original_sql": "SELECT SEGMENT, SUM(BALANCE_TRY) AS bakiye FROM EDW.DEPOSITS_DAILY WHERE DATE = TO_DATE('2025-12-31','YYYY-MM-DD') GROUP BY SEGMENT",
                },
                "config": {
                    "categories": ["Retail", "Corporate", "SME"],
                    "series": [{"name": "Dağılım", "values": [260, 188, 53]}],
                },
            },
            {
                "title": "Vadeli / vadesiz mevduat oranı",
                "type": "kpi",
                "description": "Vadeli mevduat bakiyesinin toplam mevduata oranı.",
                "tags": ["mevduat", "vade"],
                "used_tables": ["EDW.DEPOSITS_DAILY", "LOOKUP.DIM_PRODUCT"],
                "data_source": {
                    "original_sql": "SELECT SUM(CASE WHEN p.CATEGORY = 'MEVDUAT' AND PRODUCT_CODE LIKE 'VD%' THEN BALANCE_TRY ELSE 0 END) / SUM(BALANCE_TRY) AS value FROM EDW.DEPOSITS_DAILY d JOIN LOOKUP.DIM_PRODUCT p ON d.PRODUCT_CODE = p.PRODUCT_CODE WHERE DATE = (SELECT MAX(DATE) FROM EDW.DEPOSITS_DAILY)",
                },
                "config": {"value": 67.4, "unit": "%", "delta": 1.2, "delta_label": "vs geçen ay", "period": "Q4 2025"},
            },
            {
                "title": "En çok büyüyen 10 şube",
                "type": "bar_chart",
                "description": "Son 30 günde mevduat büyümesi en yüksek 10 şube.",
                "tags": ["mevduat", "şube", "trend"],
                "used_tables": ["EDW.DEPOSITS_DAILY", "EDW.DIM_BRANCH"],
                "data_source": {
                    "original_sql": "SELECT b.BRANCH_NAME, (SUM(CASE WHEN d.DATE = (SELECT MAX(DATE) FROM EDW.DEPOSITS_DAILY) THEN d.BALANCE_TRY END) - SUM(CASE WHEN d.DATE = (SELECT MAX(DATE) - 30 FROM EDW.DEPOSITS_DAILY) THEN d.BALANCE_TRY END)) AS delta FROM EDW.DEPOSITS_DAILY d JOIN EDW.DIM_BRANCH b ON d.BRANCH_CODE = b.BRANCH_CODE GROUP BY b.BRANCH_NAME ORDER BY delta DESC FETCH FIRST 10 ROWS ONLY",
                },
                "config": {
                    "categories": ["Maslak", "Beylikdüzü", "Ümraniye", "Bursa", "Tepe Prime"],
                    "series": [{"name": "Δ Mevduat (M TL)", "values": [340, 285, 220, 180, 155]}],
                },
            },
            {
                "title": "Müşteri segment × ürün matrisi",
                "type": "heatmap",
                "description": "Segment ve ürün kategorisi bazında mevduat hacim haritası.",
                "tags": ["mevduat", "segment", "ürün"],
                "used_tables": ["EDW.DEPOSITS_DAILY", "LOOKUP.DIM_PRODUCT"],
                "data_source": {
                    "original_sql": "SELECT d.SEGMENT, p.CATEGORY, SUM(d.BALANCE_TRY) AS bakiye FROM EDW.DEPOSITS_DAILY d JOIN LOOKUP.DIM_PRODUCT p ON d.PRODUCT_CODE = p.PRODUCT_CODE WHERE d.DATE = (SELECT MAX(DATE) FROM EDW.DEPOSITS_DAILY) GROUP BY d.SEGMENT, p.CATEGORY",
                },
                "config": {"categories": ["Retail", "Corporate", "SME"], "series": [{"name": "TR", "values": [120, 80, 22]}]},
            },
        ],
    },
    "likidite": {
        "label": "Likidite Yönetimi",
        "blocks": [
            {
                "title": "LCR (Liquidity Coverage Ratio)",
                "type": "kpi",
                "description": "Anlık LCR — 30 günlük net nakit çıkışını karşılama oranı.",
                "tags": ["likidite", "lcr", "kpi"],
                "used_tables": ["EDW.LIQUIDITY_RATIOS"],
                "data_source": {
                    "original_sql": "SELECT LCR AS value FROM EDW.LIQUIDITY_RATIOS WHERE DATE = (SELECT MAX(DATE) FROM EDW.LIQUIDITY_RATIOS) AND CURRENCY = 'ALL'",
                },
                "config": {
                    "value": 142.7,
                    "unit": "%",
                    "delta": 3.2,
                    "delta_label": "vs geçen ay",
                    "period": "Aralık 2025",
                },
            },
            {
                "title": "LCR + NSFR trendi (12 ay)",
                "type": "line_chart",
                "description": "İki anahtar likidite oranının 12 aylık seyri.",
                "tags": ["likidite", "trend"],
                "used_tables": ["EDW.LIQUIDITY_RATIOS"],
                "data_source": {
                    "original_sql": "SELECT TO_CHAR(DATE,'YYYY-MM') AS ay, AVG(LCR) AS lcr, AVG(NSFR) AS nsfr FROM EDW.LIQUIDITY_RATIOS WHERE DATE >= ADD_MONTHS(SYSDATE, -12) AND CURRENCY = 'ALL' GROUP BY TO_CHAR(DATE,'YYYY-MM') ORDER BY 1",
                },
                "config": {
                    "x_axis": ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"],
                    "series": [
                        {"name": "LCR", "values": [135, 138, 140, 137, 141, 139, 143, 142, 144, 141, 140, 142.7]},
                        {"name": "NSFR", "values": [112, 113, 114, 113, 115, 114, 116, 115, 117, 115, 114, 116]},
                    ],
                },
            },
            {
                "title": "Vade dilimine göre likidite gap",
                "type": "bar_chart",
                "description": "Net nakit gap — vade dilimi bazında, TL portföy.",
                "tags": ["likidite", "gap"],
                "used_tables": ["ODS_RISK.LIQUIDITY_RISK_BUCKETS"],
                "data_source": {
                    "original_sql": "SELECT TENOR, NET_GAP FROM ODS_RISK.LIQUIDITY_RISK_BUCKETS WHERE AS_OF_DATE = (SELECT MAX(AS_OF_DATE) FROM ODS_RISK.LIQUIDITY_RISK_BUCKETS) AND CURRENCY = 'TRY' ORDER BY (CASE TENOR WHEN 'ON' THEN 1 WHEN '1W' THEN 2 WHEN '1M' THEN 3 WHEN '3M' THEN 4 WHEN '6M' THEN 5 WHEN '1Y' THEN 6 WHEN '5Y' THEN 7 END)",
                },
                "config": {
                    "categories": ["ON", "1W", "1M", "3M", "6M", "1Y", "5Y+"],
                    "series": [{"name": "Net gap (M TL)", "values": [80, 120, 60, -40, -180, -220, 380]}],
                },
            },
            {
                "title": "Repo anlaşmaları — vade dağılımı",
                "type": "bar_chart",
                "description": "Aktif repo + ters repo işlemleri vade bazında.",
                "tags": ["likidite", "repo"],
                "used_tables": ["ODS_TREASURY.REPO_AGREEMENTS"],
                "data_source": {
                    "original_sql": "SELECT TENOR, DIRECTION, SUM(NOTIONAL) AS total FROM ODS_TREASURY.REPO_AGREEMENTS WHERE TRADE_DATE >= SYSDATE - 1 GROUP BY TENOR, DIRECTION",
                },
                "config": {
                    "categories": ["ON", "1W", "1M", "3M"],
                    "series": [
                        {"name": "REPO (M TL)", "values": [1200, 800, 400, 200]},
                        {"name": "REVERSE (M TL)", "values": [600, 400, 280, 120]},
                    ],
                },
            },
            {
                "title": "Para birimi bazında likidite payı",
                "type": "pie_chart",
                "description": "Toplam likidite varlıklarının para birimi dağılımı.",
                "tags": ["likidite", "ccy"],
                "used_tables": ["EDW.LIQUIDITY_RATIOS"],
                "data_source": {
                    "original_sql": "SELECT CURRENCY, AVG(LCR) AS lcr FROM EDW.LIQUIDITY_RATIOS WHERE DATE = (SELECT MAX(DATE) FROM EDW.LIQUIDITY_RATIOS) AND CURRENCY <> 'ALL' GROUP BY CURRENCY",
                },
                "config": {"categories": ["TRY", "USD", "EUR"], "series": [{"name": "LCR (%)", "values": [148, 135, 128]}]},
            },
            {
                "title": "Encumbered varlık oranı",
                "type": "line_chart",
                "description": "Teminata bağlanmış varlıkların toplam aktife oranı (12 ay).",
                "tags": ["likidite", "encumbrance"],
                "used_tables": ["EDW.LIQUIDITY_RATIOS"],
                "data_source": {
                    "original_sql": "SELECT TO_CHAR(DATE,'YYYY-MM') AS ay, AVG(ENCUMBRANCE_PCT) AS enc FROM EDW.LIQUIDITY_RATIOS WHERE DATE >= ADD_MONTHS(SYSDATE, -12) GROUP BY TO_CHAR(DATE,'YYYY-MM') ORDER BY 1",
                },
                "config": {
                    "x_axis": ["Oca", "Mar", "May", "Tem", "Eyl", "Kas"],
                    "series": [{"name": "Encumbrance (%)", "values": [18.4, 19.1, 18.8, 17.9, 18.3, 17.6]}],
                },
            },
            {
                "title": "ON likidite rezervi",
                "type": "kpi",
                "description": "Gecelik nakit + merkez bankası rezervi.",
                "tags": ["likidite", "rezerv"],
                "used_tables": ["ODS_RISK.LIQUIDITY_RISK_BUCKETS"],
                "data_source": {
                    "original_sql": "SELECT SUM(CASH_INFLOW) AS value FROM ODS_RISK.LIQUIDITY_RISK_BUCKETS WHERE TENOR = 'ON' AND CURRENCY = 'TRY' AND AS_OF_DATE = (SELECT MAX(AS_OF_DATE) FROM ODS_RISK.LIQUIDITY_RISK_BUCKETS)",
                },
                "config": {"value": 12.8, "unit": "B TRY", "delta": -2.1, "delta_label": "vs dün", "period": "Bugün"},
            },
        ],
    },
    "piyasa": {
        "label": "Piyasa Takibi",
        "blocks": [
            {
                "title": "TCMB politika faizi",
                "type": "kpi",
                "description": "Anlık politika faizi — TCMB son toplantı kararı.",
                "tags": ["piyasa", "faiz"],
                "used_tables": ["EDW.INTEREST_RATES_DAILY"],
                "data_source": {
                    "original_sql": "SELECT RATE AS value FROM EDW.INTEREST_RATES_DAILY WHERE INSTRUMENT = 'POLICY' AND DATE = (SELECT MAX(DATE) FROM EDW.INTEREST_RATES_DAILY WHERE INSTRUMENT = 'POLICY')",
                },
                "config": {
                    "value": 42.5,
                    "unit": "%",
                    "delta": -2.5,
                    "delta_label": "Aralık MPK",
                    "period": "Aralık 2025",
                },
            },
            {
                "title": "Rakip mevduat faiz oranları",
                "type": "bar_chart",
                "description": "Büyük bankaların 3 ay vadeli TL mevduat faizleri.",
                "tags": ["piyasa", "rakip"],
                "used_tables": ["EDW.COMPETITOR_RATES"],
                "data_source": {
                    "original_sql": "SELECT BANK_NAME, AVG(RATE) AS rate FROM EDW.COMPETITOR_RATES WHERE VADE = '3M' AND DATE >= SYSDATE - 7 GROUP BY BANK_NAME ORDER BY rate DESC",
                },
                "config": {
                    "categories": ["GARANTI", "AKBANK", "ISBANK", "YAPIKREDI", "ZIRAAT", "VAKIF", "HALKBANK"],
                    "series": [{"name": "3M TL faizi (%)", "values": [44.5, 44.2, 43.8, 43.5, 42.9, 42.6, 42.4]}],
                },
            },
            {
                "title": "TL faiz eğrisi (zero curve)",
                "type": "line_chart",
                "description": "Vade × faiz — anlık ve 1 ay önce karşılaştırması.",
                "tags": ["piyasa", "yield_curve"],
                "used_tables": ["EDW.INTEREST_RATES_DAILY"],
                "data_source": {
                    "original_sql": "SELECT TENOR, DATE, AVG(RATE) AS rate FROM EDW.INTEREST_RATES_DAILY WHERE INSTRUMENT = 'OIS' AND (DATE = SYSDATE - 1 OR DATE = ADD_MONTHS(SYSDATE, -1)) GROUP BY TENOR, DATE",
                },
                "config": {
                    "x_axis": ["ON", "1W", "1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y"],
                    "series": [
                        {"name": "Bugün", "values": [42.5, 42.7, 43.1, 43.6, 43.9, 43.5, 41.8, 38.5, 35.2]},
                        {"name": "1 ay önce", "values": [45.0, 45.2, 45.6, 46.0, 46.1, 45.7, 43.8, 39.2, 35.8]},
                    ],
                },
            },
            {
                "title": "USD/TRY günlük değişim",
                "type": "line_chart",
                "description": "USD/TRY paritesinin 90 günlük seyri.",
                "tags": ["piyasa", "fx"],
                "used_tables": ["HIST.FX_HIST"],
                "data_source": {
                    "original_sql": "SELECT DATE, RATE_MARKET FROM HIST.FX_HIST WHERE CCY_PAIR = 'USD_TRY' AND DATE >= SYSDATE - 90 ORDER BY DATE",
                },
                "config": {
                    "x_axis": ["Eyl", "Eki", "Kas", "Ara"],
                    "series": [{"name": "USD/TRY", "values": [33.8, 34.5, 35.1, 35.7]}],
                },
            },
            {
                "title": "FX 30 günlük volatilite",
                "type": "kpi",
                "description": "USD/TRY 30 günlük günlük getiri standart sapma.",
                "tags": ["piyasa", "fx", "volatilite"],
                "used_tables": ["HIST.FX_HIST"],
                "data_source": {
                    "original_sql": "SELECT STDDEV(LN(RATE_MARKET / LAG(RATE_MARKET) OVER (ORDER BY DATE))) * SQRT(252) AS value FROM HIST.FX_HIST WHERE CCY_PAIR = 'USD_TRY' AND DATE >= SYSDATE - 30",
                },
                "config": {"value": 18.4, "unit": "%", "delta": -1.6, "delta_label": "vs geçen ay", "period": "Aralık"},
            },
            {
                "title": "Eurobond getiri eğrisi",
                "type": "line_chart",
                "description": "TR Eurobond ana ihraçlarının YTM'leri.",
                "tags": ["piyasa", "eurobond"],
                "used_tables": ["EDW.TREASURY_BONDS"],
                "data_source": {
                    "original_sql": "SELECT MATURITY, AVG(COUPON) AS coupon, AVG(MARK_TO_MARKET / NOMINAL_USD * 100) AS price FROM EDW.TREASURY_BONDS WHERE ISSUER_TYPE = 'DEVLET' AND CURRENCY = 'USD' AND DATE = (SELECT MAX(DATE) FROM EDW.TREASURY_BONDS) GROUP BY MATURITY ORDER BY 1",
                },
                "config": {
                    "x_axis": ["2Y", "5Y", "10Y", "30Y"],
                    "series": [{"name": "YTM (%)", "values": [6.4, 7.2, 8.1, 8.6]}],
                },
            },
            {
                "title": "Rakip - bizim TL faiz farkı",
                "type": "bar_chart",
                "description": "3 ay TL mevduat faizinde bizim oranımız vs piyasa ortalaması.",
                "tags": ["piyasa", "rekabet"],
                "used_tables": ["EDW.COMPETITOR_RATES"],
                "data_source": {
                    "original_sql": "SELECT TUTAR_RANGE, AVG(RATE) AS market_avg FROM EDW.COMPETITOR_RATES WHERE VADE = '3M' AND DATE >= SYSDATE - 7 GROUP BY TUTAR_RANGE",
                },
                "config": {
                    "categories": ["0-50K", "50K-100K", "100K-250K", "250K-1M", "1M+"],
                    "series": [
                        {"name": "Piyasa ort.", "values": [42.5, 43.2, 43.8, 44.3, 45.1]},
                        {"name": "Bizim", "values": [42.0, 43.0, 43.5, 44.0, 44.8]},
                    ],
                },
            },
        ],
    },
    "alm": {
        "label": "ALM (Aktif/Pasif Yönetimi)",
        "blocks": [
            {
                "title": "Net stable funding ratio (NSFR)",
                "type": "kpi",
                "description": "Anlık NSFR — 1 yıllık fonlama yapısı sağlamlığı.",
                "tags": ["alm", "nsfr"],
                "used_tables": ["EDW.LIQUIDITY_RATIOS"],
                "data_source": {
                    "original_sql": "SELECT NSFR AS value FROM EDW.LIQUIDITY_RATIOS WHERE DATE = (SELECT MAX(DATE) FROM EDW.LIQUIDITY_RATIOS) AND CURRENCY = 'ALL'",
                },
                "config": {
                    "value": 116.4,
                    "unit": "%",
                    "delta": 1.8,
                    "delta_label": "vs geçen çeyrek",
                    "period": "Q4 2025",
                },
            },
            {
                "title": "NII projeksiyon (12 ay, senaryolar)",
                "type": "line_chart",
                "description": "Base + ±100bp + twist senaryolarında NII projeksiyonu.",
                "tags": ["alm", "nii", "senaryo"],
                "used_tables": ["ALM.NII_PROJECTIONS"],
                "data_source": {
                    "original_sql": "SELECT TO_CHAR(PROJECTION_MONTH,'YYYY-MM') AS ay, SCENARIO, SUM(PROJECTED_NII) AS nii FROM ALM.NII_PROJECTIONS WHERE AS_OF_DATE = (SELECT MAX(AS_OF_DATE) FROM ALM.NII_PROJECTIONS) GROUP BY TO_CHAR(PROJECTION_MONTH,'YYYY-MM'), SCENARIO ORDER BY 1",
                },
                "config": {
                    "x_axis": ["2026-01", "2026-03", "2026-06", "2026-09", "2026-12"],
                    "series": [
                        {"name": "BASE", "values": [102, 108, 118, 128, 138]},
                        {"name": "+100bp", "values": [110, 118, 132, 145, 160]},
                        {"name": "-100bp", "values": [95, 99, 105, 112, 120]},
                    ],
                },
            },
            {
                "title": "Vade gap analizi",
                "type": "bar_chart",
                "description": "Aktif - Pasif gap, TL portföy, vade dilimi bazında.",
                "tags": ["alm", "gap"],
                "used_tables": ["ALM.GAP_ANALYSIS"],
                "data_source": {
                    "original_sql": "SELECT TENOR, GAP FROM ALM.GAP_ANALYSIS WHERE AS_OF_DATE = (SELECT MAX(AS_OF_DATE) FROM ALM.GAP_ANALYSIS) AND CURRENCY = 'TRY' ORDER BY (CASE TENOR WHEN 'ON' THEN 1 WHEN '1W' THEN 2 WHEN '1M' THEN 3 WHEN '3M' THEN 4 WHEN '6M' THEN 5 WHEN '1Y' THEN 6 WHEN '5Y' THEN 7 END)",
                },
                "config": {
                    "categories": ["ON", "1W", "1M", "3M", "6M", "1Y", "5Y+"],
                    "series": [{"name": "Gap (M TL)", "values": [320, 180, -60, -240, -420, -180, 420]}],
                },
            },
            {
                "title": "Aktif - Pasif modified duration",
                "type": "kpi",
                "description": "Aktif tarafının ağırlıklı modified duration'ı (yıl).",
                "tags": ["alm", "duration"],
                "used_tables": ["ALM.DURATION_REPORT"],
                "data_source": {
                    "original_sql": "SELECT AVG(MODIFIED_DURATION) AS value FROM ALM.DURATION_REPORT WHERE PORTFOLIO_SIDE = 'ASSET' AND AS_OF_DATE = (SELECT MAX(AS_OF_DATE) FROM ALM.DURATION_REPORT)",
                },
                "config": {
                    "value": 2.34,
                    "unit": "yıl",
                    "delta": 0.08,
                    "delta_label": "vs geçen çeyrek",
                    "period": "Q4 2025",
                },
            },
            {
                "title": "Pasif vadesi dağılımı",
                "type": "bar_chart",
                "description": "Pasif tarafının vade dilimlerine göre tutar dağılımı.",
                "tags": ["alm", "vade", "pasif"],
                "used_tables": ["ALM.GAP_ANALYSIS"],
                "data_source": {
                    "original_sql": "SELECT TENOR, SUM(LIABILITIES) AS pasif FROM ALM.GAP_ANALYSIS WHERE AS_OF_DATE = (SELECT MAX(AS_OF_DATE) FROM ALM.GAP_ANALYSIS) AND CURRENCY = 'TRY' GROUP BY TENOR ORDER BY (CASE TENOR WHEN 'ON' THEN 1 WHEN '1W' THEN 2 WHEN '1M' THEN 3 WHEN '3M' THEN 4 WHEN '6M' THEN 5 WHEN '1Y' THEN 6 WHEN '5Y' THEN 7 END)",
                },
                "config": {
                    "categories": ["ON", "1W", "1M", "3M", "6M", "1Y", "5Y+"],
                    "series": [{"name": "Pasif (B TL)", "values": [220, 180, 140, 80, 60, 40, 20]}],
                },
            },
            {
                "title": "Convexity matrisi",
                "type": "heatmap",
                "description": "Para birimi × portföy tarafı convexity ısı haritası.",
                "tags": ["alm", "convexity"],
                "used_tables": ["ALM.DURATION_REPORT"],
                "data_source": {
                    "original_sql": "SELECT CURRENCY, PORTFOLIO_SIDE, AVG(CONVEXITY) AS conv FROM ALM.DURATION_REPORT WHERE AS_OF_DATE = (SELECT MAX(AS_OF_DATE) FROM ALM.DURATION_REPORT) GROUP BY CURRENCY, PORTFOLIO_SIDE",
                },
                "config": {"categories": ["TRY", "USD", "EUR"], "series": [{"name": "Convexity", "values": [4.2, 6.1, 5.4]}]},
            },
            {
                "title": "Faiz duyarlılığı (PV01)",
                "type": "kpi",
                "description": "1 baz puanlık faiz hareketinin portföy değeri etkisi.",
                "tags": ["alm", "duyarlılık"],
                "used_tables": ["ALM.DURATION_REPORT"],
                "data_source": {
                    "original_sql": "SELECT SUM(MODIFIED_DURATION) AS value FROM ALM.DURATION_REPORT WHERE AS_OF_DATE = (SELECT MAX(AS_OF_DATE) FROM ALM.DURATION_REPORT)",
                },
                "config": {"value": 3.18, "unit": "M TRY/bp", "delta": 0.12, "delta_label": "vs geçen ay", "period": "Aralık"},
            },
        ],
    },
}

USER_SICIL = "A16438"
USER_DEPT = "Treasury"


def _gen_id(category: str, idx: int) -> str:
    return f"lib_{category}_{idx:02d}"


def main():
    if LIBRARY_DIR.exists():
        shutil.rmtree(LIBRARY_DIR)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)

    saved = 0
    for category, payload in CATEGORIES.items():
        label = payload["label"]
        for i, block in enumerate(payload["blocks"], start=1):
            lid = _gen_id(category, i)
            now = datetime.now(timezone.utc).isoformat()

            block_full = {
                "id": lid,
                "type": block["type"],
                "title": block["title"],
                "config": block["config"],
                "data_source": block["data_source"],
            }
            meta = {
                "library_id": lid,
                "created_at": now,
                "updated_at": now,
                "owner_id": USER_SICIL,
                "owner_department": USER_DEPT,
                "name": block["title"],
                "description": block["description"],
                "tags": [label] + block.get("tags", []),
                "used_tables": block.get("used_tables", []),
                "block_type": block["type"],
                "audience_sicils": [],
                "audience_departments": [USER_DEPT],
            }
            d = LIBRARY_DIR / lid
            d.mkdir(parents=True, exist_ok=True)
            (d / "block.json").write_text(
                json.dumps(block_full, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (d / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            saved += 1

    print(f"Wrote {saved} library blocks under {LIBRARY_DIR}")


if __name__ == "__main__":
    main()
