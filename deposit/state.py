"""
Application state: dataclasses and AppState singleton.

Frontend contract — fields referenced by templates:
  ps.conversation[*].role / .content         (role: user|assistant|choice)
  ps.priced_before, ps.confirmation_state
  ps.cust_info.{cust_id, full_nm, total_aum}
  ps.request.{cust_id, tenor, amount, currency, price, pricing_no}
  rt[*].{idx, cust_info, amount, currency, tenor, return_price}
  lbl[*].{enabled, confirmed, text}
"""

from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------------------- #
# Domain dataclasses
# --------------------------------------------------------------------------- #

@dataclass
class CustomerInfo:
    cust_id: int = 0
    full_nm: str = ""
    total_aum: float = 0.0

    def clear(self):
        self.cust_id = 0
        self.full_nm = ""
        self.total_aum = 0.0


@dataclass
class PricingRequest:
    cust_id: int = 0
    tenor: int = 0
    amount: float = 0.0
    currency: str = ""
    price: float = 0.0
    pricing_no: int = 0
    previous_price: float = 0.0
    previous_query: str = ""
    valid: bool = False

    def clear(self):
        self.cust_id = 0
        self.tenor = 0
        self.amount = 0.0
        self.currency = ""
        self.price = 0.0
        self.pricing_no = 0
        self.previous_price = 0.0
        self.previous_query = ""
        self.valid = False


@dataclass
class PricingRevisionCheck:
    is_price_request: bool = False
    demanded_price: float = 0.0

    def clear(self):
        self.is_price_request = False
        self.demanded_price = 0.0


@dataclass
class DepositReturn:
    idx: int
    cust_info: CustomerInfo
    mtrty_dt: int = 0
    amount: float = 0.0
    currency: str = ""
    tenor: int = 0
    return_price: float = 0.0
    suggested_price: float = 0.0
    session_index: int = -1


@dataclass
class PriceSnapshot:
    """One pricing event's state, shown as an inline card in the chat."""
    amount: float = 0.0
    currency: str = ""
    price: float = 0.0
    pricing_no: int = 0


@dataclass
class PricingSession:
    priced_before: bool = False
    choice_made_or_ignored: bool = False
    confirmation_state: int = 0  # 0 none, 1 asking, 2 confirmed
    deposit_return: Optional[DepositReturn] = None
    request: PricingRequest = field(default_factory=PricingRequest)
    cust_info: CustomerInfo = field(default_factory=CustomerInfo)
    revision_check: PricingRevisionCheck = field(default_factory=PricingRevisionCheck)
    conversation: list = field(default_factory=list)
    # Indices into `conversation` at which a PriceSnapshot card should render.
    newPrices: list = field(default_factory=list)
    # Parallel list to newPrices — priceHistory[i] is the snapshot for the
    # conversation entry at index newPrices[i].
    priceHistory: list = field(default_factory=list)
    # First non-zero demanded_price the user ever stated. Once set, never
    # overwritten — silently ignores subsequent user demands. The user is
    # NOT informed of this latch.
    latched_demanded_price: float = 0.0

    def clear(self):
        self.priced_before = False
        self.choice_made_or_ignored = False
        self.confirmation_state = 0
        self.deposit_return = None
        self.request.clear()
        self.cust_info.clear()
        self.revision_check.clear()
        self.conversation = []
        self.newPrices = []
        self.priceHistory = []
        self.latched_demanded_price = 0.0

    def record_price_snapshot(self, conversation_index: int) -> None:
        """Mark the given conversation entry as price-bearing and append a
        snapshot of the current request state to priceHistory."""
        self.newPrices.append(conversation_index)
        self.priceHistory.append(PriceSnapshot(
            amount=self.request.amount,
            currency=self.request.currency,
            price=self.request.price,
            pricing_no=self.request.pricing_no,
        ))


@dataclass
class PricingSessionLabel:
    confirmed: bool = False
    enabled: bool = False
    text: str = ""


# --------------------------------------------------------------------------- #
# Label helpers
# --------------------------------------------------------------------------- #

def _fmt_amount(amt: float) -> str:
    if amt >= 1_000_000_000:
        return f"{round(amt / 1_000_000_000, 1)}B"
    if amt >= 1_000_000:
        return f"{int(amt / 1_000_000)}M"
    if amt >= 1_000:
        return f"{int(amt / 1_000)}K"
    return str(int(amt))


def label_from_session(ps: PricingSession) -> str:
    first_nm = ps.cust_info.full_nm.split(" ")[0] if ps.cust_info.full_nm else "—"
    return (
        f"{first_nm} {ps.request.currency} {_fmt_amount(ps.request.amount)}, "
        f"{ps.request.tenor}g, {ps.request.price}%"
    )


def label_from_return(dr: DepositReturn) -> str:
    first_nm = dr.cust_info.full_nm.split(" ")[0] if dr.cust_info.full_nm else "—"
    return (
        f"[DÖNÜŞ] {first_nm} {dr.currency} {_fmt_amount(dr.amount)}, "
        f"{dr.tenor}g, {dr.return_price}%"
    )


# --------------------------------------------------------------------------- #
# AppState singleton
# --------------------------------------------------------------------------- #

@dataclass
class AppState:
    active_session_index: int = 0
    active_pricing_session: Optional[PricingSession] = None
    number_of_sessions: int = 10
    session_count: int = 0
    show_intro: int = 0
    pricing_sessions: list = field(default_factory=list)
    session_labels: list = field(default_factory=list)
    deposit_returns: list = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def init(self, size: int = 10):
        self.number_of_sessions = size
        self.pricing_sessions = [PricingSession() for _ in range(size)]
        self.session_labels = [PricingSessionLabel() for _ in range(size)]
        self.active_session_index = 0
        self.active_pricing_session = self.pricing_sessions[0]
        self.session_count = 1
        self.show_intro = 3
        self.session_label_set(0, default=True)

    def init_generate_choices(self):
        """Populate the first session with deposit-return choice cards."""
        from .mock_data import default_deposit_returns
        self.deposit_returns = default_deposit_returns()
        ps = self.active_pricing_session
        ps.choice_made_or_ignored = False
        for dr in self.deposit_returns:
            if dr.session_index == -1:
                first_nm = dr.cust_info.full_nm.split(" ")[0]
                txt = (f"Base: {dr.cust_info.cust_id} ({first_nm}), "
                       f"{dr.currency} {dr.amount}, Vade: {dr.tenor} gün, "
                       f"Faiz oranı: {dr.return_price}%")
                ps.conversation.append({"role": "choice", "content": txt})

    # ------------------------------------------------------------------ #
    def session_label_set(self, index: int, default: bool = False):
        lbl = self.session_labels[index]
        lbl.enabled = True
        if default:
            lbl.text = "Yeni Fiyat"
            return
        ps = self.pricing_sessions[index]
        if ps.deposit_return is not None:
            lbl.text = label_from_return(ps.deposit_return)
        else:
            lbl.text = label_from_session(ps)

    def session_add(self) -> bool:
        if self.session_count >= self.number_of_sessions:
            return False
        self.session_count += 1
        self.session_label_set(self.session_count - 1, default=True)
        self.show_intro = 0
        return True

    def session_switch(self, index: int):
        if index >= self.session_count:
            index = self.session_count - 1
        self.active_session_index = index
        self.active_pricing_session = self.pricing_sessions[index]
        if self.show_intro == 2:
            self.show_intro = 0
