"""Mock customer database and deposit returns (no EDW / no external API).

KVKK: buradaki kayıtların tamamı SENTETİKTİR ve müşteri adları platformun
maskeleme biçimiyle (`X*** Y***`) yazılır — düz PII repoda tutulmaz
(CLAUDE.md KVKK kuralı: `FULL_NM` daima maskeli yazılır). Müşteri numaraları
da gerçek numaralarla çakışmayacak sentetik bir blokta (1000000x) durur.
Yalnız demo/geliştirme amaçlıdır.
"""

from .state import CustomerInfo, DepositReturn


# --------------------------------------------------------------------------- #
# Customer database — sentetik, maskeli
# --------------------------------------------------------------------------- #

_CUSTOMERS = {
    10000001: CustomerInfo(10000001, "A*** K***",          185_000_000.00),
    10000002: CustomerInfo(10000002, "B*** T*** A.Ş.",     148_500_000.00),
    10000003: CustomerInfo(10000003, "C*** Y***",          266_500_000.00),
    10000004: CustomerInfo(10000004, "D*** H*** A.Ş.",   1_488_000_000.00),
    10000005: CustomerInfo(10000005, "E*** S***",           42_100_000.00),
    10000006: CustomerInfo(10000006, "F*** M***",           58_700_000.00),
    10000007: CustomerInfo(10000007, "G*** B***",           91_450_000.00),
    10000008: CustomerInfo(10000008, "H*** N***",           12_300_000.00),
}

_NEW_CUSTOMER_ID = 999_999_999


def get_customer_info(cust_id: int) -> CustomerInfo:
    """Return customer info. Unknown IDs → generic 'YENİ MÜŞTERİ' record.

    Returns a fresh copy so mutation in session state doesn't pollute the
    registry.
    """
    if cust_id in _CUSTOMERS:
        c = _CUSTOMERS[cust_id]
        return CustomerInfo(c.cust_id, c.full_nm, c.total_aum)
    if cust_id == _NEW_CUSTOMER_ID:
        return CustomerInfo(_NEW_CUSTOMER_ID, "YENİ MÜŞTERİ", 0.0)
    return CustomerInfo(cust_id, "YENİ MÜŞTERİ", 0.0)


# --------------------------------------------------------------------------- #
# Deposit returns shown as choice cards on first load
# --------------------------------------------------------------------------- #

def default_deposit_returns() -> list[DepositReturn]:
    return [
        DepositReturn(0, get_customer_info(10000001),
                      20240726, 101_450_000, "TRY", 32, 50.5),
        DepositReturn(1, get_customer_info(10000002),
                      20240726,  37_500_000, "TRY", 92, 59.0),
        DepositReturn(2, get_customer_info(10000003),
                      20240726,  30_582_000, "TRY", 32, 50.5),
    ]
