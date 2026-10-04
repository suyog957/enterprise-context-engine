from functools import lru_cache

from enterprise_context.config import get_settings
from enterprise_context.domain.transactions import ProcurementTransactions


@lru_cache(maxsize=1)
def get_procurement_transactions() -> ProcurementTransactions:
    return ProcurementTransactions(get_settings())
