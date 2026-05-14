# -*- coding: utf-8 -*-
"""
Deposit Panel Blueprint
Mevduat Paneli – Parametreler & Rezervasyon Takip
"""

import json
import logging
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, Response
from flask_login import login_required, current_user
import pandas as pd

log = logging.getLogger(__name__)

deposit_panel_bp = Blueprint(
    "deposit_panel",
    __name__,
    template_folder="templates",
    static_folder="static",
)

# ── Department whitelist for hyperparameter editing ─────────────────────
HYPERPARAM_EDIT_DEPARTMENTS = [
    "FİNANSAL YAPAY ZEKA UYGULAMALARI",
]

# ── Injected references – set by init_app() from app.py ─────────────────
_dc = None
_get_df = None


def init_app(dc, get_df_func=None):
    global _dc, _get_df
    _dc = dc
    _get_df = get_df_func


# ── Helpers ─────────────────────────────────────────────────────────────
# SELECT queries use _dc.get_data() → TEST/DEV reads from S3, PROD from Oracle
# DML (INSERT/DELETE/UPDATE) uses _execute_dml() → direct connection always


def _execute_dml(statements):
    conn = _dc.get_connection()
    try:
        cur = conn.cursor()
        for sql, params in statements:
            cur.execute(sql, params or {})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _dc.drop_connection(conn)


def _user_can_edit_hyperparams():
    """Check if the current user's department allows hyperparameter editing."""
    try:
        from flask import current_app
        if current_app.config.get("LOGIN_DISABLED"):
            return True
        return current_user.is_authenticated and \
               current_user.department in HYPERPARAM_EDIT_DEPARTMENTS
    except Exception:
        return False


# ═════════════════════════════════════════════════════════════════════════
#  PAGE ROUTES
# ═════════════════════════════════════════════════════════════════════════

@deposit_panel_bp.route("/")
@login_required
def index():
    from flask import redirect, url_for
    return redirect(url_for("deposit_panel.params"))


@deposit_panel_bp.route("/params")
@login_required
def params():
    can_edit_hp = _user_can_edit_hyperparams()
    return render_template("deposit_panel/params.html", can_edit_hp=can_edit_hp)


@deposit_panel_bp.route("/reservations")
@login_required
def reservations():
    return render_template("deposit_panel/reservations.html")


# ═════════════════════════════════════════════════════════════════════════
#  API – DEP_SMALL_APP_PARAMS
# ═════════════════════════════════════════════════════════════════════════

