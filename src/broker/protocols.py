"""Broker client protocols for dependency injection.

Composable Protocol definitions that describe the capabilities a broker client
must provide.  Consumers type-hint against the narrowest protocol they need.
IBKRClient satisfies all of these via structural subtyping (no inheritance
required).

Usage::

    from src.broker.protocols import BrokerClient, MarketDataProvider

    class MyService:
        def __init__(self, broker: MarketDataProvider):
            self.broker = broker
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


# ═══════════════════════════════════════════════════════════════════════════
# SUB-PROTOCOLS (narrow interfaces — prefer these for type hints)
# ═══════════════════════════════════════════════════════════════════════════


@runtime_checkable
class ConnectionManager(Protocol):
    """Connection lifecycle management."""

    def connect(self, retry: bool = True) -> bool: ...
    def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...
    def ensure_connected(self) -> None: ...
    def get_account_id(self) -> str | None: ...
    def is_paper_account(self) -> bool: ...


@runtime_checkable
class MarketDataProvider(Protocol):
    """Price and quote retrieval."""

    def get_stock_price(self, symbol: str) -> float | None: ...

    def get_option_quote(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        right: str,
    ) -> dict | None: ...

    def get_market_data(
        self, contract: Any, snapshot: bool = True,
    ) -> dict | None: ...

    def get_quote_sync(
        self, contract: Any, timeout: float | None = None,
    ) -> Any: ...

    def is_market_open(self, exchange: str = "NYSE") -> dict: ...

    def get_contract_details(self, symbol: str) -> dict | None: ...


@runtime_checkable
class ContractFactory(Protocol):
    """Contract creation and qualification."""

    def get_stock_contract(
        self,
        symbol: str,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> Any: ...

    def get_index_contract(
        self,
        symbol: str,
        exchange: str = "CBOE",
        currency: str = "USD",
    ) -> Any: ...

    def get_option_contract(
        self,
        symbol: str,
        expiration: str,
        strike: float,
        right: str = "P",
        exchange: str = "SMART",
        trading_class: str = "",
        currency: str = "USD",
    ) -> Any: ...

    def qualify_contract(self, contract: Any) -> Any | None: ...


@runtime_checkable
class OrderManager(Protocol):
    """Order placement, modification, and cancellation."""

    def place_order_sync(
        self, contract: Any, order: Any, reason: str = "",
    ) -> Any: ...

    def cancel_order_sync(
        self, order_id: int, reason: str = "",
    ) -> bool: ...

    def modify_order_sync(
        self, trade: Any, new_limit: float, reason: str = "",
    ) -> Any: ...


@runtime_checkable
class AccountProvider(Protocol):
    """Account, position, and trade queries."""

    def get_account_summary(self) -> dict: ...
    def get_positions(self) -> list: ...
    def get_portfolio(self) -> list: ...
    def get_trades(self) -> list: ...
    def get_orders(self) -> list: ...
    def get_fills(self) -> list: ...
    def get_executions(self) -> list: ...


@runtime_checkable
class MarginProvider(Protocol):
    """Margin requirement queries."""

    def get_actual_margin(
        self, contract: Any, quantity: int = 1, max_retries: int = 3,
    ) -> Optional[float]: ...

    def get_margin_requirement(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        option_type: str,
        contracts: int,
        action: str = "SELL",
    ) -> Optional[float]: ...


# ═══════════════════════════════════════════════════════════════════════════
# COMPOSITE PROTOCOL (full broker client — use when narrow protocol won't do)
# ═══════════════════════════════════════════════════════════════════════════


@runtime_checkable
class BrokerClient(
    ConnectionManager,
    MarketDataProvider,
    ContractFactory,
    OrderManager,
    AccountProvider,
    MarginProvider,
    Protocol,
):
    """Full broker client protocol — union of all sub-protocols.

    Use this when a consumer needs methods from multiple sub-protocols.
    Prefer narrower sub-protocols when possible for better testability.
    """

    def wait(self, seconds: float) -> None: ...
