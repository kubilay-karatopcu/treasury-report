# -*- coding: utf-8 -*-
"""
Deposit Pricing Assistant — Flask Blueprint.

Mounted by the host app at any url_prefix (e.g. /deposit-assistant). All
endpoints, forms, JS fetch URLs and template variables of the original
standalone app are preserved; the blueprint just relativises URLs via
url_for() so they pick up the prefix automatically on the JS side too
(the chat.html template emits a JS constant the existing demo.js uses).

Auth: relies on the host app's flask-login `current_user`. The user is
identified by `current_user.sicil` for per-user state isolation, and the
display name comes from `current_user.name`.
"""

import logging
from flask import (Blueprint, request, render_template, url_for, g)
from flask_login import login_required, current_user

from .state import AppState, PricingSession, PriceSnapshot
from .pricer import price_deposit
from .llm import extract_request, check_revision, generate_response


log = logging.getLogger(__name__)

# Blueprint — note: we DO NOT set static_folder here because we want all
# /static/deposit/... assets served by the host app's main static handler.
# Templates live under templates/deposit/ in the host app's template tree.
deposit_bp = Blueprint(
    "deposit",
    __name__,
    template_folder=None,   # use host app's templates/ dir
    static_folder=None,     # use host app's static/ dir
)


# --------------------------------------------------------------------------- #
# Per-user application state — isolated by sicil
# --------------------------------------------------------------------------- #

_user_states: dict[str, AppState] = {}


def _get_state() -> AppState:
    """Return (or lazily create) the AppState for the logged-in user."""
    sicil = str(current_user.sicil)
    s = _user_states.get(sicil)
    if s is None:
        s = AppState()
        s.init()
        s.init_generate_choices()
        _user_states[sicil] = s
    return s


def _username() -> str:
    """Display name from the host app's User model.
    Tries `.name`, then `.user_json["name"]`, then sicil, falling back
    to a generic label. Never returns falsy or 'undefined'.
    """
    # Direct attribute
    nm = getattr(current_user, "name", None)
    if nm:
        return str(nm)
    # Nested user_json (host app pattern)
    uj = getattr(current_user, "user_json", None)
    if isinstance(uj, dict):
        nm = uj.get("name") or uj.get("NAME")
        if nm:
            return str(nm)
    # Sicil as last meaningful fallback
    sicil = getattr(current_user, "sicil", None)
    if sicil:
        return str(sicil)
    return "Kullanıcı"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _render_partial(state: AppState):
    return render_template(
        "deposit/chat_partial.html",
        show_intro=state.show_intro,
        ps=state.active_pricing_session,
        rt=state.deposit_returns,
        lbl=state.session_labels,
        username=_username(),
    )


def _apply_extraction(ps: PricingSession, user_input: str) -> None:
    extracted = extract_request(user_input)
    req = ps.request
    if extracted.cust_id:
        req.cust_id = int(extracted.cust_id)
    if extracted.tenor:
        req.tenor = int(extracted.tenor)
    if extracted.amount:
        req.amount = float(extracted.amount)
    if extracted.currency:
        ccy = extracted.currency.upper().strip()
        if ccy in ("TL", "TRL"):
            ccy = "TRY"
        req.currency = ccy


def _ready_to_price(ps: PricingSession) -> bool:
    r = ps.request
    return bool(r.cust_id and r.tenor and r.amount > 0 and r.currency)


def _record_price_card(ps: PricingSession) -> None:
    idx = len(ps.conversation) - 1
    ps.newPrices.append(idx)
    ps.priceHistory.append(PriceSnapshot(
        amount     = ps.request.amount,
        currency   = ps.request.currency,
        price      = ps.request.price,
        pricing_no = ps.request.pricing_no,
    ))


# --------------------------------------------------------------------------- #
# Main chat
# --------------------------------------------------------------------------- #

