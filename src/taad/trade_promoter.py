"""Promote matched trade lifecycles from import schema into public.trades.

Converts TradeMatchingLog rows (matched STO+BTC/expiration pairs) into Trade
records that the enrichment engine and learning pipeline can consume.

Usage:
    from src.taad.trade_promoter import promote_matches_to_trades
    result = promote_matches_to_trades(session, account_id="YOUR_ACCOUNT")
"""

from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger
from sqlalchemy.orm import Session, aliased

from src.data.models import Trade
from src.taad.models import IBKRRawImport, TradeMatchingLog

# Maps match_type from trade_matcher.py to exit_reason values
_EXIT_REASON_MAP = {
    "sell_to_open+buy_to_close": "buy_to_close",
    "sell_to_open+expiration": "expiration",
    "sell_to_open+assignment": "assignment",
}


@dataclass
class PromotionResult:
    """Summary of a promotion run."""

    promoted: int = 0
    skipped_already_promoted: int = 0
    skipped_no_exit_date: int = 0
    errors: int = 0
    error_messages: list[str] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return self.promoted + self.skipped_already_promoted + self.skipped_no_exit_date + self.errors


def promote_matches_to_trades(
    session: Session,
    account_id: str | None = None,
    dry_run: bool = False,
) -> PromotionResult:
    """Promote matched trade lifecycles into public.trades for enrichment.

    For each unpromoted TradeMatchingLog row, creates a Trade record with
    trade_source='ibkr_import' and links it back via matched_trade_id.

    Args:
        session: SQLAlchemy session (caller must commit)
        account_id: Optional filter by IBKR account ID
        dry_run: If True, compute results but don't write to DB

    Returns:
        PromotionResult with counts of promoted/skipped/errors
    """
    result = PromotionResult()

    OpenImport = aliased(IBKRRawImport, name="open_imp")
    CloseImport = aliased(IBKRRawImport, name="close_imp")

    query = (
        session.query(TradeMatchingLog, OpenImport, CloseImport)
        .join(OpenImport, TradeMatchingLog.raw_import_id_open == OpenImport.id)
        .outerjoin(CloseImport, TradeMatchingLog.raw_import_id_close == CloseImport.id)
        .filter(TradeMatchingLog.matched_trade_id.is_(None))  # only unpromoted
    )

    if account_id:
        query = query.filter(OpenImport.account_id == account_id)

    rows = query.order_by(OpenImport.trade_date).all()

    logger.info(f"Found {len(rows)} unpromoted matched trades to process")

    for match_log, open_imp, close_imp in rows:
        try:
            trade = _build_trade_from_match(match_log, open_imp, close_imp)

            if trade is None:
                result.skipped_no_exit_date += 1
                continue

            # Check for existing trade with same ibkr_execution_id (idempotency)
            existing = (
                session.query(Trade)
                .filter(Trade.ibkr_execution_id == trade.ibkr_execution_id)
                .first()
            )
            if existing:
                # Link the match log to the existing trade and move on
                if not dry_run:
                    match_log.matched_trade_id = existing.trade_id
                result.skipped_already_promoted += 1
                continue

            if not dry_run:
                session.add(trade)
                session.flush()  # get trade.id assigned
                match_log.matched_trade_id = trade.trade_id

            result.promoted += 1

        except Exception as e:
            result.errors += 1
            msg = (
                f"Error promoting match {match_log.id} "
                f"({open_imp.underlying_symbol} {open_imp.strike}): {e}"
            )
            result.error_messages.append(msg)
            logger.error(msg)

    logger.info(
        f"Promotion complete: {result.promoted} promoted, "
        f"{result.skipped_already_promoted} already promoted, "
        f"{result.skipped_no_exit_date} skipped (no exit date), "
        f"{result.errors} errors"
    )

    return result


def _build_trade_from_match(
    match_log: TradeMatchingLog,
    open_imp: IBKRRawImport,
    close_imp: IBKRRawImport | None,
) -> Trade | None:
    """Build a Trade object from a matched trade lifecycle.

    Returns None if no exit date can be determined (defense-in-depth).
    """
    # Derive exit date
    if close_imp and close_imp.trade_date:
        exit_date = datetime.combine(close_imp.trade_date, datetime.min.time())
    elif match_log.match_type == "sell_to_open+expiration" and open_imp.expiry:
        exit_date = datetime.combine(open_imp.expiry, datetime.min.time())
    elif match_log.match_type == "sell_to_open+assignment" and open_imp.expiry:
        exit_date = datetime.combine(open_imp.expiry, datetime.min.time())
    else:
        logger.warning(
            f"Match {match_log.id} has no computable exit date, skipping"
        )
        return None

    # Core fields
    entry_premium = abs(open_imp.price)
    contracts = abs(open_imp.quantity)
    multiplier = open_imp.multiplier or 100
    exit_premium = abs(close_imp.price) if close_imp else 0.0

    # P&L
    gross_pnl = (entry_premium - exit_premium) * contracts * multiplier

    # Commission
    entry_commission = abs(open_imp.commission) if open_imp.commission else 0.0
    exit_commission = abs(close_imp.commission) if close_imp and close_imp.commission else 0.0
    commission = entry_commission + exit_commission

    net_pnl = gross_pnl - commission

    # Profit percentage (relative to entry premium collected)
    premium_collected = entry_premium * contracts * multiplier
    profit_pct = net_pnl / premium_collected if premium_collected > 0 else 0.0

    # Entry date as datetime
    entry_date = datetime.combine(open_imp.trade_date, datetime.min.time())

    # DTE
    dte = (open_imp.expiry - open_imp.trade_date).days if open_imp.expiry else 0

    # Days held
    days_held = (exit_date - entry_date).days

    # Exit reason
    exit_reason = _EXIT_REASON_MAP.get(match_log.match_type, match_log.match_type)

    # Option type
    option_type = "PUT" if open_imp.put_call == "P" else "CALL"

    # Deterministic trade_id from the opening execution ID
    trade_id = f"IBKR_{open_imp.ibkr_exec_id}"

    return Trade(
        trade_id=trade_id,
        symbol=open_imp.underlying_symbol,
        strike=open_imp.strike,
        expiration=open_imp.expiry,
        option_type=option_type,
        entry_date=entry_date,
        entry_premium=entry_premium,
        contracts=contracts,
        exit_date=exit_date,
        exit_premium=exit_premium,
        exit_reason=exit_reason,
        profit_loss=net_pnl,
        profit_pct=profit_pct,
        roi=profit_pct,
        days_held=days_held,
        otm_pct=None,  # enrichment will fill from stock price
        dte=dte,
        commission=commission,
        trade_source="ibkr_import",
        account_id=open_imp.account_id,
        ibkr_execution_id=open_imp.ibkr_exec_id,
        enrichment_status="pending",
    )
