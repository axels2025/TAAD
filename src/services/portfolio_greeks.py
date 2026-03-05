"""Portfolio-level Greeks aggregation service.

Aggregates per-position Greeks from PositionSnapshot into portfolio totals.
Supports two modes:
  1. DB-only (fast): reads latest snapshots from database
  2. Live refresh (slow): fetches fresh Greeks from IBKR, updates snapshots, returns aggregated
"""

from datetime import date, datetime
from typing import Any, Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import PositionSnapshot, Trade
from src.utils.timezone import us_trading_date


def get_portfolio_greeks(db_session: Session) -> dict[str, Any]:
    """Aggregate Greeks from latest PositionSnapshot for each open trade.

    Reads the most recent snapshot per open trade and sums Greeks weighted
    by contract count for portfolio-level totals.

    Args:
        db_session: Active database session

    Returns:
        Dict with 'positions' list and 'portfolio' summary.
        Returns empty positions list if no open trades exist.
    """
    open_trades = (
        db_session.query(Trade).filter(Trade.exit_date.is_(None)).all()
    )

    if not open_trades:
        return {
            "positions": [],
            "portfolio": {
                "total_delta": 0.0,
                "total_gamma": 0.0,
                "total_theta": 0.0,
                "total_vega": 0.0,
                "position_count": 0,
                "total_pnl": 0.0,
                "avg_iv": None,
                "last_updated": None,
            },
        }

    today = us_trading_date()
    now = datetime.now()
    positions = []
    total_delta = 0.0
    total_gamma = 0.0
    total_theta = 0.0
    total_vega = 0.0
    total_pnl = 0.0
    iv_sum = 0.0
    iv_count = 0
    latest_captured = None

    for trade in open_trades:
        # Get most recent snapshot for this trade
        snapshot = (
            db_session.query(PositionSnapshot)
            .filter(PositionSnapshot.trade_id == trade.id)
            .order_by(
                PositionSnapshot.snapshot_date.desc(),
                PositionSnapshot.id.desc(),
            )
            .first()
        )

        dte = (trade.expiration - today).days if trade.expiration else None

        pos_entry: dict[str, Any] = {
            "symbol": trade.symbol,
            "strike": trade.strike,
            "option_type": trade.option_type,
            "contracts": trade.contracts,
            "dte": dte,
            "delta": None,
            "gamma": None,
            "theta": None,
            "vega": None,
            "iv": None,
            "current_pnl": None,
            "snapshot_age_minutes": None,
        }

        if snapshot:
            pos_entry["delta"] = snapshot.delta
            pos_entry["gamma"] = snapshot.gamma
            pos_entry["theta"] = snapshot.theta
            pos_entry["vega"] = snapshot.vega
            pos_entry["iv"] = snapshot.iv
            pos_entry["current_pnl"] = snapshot.current_pnl

            # Snapshot age in minutes
            if snapshot.captured_at:
                age = (now - snapshot.captured_at).total_seconds() / 60
                pos_entry["snapshot_age_minutes"] = round(age, 1)
                if latest_captured is None or snapshot.captured_at > latest_captured:
                    latest_captured = snapshot.captured_at

            # Contract-weighted aggregation
            qty = trade.contracts
            if snapshot.delta is not None:
                total_delta += snapshot.delta * qty
            if snapshot.gamma is not None:
                total_gamma += snapshot.gamma * qty
            if snapshot.theta is not None:
                total_theta += snapshot.theta * qty
            if snapshot.vega is not None:
                total_vega += snapshot.vega * qty
            if snapshot.current_pnl is not None:
                total_pnl += snapshot.current_pnl
            if snapshot.iv is not None:
                iv_sum += snapshot.iv
                iv_count += 1

        positions.append(pos_entry)

    avg_iv = (iv_sum / iv_count) if iv_count > 0 else None

    return {
        "positions": positions,
        "portfolio": {
            "total_delta": round(total_delta, 4),
            "total_gamma": round(total_gamma, 6),
            "total_theta": round(total_theta, 4),
            "total_vega": round(total_vega, 4),
            "position_count": len(open_trades),
            "total_pnl": round(total_pnl, 2),
            "avg_iv": round(avg_iv, 4) if avg_iv is not None else None,
            "last_updated": latest_captured.isoformat() if latest_captured else None,
        },
    }


def refresh_greeks(ibkr_client, db_session: Session) -> dict[str, Any]:
    """Fetch live Greeks from IBKR, update snapshots, return aggregated.

    For each open trade:
      1. Builds an option contract and requests market data from IBKR
      2. Upserts today's PositionSnapshot with fresh Greeks
      3. Returns the same aggregated format as get_portfolio_greeks()

    Args:
        ibkr_client: Connected IBKRClient instance
        db_session: Active database session

    Returns:
        Same dict format as get_portfolio_greeks() with live data
    """
    from src.services.position_snapshot import PositionSnapshotService

    service = PositionSnapshotService(ibkr_client, db_session)
    open_trades = (
        db_session.query(Trade).filter(Trade.exit_date.is_(None)).all()
    )

    if not open_trades:
        logger.info("No open trades to refresh Greeks for")
        return get_portfolio_greeks(db_session)

    today = us_trading_date()
    refreshed = 0

    # Fetch VIX/SPY once for all positions
    market_ctx = service._fetch_market_context()

    for trade in open_trades:
        try:
            # Check for existing snapshot today
            existing = (
                db_session.query(PositionSnapshot)
                .filter(
                    PositionSnapshot.trade_id == trade.id,
                    PositionSnapshot.snapshot_date == today,
                )
                .first()
            )

            if existing:
                # Delete existing so _capture_single_position creates a fresh one
                db_session.delete(existing)
                db_session.flush()

            snapshot = service._capture_single_position(trade, today, market_ctx)
            if snapshot:
                db_session.add(snapshot)
                refreshed += 1

        except Exception as e:
            logger.error(f"Failed to refresh Greeks for trade {trade.id} ({trade.symbol}): {e}")

    db_session.commit()
    logger.info(f"Refreshed Greeks for {refreshed}/{len(open_trades)} positions")

    return get_portfolio_greeks(db_session)