@deposit_bp.route("/", methods=["GET", "POST"])
@login_required
def chat():
    state = _get_state()
    ps = state.active_pricing_session

    if request.method == "GET":
        if len(ps.conversation) == 0 and state.active_session_index == 0:
            state.init_generate_choices()
        return render_template(
            "deposit/chat.html",
            show_intro=state.show_intro,
            ps=ps,
            rt=state.deposit_returns,
            lbl=state.session_labels,
            username=_username(),
        )

    # ---- POST ----
    if ps.confirmation_state == 2:
        log.info("POST rejected: session already confirmed.")
        return _render_partial(state)

    if not ps.choice_made_or_ignored:
        ps.choice_made_or_ignored = True
        ps.conversation = []

    user_input = request.form.get("user_input", "").strip()
    if not user_input:
        return _render_partial(state)

    ps.conversation.append({"role": "user", "content": user_input})
    state.show_intro = 0

    pn_before = ps.request.pricing_no
    priced_now = False
    context_note = ""

    if not ps.priced_before:
        # --- Initial pricing flow ---
        _apply_extraction(ps, user_input)
        ps.request.previous_query = user_input

        if _ready_to_price(ps):
            price_deposit(ps)
            if ps.request.price > 0 and ps.request.pricing_no > pn_before:
                ps.priced_before = True
                priced_now = True
                state.session_label_set(state.active_session_index)

        reply = generate_response(ps.request, context_note=context_note)
        ps.conversation.append({"role": "assistant", "content": reply})

    else:
        # --- Revision / change flow ---
        rev = check_revision(user_input)
        ps.request.previous_query = user_input

        # Latch demanded_price (#6 from the redesign): first non-zero demand
        # is captured; later attempts are silently ignored.
        if rev.demanded_price and ps.latched_demanded_price == 0.0:
            ps.latched_demanded_price = float(rev.demanded_price)
            log.info("demanded_price LATCHED at %s", ps.latched_demanded_price)
        elif rev.demanded_price and ps.latched_demanded_price > 0:
            log.info("demanded_price ignored (latched=%s, attempted=%s)",
                     ps.latched_demanded_price, rev.demanded_price)
        ps.revision_check.demanded_price = ps.latched_demanded_price
        ps.revision_check.is_price_request = rev.is_price_request

        amount_changed = bool(rev.amount_change and rev.amount_change > 0)
        tenor_changed  = bool(rev.tenor_change  and rev.tenor_change  > 0)
        conditions_changed = amount_changed or tenor_changed

        notes = []
        if amount_changed:
            old_amount = ps.request.amount
            ps.request.amount += rev.amount_change
            notes.append(
                f"Tutar {old_amount:,.0f} → {ps.request.amount:,.0f} "
                f"{ps.request.currency} olarak güncellendi "
                f"(+{rev.amount_change:,.0f} ek tutar)."
            )
            log.info("amount change: %s → %s", old_amount, ps.request.amount)
        if tenor_changed:
            old_tenor = ps.request.tenor
            ps.request.tenor = rev.tenor_change
            notes.append(
                f"Vade {old_tenor} gün → {rev.tenor_change} gün olarak "
                f"güncellendi."
            )
            log.info("tenor change: %s → %s", old_tenor, rev.tenor_change)

        if rev.is_acceptance:
            log.info("ACCEPTANCE detected — reminding user to confirm.")
            context_note = ("KABUL_EDILDI: Çalışan teşekkür/kabul/onay "
                            "bildirdi. Sağ panelden 'Fiyatlamayı Onayla' "
                            "butonuna basmasını hatırlat.")
            reply = generate_response(ps.request, context_note=context_note)
            ps.conversation.append({"role": "assistant", "content": reply})

        elif conditions_changed:
            log.info("Conditions changed — FRESH PRICING.")
            ps.request.pricing_no = 0
            ps.request.previous_price = 0.0
            pn_at_reset = 0
            notes.append("YENI_FIYATLAMA: Koşullar değiştiği için bu, "
                         "değişen koşullara göre yeni bir fiyatlamadır "
                         "(eski fiyatla karşılaştırma yapma).")
            context_note = " ".join(notes)
            price_deposit(ps, revision_probability=1.0)
            if ps.request.pricing_no > pn_at_reset:
                priced_now = True
                state.session_label_set(state.active_session_index)
            reply = generate_response(ps.request, context_note=context_note)
            ps.conversation.append({"role": "assistant", "content": reply})

        elif rev.is_price_request:
            REVISION_PROB_THRESHOLD = 0.20
            prob = rev.revision_probability
            log.info("revision_probability=%.2f (threshold=%.2f)",
                     prob, REVISION_PROB_THRESHOLD)

            if prob < REVISION_PROB_THRESHOLD:
                notes.append(
                    f"REVIZYON_REDDEDILDI: Yetersiz gerekçe "
                    f"(revision_probability={prob:.2f}). "
                    f"Mevcut son fiyat {ps.request.price}% olarak sabit "
                    f"kalmaktadır (bu değeri kullan, previous_price'ı "
                    f"DEĞİL). Daha detaylı gerekçe ile tekrar talep "
                    f"edilebilir."
                )
                context_note = " ".join(notes)
                reply = generate_response(ps.request,
                                          context_note=context_note)
                ps.conversation.append({"role": "assistant", "content": reply})
            else:
                context_note = " ".join(notes) if notes else ""
                price_deposit(ps, revision_probability=prob)
                if ps.request.pricing_no > pn_before:
                    priced_now = True
                    state.session_label_set(state.active_session_index)
                reply = generate_response(ps.request,
                                          context_note=context_note)
                ps.conversation.append({"role": "assistant", "content": reply})
        else:
            context_note = " ".join(notes) if notes else ""
            reply = generate_response(ps.request, context_note=context_note)
            ps.conversation.append({"role": "assistant", "content": reply})

    if priced_now:
        _record_price_card(ps)

    return _render_partial(state)


