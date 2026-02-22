"""Scan result persistence.

Handles saving Barchart and IBKR scan results to database for
historical tracking and analysis.
"""

from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import ScanOpportunity, ScanResult
from src.utils.timezone import us_trading_date
from src.data.repositories import ScanRepository
from src.tools.barchart_scanner import BarchartScanOutput
from src.tools.ibkr_validator import ValidatedOption


class ScanPersistence:
    """Persists scan results to database."""

    def __init__(self, session: Session):
        """Initialize persistence handler.

        Args:
            session: Database session
        """
        self.session = session
        self.scan_repo = ScanRepository(session)

        logger.info("Initialized ScanPersistence")

    def save_barchart_scan(
        self,
        scan_output: BarchartScanOutput,
        execution_time: float,
        validated_results: Optional[list[ValidatedOption]] = None,
    ) -> ScanResult:
        """Save Barchart scan results to database.

        Args:
            scan_output: Barchart scan output
            execution_time: Time taken for scan (seconds)
            validated_results: Optional IBKR validation results

        Returns:
            Created ScanResult record
        """
        # Create scan result record
        scan_result = ScanResult(
            scan_timestamp=scan_output.scan_timestamp,
            source="barchart",
            config_used=scan_output.config_used,
            total_candidates=scan_output.total_results,
            validated_count=len(validated_results) if validated_results else 0,
            execution_time_seconds=execution_time,
            notes=None,
        )

        scan_result = self.scan_repo.create_scan(scan_result)
        logger.info(f"Created scan_result (ID: {scan_result.id}) for Barchart scan")

        # Save Barchart opportunities
        for result in scan_output.results:
            # Calculate DTE
            exp_date = datetime.strptime(result.expiration_date, "%Y-%m-%d")
            dte = (exp_date.date() - us_trading_date()).days

            opportunity = ScanOpportunity(
                scan_id=scan_result.id,
                symbol=result.underlying_symbol,
                strike=result.strike,
                expiration=exp_date.date(),
                option_type=result.option_type.upper(),
                premium=result.option_price,
                bid=result.bid,
                ask=result.ask,
                delta=result.delta,
                gamma=result.gamma,
                theta=result.theta,
                vega=result.vega,
                iv=result.volatility,
                dte=dte,
                stock_price=result.last_price,
                volume=result.volume,
                open_interest=result.open_interest,
                validation_status="barchart_only",
                source="barchart",
                executed=False,
            )

            self.scan_repo.add_opportunity(opportunity)

        logger.info(
            f"Saved {len(scan_output.results)} Barchart opportunities to database"
        )

        # Update validated opportunities if provided
        if validated_results:
            self._update_validated_opportunities(scan_result.id, validated_results)

        # Commit
        self.session.commit()

        return scan_result

    def _update_validated_opportunities(
        self, scan_id: int, validated_results: list[ValidatedOption]
    ) -> None:
        """Update opportunities with IBKR validation data.

        Args:
            scan_id: Scan ID
            validated_results: List of validated options
        """
        # Create lookup of validated options
        validated_lookup = {}
        for validated in validated_results:
            key = (validated.symbol, validated.strike, validated.expiration)
            validated_lookup[key] = validated

        # Get all opportunities for this scan
        opportunities = self.scan_repo.get_opportunities_by_scan(scan_id)

        # Update matching opportunities
        for opp in opportunities:
            key = (opp.symbol, opp.strike, opp.expiration.strftime("%Y-%m-%d"))

            if key in validated_lookup:
                validated = validated_lookup[key]

                # Update with IBKR validation data
                opp.premium = validated.premium
                opp.bid = validated.ibkr_bid
                opp.ask = validated.ibkr_ask
                opp.spread_pct = validated.spread_pct
                opp.stock_price = validated.stock_price
                opp.otm_pct = validated.otm_pct
                opp.margin_required = validated.margin_required
                opp.margin_efficiency = validated.margin_efficiency
                opp.trend = validated.trend
                opp.validation_status = "ibkr_validated"

                logger.debug(f"Updated validation for {opp.symbol} ${opp.strike}")
            else:
                # Not validated - mark as rejected
                opp.validation_status = "rejected"
                opp.rejection_reason = "Did not pass IBKR validation filters"

        logger.info(f"Updated {len(validated_results)} opportunities with IBKR validation data")

    def mark_opportunity_executed(
        self, opportunity_id: int, trade_id: str
    ) -> None:
        """Mark an opportunity as executed and link to trade.

        Args:
            opportunity_id: Opportunity database ID
            trade_id: Trade ID from trades table
        """
        self.scan_repo.mark_opportunity_executed(opportunity_id, trade_id)
        self.session.commit()
        logger.info(f"Marked opportunity {opportunity_id} as executed (trade: {trade_id})")
