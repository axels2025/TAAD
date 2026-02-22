#!/usr/bin/env python3
"""Backfill entry snapshots for trades that are missing them.

Run from the trading_agent directory:
    python -m scripts.backfill_entry_snapshots [--all] [--dry-run]

Options:
    --all       Backfill all trades (open + closed). Default: open trades only.
    --dry-run   Show what would be backfilled without saving.

Requires IBKR connection (TWS/Gateway running) for live market data.
"""

import sys
import os
import argparse
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import get_db_session
from src.data.models import Trade, TradeEntrySnapshot
from src.services.entry_snapshot import EntrySnapshotService
from src.tools.ibkr_client import IBKRClient
from src.config.base import IBKRConfig

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)


def get_trades_without_snapshots(session, open_only=True):
    """Find trades that don't have entry snapshots."""
    query = (
        session.query(Trade)
        .outerjoin(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
        .filter(TradeEntrySnapshot.id.is_(None))
    )
    if open_only:
        query = query.filter(Trade.exit_date.is_(None))
    return query.all()


def get_stock_price(ibkr_client, symbol):
    """Get current stock price for a symbol."""
    import asyncio
    contract = ibkr_client.get_stock_contract(symbol)
    qualified = ibkr_client.qualify_contract(contract)
    if not qualified:
        return None
    quote = asyncio.run(ibkr_client.get_quote(qualified, timeout=5.0))
    if quote.last and quote.last > 0:
        return quote.last
    if quote.bid and quote.ask:
        return (quote.bid + quote.ask) / 2
    return None


def main():
    parser = argparse.ArgumentParser(description="Backfill entry snapshots for trades")
    parser.add_argument("--all", action="store_true", help="Backfill all trades, not just open ones")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without saving")
    args = parser.parse_args()

    open_only = not args.all

    logger.info("Connecting to IBKR...")
    config = IBKRConfig()
    ibkr_client = IBKRClient(config)
    ibkr_client.connect()
    
    try:
        snapshot_service = EntrySnapshotService(ibkr_client)

        with get_db_session() as session:
            trades = get_trades_without_snapshots(session, open_only=open_only)
            
            if not trades:
                logger.info("All trades already have entry snapshots. Nothing to backfill.")
                return

            logger.info(f"Found {len(trades)} trades without entry snapshots:")
            for t in trades:
                status = "OPEN" if t.exit_date is None else f"CLOSED ({t.exit_reason})"
                logger.info(f"  {t.symbol} ${t.strike} {t.option_type or 'P'} exp={t.expiration} "
                           f"contracts={t.contracts} premium=${t.entry_premium} [{status}]")

            if args.dry_run:
                logger.info("Dry run — not saving anything.")
                return

            success = 0
            failed = 0

            for trade in trades:
                try:
                    # Get current stock price
                    stock_price = get_stock_price(ibkr_client, trade.symbol)
                    if not stock_price:
                        logger.warning(f"  ✗ Could not get stock price for {trade.symbol}, skipping")
                        failed += 1
                        continue

                    # Use trade.dte (DTE at entry time), not expiration - today
                    dte = trade.dte

                    logger.info(f"  Capturing snapshot for {trade.symbol} ${trade.strike} "
                               f"(stock=${stock_price:.2f}, DTE={dte})...")

                    snapshot = snapshot_service.capture_entry_snapshot(
                        trade_id=trade.id,
                        opportunity_id=None,
                        symbol=trade.symbol,
                        strike=trade.strike,
                        expiration=trade.expiration,
                        option_type=trade.option_type or "PUT",
                        entry_premium=trade.entry_premium,
                        contracts=trade.contracts,
                        stock_price=stock_price,
                        dte=dte,
                        source="backfill",
                    )

                    snapshot_service.save_snapshot(snapshot, session)
                    logger.info(f"  ✓ {trade.symbol}: quality={snapshot.data_quality_score:.1%}")
                    success += 1

                except Exception as e:
                    logger.error(f"  ✗ {trade.symbol}: {e}")
                    failed += 1

            logger.info(f"\nDone. Success: {success}, Failed: {failed}")

    finally:
        ibkr_client.disconnect()


if __name__ == "__main__":
    main()