# --------------------------------------------------------------------------- #
# Session management
# --------------------------------------------------------------------------- #

@deposit_bp.route("/clear", methods=["GET", "POST"])
@login_required
def chat_clear():
    state = _get_state()
    state.active_pricing_session.clear()
    state.session_labels[state.active_session_index].enabled = False
    return _render_partial(state)


@deposit_bp.route("/pricing-session-add", methods=["GET", "POST"])
@login_required
def session_add():
    state = _get_state()
    state.session_add()
    return render_template("deposit/info_panel_left.html",
                           lbl=state.session_labels)


@deposit_bp.route("/pricing-session-switch", methods=["GET", "POST"])
@login_required
def session_switch():
    state = _get_state()
    idx = int(request.form.get("session_index", "0"))
    state.session_switch(idx)
    return _render_partial(state)


@deposit_bp.route("/pricing-session-ask-confirmation", methods=["GET", "POST"])
@login_required
def session_confirm_ask():
    state = _get_state()
    state.pricing_sessions[state.active_session_index].confirmation_state = 1
    return _render_partial(state)


@deposit_bp.route("/pricing-session-cancel-confirmation",
                  methods=["GET", "POST"])
@login_required
def session_confirm_cancel():
    state = _get_state()
    ps = state.pricing_sessions[state.active_session_index]
    ps.confirmation_state = 0
    state.session_labels[state.active_session_index].confirmed = False
    return _render_partial(state)


@deposit_bp.route("/pricing-session-confirm", methods=["GET", "POST"])
@login_required
def session_confirm():
    state = _get_state()
    ps = state.pricing_sessions[state.active_session_index]
    ps.confirmation_state = 2
    state.session_labels[state.active_session_index].confirmed = True
    return _render_partial(state)


# --------------------------------------------------------------------------- #
# Deposit-return interaction
# --------------------------------------------------------------------------- #

@deposit_bp.route("/return-session-set", methods=["POST"])
@login_required
def return_session_set():
    state = _get_state()
    def _safe_int(v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default
    return_index  = _safe_int(request.form.get("return_index"))
    session_index = _safe_int(request.form.get("session_index"))
    if return_index < 0 or return_index >= len(state.deposit_returns):
        log.warning("return-session-set: invalid return_index=%s", return_index)
        return "0"
    state.deposit_returns[return_index].session_index = session_index
    state.active_session_index = session_index
    state.active_pricing_session = state.pricing_sessions[session_index]
    return "0"


@deposit_bp.route("/return-list", methods=["GET", "POST"])
@login_required
def generate_return_list():
    state = _get_state()
    state.show_intro = 2
    remaining = [r for r in state.deposit_returns if r.session_index == -1]

    dummy = PricingSession()
    for r in remaining:
        first_nm = r.cust_info.full_nm.split(" ")[0]
        txt = (f"Base: {r.cust_info.cust_id} ({first_nm}), "
               f"{r.currency} {r.amount}, Vade: {r.tenor} gün, "
               f"Faiz oranı: {r.return_price}%")
        dummy.conversation.append({"role": "choice", "content": txt})

    return render_template(
        "deposit/chat_partial.html",
        show_intro=state.show_intro,
        ps=dummy,
        rt=remaining,
        lbl=state.session_labels,
        username=_username(),
    )


# --------------------------------------------------------------------------- #
# App reset + dummy
# --------------------------------------------------------------------------- #

@deposit_bp.route("/app-reset", methods=["POST"])
@login_required
def app_reset():
    log.info("app_reset for sicil=%s", current_user.sicil)
    state = _get_state()
    state.init(state.number_of_sessions)
    state.init_generate_choices()
    return _render_partial(state)


@deposit_bp.route("/test", methods=["GET", "POST"])
@login_required
def dummy_response():
    state = _get_state()
    ps = state.active_pricing_session
    ps.conversation.append({"role": "user",
                            "content": request.form.get("user_input", "")})
    n = len(ps.conversation) * 2
    g.message = {"role": "assistant",
                 "content": ("Asistan rolü yapan bir dummy'yim."
                             if n < 9 else
                             "Ben robotum beni ikna edemezsin")}
    ps.conversation.append(g.message)
    return render_template("deposit/dummy.html")