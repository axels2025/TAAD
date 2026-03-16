"""Broker type re-exports.

Import ib_async types through this module instead of directly from ib_async.
This creates a single seam for future broker replacement — only this file
and ibkr_client.py need to change.

Usage::

    from src.broker.types import Contract, Option, LimitOrder, Quote
"""

# ib_async contract types
from ib_async import Contract, Index, Option, Stock

# ib_async order types
from ib_async import LimitOrder, MarketOrder, Order

# ib_async result types
from ib_async import Trade

# Our custom types (broker-agnostic)
from src.tools.ibkr_client import OrderAuditEntry, Quote

__all__ = [
    # Contracts
    "Contract",
    "Index",
    "Option",
    "Stock",
    # Orders
    "LimitOrder",
    "MarketOrder",
    "Order",
    # Results
    "Trade",
    # Custom
    "OrderAuditEntry",
    "Quote",
]
