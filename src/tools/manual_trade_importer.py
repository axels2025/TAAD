"""Manual trade importer.

Handles importing manual trade JSON files into the database,
merging with Barchart results, and managing file lifecycle.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import ScanOpportunity, ScanResult
from src.utils.timezone import us_trading_date
from src.data.repositories import ScanRepository
from src.tools.manual_trade_entry import ManualTradeFile, ManualTradeManager


class ManualTradeImporter:
    """Imports manual trade JSON files into database."""

    def __init__(self, session: Session):
        """Initialize importer.

        Args:
            session: Database session
        """
        self.session = session
        self.scan_repo = ScanRepository(session)
        self.manager = ManualTradeManager()

        logger.info("Initialized ManualTradeImporter")

    def import_pending_trades(self) -> tuple[int, int]:
        """Import all pending manual trade files.

        Returns:
            Tuple of (files_imported, opportunities_imported)
        """
        pending_files = self.manager.load_pending_files()

        if not pending_files:
            logger.info("No pending manual trade files to import")
            return (0, 0)

        files_imported = 0
        opportunities_imported = 0

        for file_path, trade_file in pending_files:
            try:
                count = self._import_single_file(file_path, trade_file)
                opportunities_imported += count
                files_imported += 1

                # Move to imported directory
                self.manager.move_to_imported(file_path)

            except Exception as e:
                logger.error(f"Failed to import {file_path.name}: {e}")
                # Don't move file if import failed
                continue

        logger.info(
            f"Imported {files_imported} files with {opportunities_imported} opportunities"
        )

        # Commit all imports
        self.session.commit()

        return (files_imported, opportunities_imported)

    def _import_single_file(
        self, file_path: Path, trade_file: ManualTradeFile
    ) -> int:
        """Import a single manual trade file.

        Args:
            file_path: Path to JSON file
            trade_file: Parsed trade file

        Returns:
            Number of opportunities imported
        """
        # Create scan result record
        scan_result = ScanResult(
            scan_timestamp=datetime.fromisoformat(trade_file.scan_timestamp),
            source="manual",
            config_used={"filename": file_path.name},
            total_candidates=len(trade_file.opportunities),
            validated_count=0,  # Will be updated after IBKR validation
            execution_time_seconds=0.0,
            notes=trade_file.notes,
        )

        scan_result = self.scan_repo.create_scan(scan_result)
        logger.debug(f"Created scan_result (ID: {scan_result.id}) for {file_path.name}")

        # Import each opportunity
        imported_count = 0
        for entry in trade_file.opportunities:
            try:
                # Calculate DTE
                exp_date = datetime.strptime(entry.expiration, "%Y-%m-%d")
                dte = (exp_date.date() - us_trading_date()).days

                # Create opportunity record
                opportunity = ScanOpportunity(
                    scan_id=scan_result.id,
                    symbol=entry.symbol,
                    strike=entry.strike,
                    expiration=exp_date.date(),
                    option_type=entry.option_type,
                    premium=entry.premium,
                    bid=entry.bid,
                    ask=entry.ask,
                    delta=entry.delta,
                    otm_pct=entry.otm_pct,
                    dte=dte,
                    stock_price=entry.stock_price,
                    trend=entry.trend,
                    volume=entry.volume,
                    open_interest=entry.open_interest,
                    iv=entry.iv,
                    validation_status="pending",  # Needs IBKR validation
                    source="manual",
                    entry_notes=entry.notes,
                    executed=False,
                )

                self.scan_repo.add_opportunity(opportunity)
                imported_count += 1

                logger.debug(
                    f"Imported: {entry.symbol} ${entry.strike} {entry.expiration}"
                )

            except Exception as e:
                logger.warning(
                    f"Failed to import opportunity {entry.symbol} ${entry.strike}: {e}"
                )
                continue

        logger.info(f"Imported {imported_count} opportunities from {file_path.name}")
        return imported_count

    def get_pending_manual_opportunities(
        self, limit: Optional[int] = None
    ) -> list[ScanOpportunity]:
        """Get pending manual opportunities from database.

        Args:
            limit: Maximum number to return

        Returns:
            List of unexecuted manual opportunities from both web and CLI sources
        """
        query = (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.source.in_(["manual", "manual_web"]))
            .filter(ScanOpportunity.executed == False)
            .join(ScanResult)
            .order_by(ScanResult.scan_timestamp.desc())
        )

        if limit:
            query = query.limit(limit)

        return query.all()

    def merge_with_barchart_opportunities(
        self, manual_opps: list[ScanOpportunity], barchart_opps: list
    ) -> list:
        """Merge manual and Barchart opportunities, handling duplicates.

        Strategy: If same option appears in both, keep manual notes but use
        Barchart pricing (more up-to-date).

        Args:
            manual_opps: Manual opportunities from database
            barchart_opps: Barchart opportunities (validated)

        Returns:
            Merged list of opportunities (as dicts)
        """
        # Convert manual opps to dict format
        manual_dict_list = []
        for opp in manual_opps:
            manual_dict_list.append(
                {
                    "id": opp.id,
                    "symbol": opp.symbol,
                    "strike": opp.strike,
                    "expiration": opp.expiration.strftime("%Y-%m-%d"),
                    "option_type": opp.option_type,
                    "premium": opp.premium,
                    "bid": opp.bid,
                    "ask": opp.ask,
                    "delta": opp.delta,
                    "otm_pct": opp.otm_pct,
                    "dte": opp.dte,
                    "stock_price": opp.stock_price,
                    "trend": opp.trend,
                    "margin_required": opp.margin_required,
                    "margin_efficiency": opp.margin_efficiency,
                    "source": "manual",
                    "notes": opp.entry_notes,
                }
            )

        # Create lookup for Barchart opps
        barchart_lookup = {}
        for opp in barchart_opps:
            key = (opp.symbol, opp.strike, opp.expiration)
            barchart_lookup[key] = opp

        # Merge: keep manual notes, update with Barchart pricing
        merged = []
        manual_keys_added = set()

        for manual_opp in manual_dict_list:
            key = (
                manual_opp["symbol"],
                manual_opp["strike"],
                manual_opp["expiration"],
            )

            if key in barchart_lookup:
                # Duplicate found - merge (manual notes + Barchart pricing)
                barchart_opp = barchart_lookup[key]

                merged_opp = manual_opp.copy()
                # Update with Barchart data
                merged_opp.update(
                    {
                        "premium": barchart_opp.premium,
                        "bid": barchart_opp.ibkr_bid,
                        "ask": barchart_opp.ibkr_ask,
                        "spread_pct": barchart_opp.spread_pct,
                        "stock_price": barchart_opp.stock_price,
                        "margin_required": barchart_opp.margin_required,
                        "margin_efficiency": barchart_opp.margin_efficiency,
                        "trend": barchart_opp.trend,
                        "source": "manual+barchart",  # Indicate merge
                    }
                )

                merged.append(merged_opp)
                manual_keys_added.add(key)

                logger.debug(
                    f"Merged duplicate: {key[0]} ${key[1]} (manual notes + Barchart pricing)"
                )
            else:
                # No duplicate - keep manual as-is
                merged.append(manual_opp)
                manual_keys_added.add(key)

        # Add Barchart-only opportunities
        for opp in barchart_opps:
            key = (opp.symbol, opp.strike, opp.expiration)

            if key not in manual_keys_added:
                # Convert ValidatedOption to dict
                merged.append(opp.to_dict())

        logger.info(
            f"Merged {len(manual_dict_list)} manual + {len(barchart_opps)} Barchart "
            f"= {len(merged)} total opportunities"
        )

        return merged
