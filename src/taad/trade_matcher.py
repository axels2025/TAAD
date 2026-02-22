"""Match raw IBKR executions into trade lifecycles.

Pairing logic:
- STO (Sell to Open) is the entry
- BTC (Buy to Close) is the exit
- Expiration (no closing trade, past expiry date) = expired worthless
- Assignment (no BTC, but stock assignment) = assigned

Matching key: (account_id, underlying_symbol, strike, expiry, put_call)
Within each key, STO records are matched to BTC records by date order.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from src.utils.timezone import us_trading_date

from loguru import logger
from sqlalchemy.orm import Session

from src.taad.models import IBKRRawImport, TradeMatchingLog


@dataclass
class MatchedTrade:
    """A matched trade lifecycle (entry + exit)."""

    open_import: IBKRRawImport
    close_import: IBKRRawImport | None  # None for expiration/assignment
    match_type: str  # sell_to_open+buy_to_close, sell_to_open+expiration, sell_to_open+assignment
    confidence_score: float
    notes: str = ""


def _match_key(record: IBKRRawImport) -> tuple:
    """Generate a matching key for a raw import record."""
    return (
        record.account_id,
        record.underlying_symbol,
        record.strike,
        record.expiry,
        record.put_call,
    )


def match_trades(
    session: Session,
    account_id: str | None = None,
    reference_date: date | None = None,
) -> list[MatchedTrade]:
    """Match unmatched STO records with their corresponding BTC/expiration/assignment.

    Args:
        session: SQLAlchemy session.
        account_id: Filter to a specific account (optional).
        reference_date: Date to use for expiration checks (defaults to today).

    Returns:
        List of MatchedTrade objects.
    """
    if reference_date is None:
        reference_date = us_trading_date()

    # Query all unmatched option records
    query = session.query(IBKRRawImport).filter(
        IBKRRawImport.matched == False,  # noqa: E712
        IBKRRawImport.asset_category == "OPT",
    )
    if account_id:
        query = query.filter(IBKRRawImport.account_id == account_id)

    unmatched = query.order_by(IBKRRawImport.trade_date, IBKRRawImport.id).all()

    if not unmatched:
        logger.info("No unmatched option records found")
        return []

    logger.info(f"Processing {len(unmatched)} unmatched option records")

    # Group by matching key
    sto_by_key: dict[tuple, list[IBKRRawImport]] = {}
    btc_by_key: dict[tuple, list[IBKRRawImport]] = {}

    for record in unmatched:
        key = _match_key(record)
        if record.is_sell_to_open():
            sto_by_key.setdefault(key, []).append(record)
        elif record.is_buy_to_close():
            btc_by_key.setdefault(key, []).append(record)
        else:
            logger.debug(
                f"Skipping non-STO/BTC record: {record.underlying_symbol} "
                f"{record.buy_sell}/{record.open_close}"
            )

    matched_trades: list[MatchedTrade] = []

    # Match STO → BTC
    for key, sto_records in sto_by_key.items():
        btc_records = btc_by_key.get(key, [])

        # Sort both lists by trade_date
        sto_records.sort(key=lambda r: (r.trade_date, r.id))
        btc_records.sort(key=lambda r: (r.trade_date, r.id))

        # Match STO to BTC in order, tracking remaining quantities
        sto_idx = 0
        btc_idx = 0
        sto_remaining: dict[int, int] = {r.id: abs(r.quantity) for r in sto_records}
        btc_remaining: dict[int, int] = {r.id: abs(r.quantity) for r in btc_records}

        while sto_idx < len(sto_records) and btc_idx < len(btc_records):
            sto = sto_records[sto_idx]
            btc = btc_records[btc_idx]

            # BTC must be after STO
            if btc.trade_date < sto.trade_date:
                btc_idx += 1
                continue

            sto_qty = sto_remaining[sto.id]
            btc_qty = btc_remaining[btc.id]

            if sto_qty == btc_qty:
                # Perfect match
                matched_trades.append(MatchedTrade(
                    open_import=sto,
                    close_import=btc,
                    match_type="sell_to_open+buy_to_close",
                    confidence_score=1.0,
                    notes=f"Matched {sto_qty} contracts",
                ))
                sto_remaining[sto.id] = 0
                btc_remaining[btc.id] = 0
                sto_idx += 1
                btc_idx += 1

            elif sto_qty > btc_qty:
                # Partial close — BTC closes some of the STO
                matched_trades.append(MatchedTrade(
                    open_import=sto,
                    close_import=btc,
                    match_type="sell_to_open+buy_to_close",
                    confidence_score=0.9,
                    notes=f"Partial close: {btc_qty} of {abs(sto.quantity)} contracts",
                ))
                sto_remaining[sto.id] -= btc_qty
                btc_remaining[btc.id] = 0
                btc_idx += 1

            else:
                # BTC closes more than this STO — consume STO fully, continue BTC
                matched_trades.append(MatchedTrade(
                    open_import=sto,
                    close_import=btc,
                    match_type="sell_to_open+buy_to_close",
                    confidence_score=0.9,
                    notes=f"Partial close: {sto_qty} of {abs(btc.quantity)} BTC contracts",
                ))
                btc_remaining[btc.id] -= sto_qty
                sto_remaining[sto.id] = 0
                sto_idx += 1

        # Remaining unmatched STOs — check for expiration
        for sto in sto_records:
            if sto_remaining.get(sto.id, 0) <= 0:
                continue

            if sto.expiry and sto.expiry < reference_date:
                # Past expiry with no BTC — expired worthless
                matched_trades.append(MatchedTrade(
                    open_import=sto,
                    close_import=None,
                    match_type="sell_to_open+expiration",
                    confidence_score=0.95,
                    notes=f"Expired {sto.expiry.isoformat()}, {sto_remaining[sto.id]} contracts",
                ))
                sto_remaining[sto.id] = 0

    logger.info(f"Matched {len(matched_trades)} trade lifecycles")
    return matched_trades


def persist_matches(
    session: Session,
    matches: list[MatchedTrade],
) -> int:
    """Write matched trades to the database and mark raw imports as matched.

    Args:
        session: SQLAlchemy session.
        matches: List of MatchedTrade objects from match_trades().

    Returns:
        Number of matches persisted.
    """
    count = 0
    for match in matches:
        # Create matching log entry
        log_entry = TradeMatchingLog(
            raw_import_id_open=match.open_import.id,
            raw_import_id_close=match.close_import.id if match.close_import else None,
            match_type=match.match_type,
            confidence_score=match.confidence_score,
            match_notes=match.notes,
        )
        session.add(log_entry)

        # Mark raw imports as matched
        match.open_import.matched = True
        if match.close_import:
            match.close_import.matched = True

        count += 1

    session.flush()
    logger.info(f"Persisted {count} trade matches to database")
    return count
