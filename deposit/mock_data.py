"""Mock customer database and deposit returns (no EDW / no external API)."""

from .state import CustomerInfo, DepositReturn


# --------------------------------------------------------------------------- #
# Customer database
# --------------------------------------------------------------------------- #

_CUSTOMERS = {
    12688316:  CustomerInfo(12688316,  "MUSTAFA YILMAZ",                 184_683_484.82),
    86741789:  CustomerInfo(86741789,
                            "ENUYGUN COM İNTERNETBİLGİ HİZMETLERİ TEKNOLOJİ VE TİCARET AŞ",
                            148_530_000.00),
    15228702:  CustomerInfo(15228702,  "MAHMUT MAHİR KUŞÇULU",           266_490_659.66),
    9621148:   CustomerInfo(9621148,
                            "KREA İÇERİK HİZMETLERİ VE PRODÜKSİYON A.Ş.",
                            1_488_191_797.72),
    109796767: CustomerInfo(109796767, "AYŞE DEMİR",                      42_100_000.00),
    11203696:  CustomerInfo(11203696,  "CEM ÖZTÜRK",                      58_700_000.00),
    48583223:  CustomerInfo(48583223,  "ZEYNEP KAYA",                     91_450_000.00),
    545050:    CustomerInfo(545050,    "MEHMET ARSLAN",                   12_300_000.00),
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
        DepositReturn(0, get_customer_info(12688316),
                      20240726, 101_450_000, "TRY", 32, 50.5),
        DepositReturn(1, get_customer_info(86741789),
                      20240726,  37_500_000, "TRY", 92, 59.0),
        DepositReturn(2, get_customer_info(15228702),
                      20240726,  30_582_000, "TRY", 32, 50.5),
    ]
