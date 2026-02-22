"""Trade recording for NakedTrader positions.

Creates Trade database records with nakedtrader-specific fields including
bracket order IDs and status tracking.
"""

import uuid
from datetime import datetime

from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import Trade
from src.data.repositories import TradeRepository
from src.nakedtrader.order_manager import BracketOrderResult
from src.nakedtrader.strike_selector import StrikeSelection


def record_trade(
    session: Session,
    selection: StrikeSelection,
    bracket: BracketOrderResult,
    fill_price: float | None = None,
    fill_time: datetime | None = None,
    vix: float | None = None,
    account_id: str | None = None,
) -> Trade:
    """Record a NakedTrader trade in the database.

    Creates a Trade record with trade_strategy='nakedtrader' and bracket
    order tracking fields.

    Args:
        session: Database session.
        selection: Strike selection result with quote and price data.
        bracket: Bracket order result with order IDs.
        fill_price: Actual fill price (if filled), else uses bid.
        fill_time: Actual fill time (if filled), else uses now.
        vix: VIX level at entry.
        account_id: IBKR account ID.

    Returns:
        Created Trade record.
    """
    repo = TradeRepository(session)
    now = fill_time or datetime.now()
    entry_premium = fill_price or selection.quote.bid

    trade_id = f"NT-{uuid.uuid4().hex[:12]}"

    trade = Trade(
        trade_id=trade_id,
        symbol=selection.symbol,
        strike=selection.quote.strike,
        expiration=datetime.strptime(selection.quote.expiration, "%Y%m%d").date(),
        option_type="PUT",
        entry_date=now,
        entry_premium=entry_premium,
        contracts=1,  # Will be updated from config in workflow
        otm_pct=selection.quote.otm_pct,
        dte=selection.quote.dte,
        vix_at_entry=vix,
        spy_price_at_entry=selection.underlying_price,
        trade_strategy="nakedtrader",
        trade_source="paper",
        bracket_status="active",
        order_id=bracket.parent_order_id,
        exit_order_id=bracket.profit_take_order_id,
        stop_order_id=bracket.stop_loss_order_id,
        account_id=account_id,
    )

    repo.create(trade)
    logger.info(
        f"Recorded trade {trade_id}: {selection.symbol} "
        f"${selection.quote.strike}P @ ${entry_premium:.2f}"
    )

    return trade