@deposit_panel_bp.route("/api/get-params", methods=["POST"])
@login_required
def api_get_params():
    try:
        df = _dc.get_data(
            base_prefix="bsp",
            dataset="raw/input_data/dep_params",
            query="./queries/dep_params.sql",
        )
        for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
            df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")

        # to_json -> spec uyumlu null üretir, NaN/NaT/Inf hepsini null yapar
        json_str = df.to_json(orient="records", date_format="iso")
        return Response(
            f'{{"ok": true, "data": {json_str}}}',
            mimetype="application/json",
        )
    except Exception as e:
        log.exception("api_get_params failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@deposit_panel_bp.route("/api/set-params", methods=["POST"])
@login_required
def api_set_params():
    # Model params (market max, new funding) → everyone can update
    try:
        body = request.get_json(force=True)
        market_max_rt = float(body["market_max_rt"])
        new_funding_rt = float(body["new_funding_rt"])
        asofdate = datetime.today().strftime("%d/%m/%Y")

        _execute_dml([
            (
                "DELETE FROM A16438.DEP_SMALL_APP_PARAMS WHERE ASOFDATE = TO_DATE(:asofdate, 'DD/MM/YYYY')",
                {"asofdate": asofdate},
            ),
            (
                """INSERT INTO A16438.DEP_SMALL_APP_PARAMS
                   (ASOFDATE, INSERT_DT, MARKET_MAX_RT, NEW_FUNDING_RT)
                   VALUES (TO_DATE(:asofdate, 'DD/MM/YYYY'), SYSDATE, :market_max_rt, :new_funding_rt)""",
                {"asofdate": asofdate, "market_max_rt": market_max_rt, "new_funding_rt": new_funding_rt},
            ),
        ])
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("api_set_params failed")
        return jsonify({"ok": False, "error": str(e)}), 500


# ═════════════════════════════════════════════════════════════════════════
#  API – DEP_SMALL_PRC_HYPERPARAMETERS
# ═════════════════════════════════════════════════════════════════════════

@deposit_panel_bp.route("/api/get-hyperparams", methods=["POST"])
@login_required
def api_get_hyperparams():
    try:
        df = _dc.get_data(
            base_prefix="bsp",
            dataset="raw/input_data/dep_hyperparams",
            query="./queries/dep_hyperparams.sql",
        )
        # Convert DataFrame to {PAR_NAME: PAR_VALUE} dict
        hp = dict(zip(df["PAR_NAME"], df["PAR_VALUE"]))
        return jsonify({"ok": True, "data": hp})
    except Exception as e:
        log.exception("api_get_hyperparams failed")
        return jsonify({"ok": False, "error": str(e)}), 500


@deposit_panel_bp.route("/api/set-hyperparams", methods=["POST"])
@login_required
def api_set_hyperparams():
    try:
        body = request.get_json(force=True)
        params_to_update = body.get("params", {})

        # Separate: PRCNG_STRATEGIES is open to everyone,
        # CUST_TP_ADJ and MARKET_ADJUST require department check
        restricted_keys = {"CUST_TP_ADJ", "MARKET_ADJUST"}
        can_edit = _user_can_edit_hyperparams()

        stmts = []
        for par_name, par_value in params_to_update.items():
            if par_name in restricted_keys and not can_edit:
                continue  # silently skip unauthorized keys
            stmts.append((
                """UPDATE A16438.DEP_SMALL_PRC_HYPERPARAMETERS
                   SET PAR_VALUE = :par_value
                   WHERE PAR_NAME = :par_name""",
                {"par_value": str(par_value), "par_name": par_name},
            ))

        if stmts:
            _execute_dml(stmts)

        return jsonify({"ok": True})
    except Exception as e:
        log.exception("api_set_hyperparams failed")
        return jsonify({"ok": False, "error": str(e)}), 500


# ═════════════════════════════════════════════════════════════════════════
#  API – Reservations (today's data)
# ═════════════════════════════════════════════════════════════════════════

@deposit_panel_bp.route("/api/get-today-data", methods=["GET"])
@login_required
def api_get_today_data():
    try:
        if _get_df is None:
            return jsonify([])

        df = _get_df()
        if df is None or df.empty:
            return jsonify([])

        today_str = datetime.today().strftime("%Y-%m-%d")

        cols_to_send = [
            "DATE_TIME_STR", "DATE_STR_CLEAN", "DATA_SRC", "CUST_TP",
            "CCY_CODE", "VADE_BASLANGIC", "RESERVATION_AMT", "CURRENTAMOUNT",
            "INCOMING_AMT", "OFFERED_RATE", "DEMANDED_RATE", "SUGGESTED_PRICE",
            "MARKET_MAX_RT", "EKSTREM", "EKSTREM_YETKI",
            "PERCENTILE_COMPETITOR_RTS", "PERCENTILE_DEMANDED_RTS",
            "TALEP_REVIZE_NO", "IS_MAX_REVIZE", "CUST_ID",
        ]
        valid_cols = [c for c in cols_to_send if c in df.columns]

        df_today = df[df["DATE_STR_CLEAN"] == today_str][valid_cols].copy()
        df_today = df_today.where(pd.notnull(df_today), None)

        json_data = df_today.to_json(orient="records", date_format="iso")
        return Response(json_data, mimetype="application/json")
    except Exception as e:
        log.exception("api_get_today_data failed")
        return jsonify([])