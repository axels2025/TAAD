"""Portfolio stress testing for naked put positions.

Estimates portfolio impact under adverse market scenarios:
- Market drops (5%, 10%, 20%)
- Single stock crash (one underlying drops 20%)
- VIX spike (margin expansion)
- Correlation crisis (all positions move against simultaneously)

For each scenario, estimates new P&L, delta impact, margin changes,
and whether a margin call would be triggered.
"""

from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from src.execution.position_monitor import PositionStatus


@dataclass
class PositionImpact:
    """Impact of a stress scenario on a single position.

    Attributes:
        symbol: Stock symbol
        strike: Option strike
        contracts: Number of contracts
        current_pnl: Current P&L before stress
        stressed_pnl: Estimated P&L after stress
        pnl_change: Change in P&L
        new_underlying: Estimated underlying price after stress
        margin_estimate: Estimated new margin requirement
    """

    symbol: str
    strike: float
    contracts: int
    current_pnl: float
    stressed_pnl: float
    pnl_change: float
    new_underlying: float
    margin_estimate: float


@dataclass
class StressTestResult:
    """Result of a single stress scenario.

    Attributes:
        scenario_name: Name of the scenario
        description: What the scenario simulates
        total_pnl_change: Aggregate P&L change across all positions
        worst_position: Symbol of the worst-hit position
        worst_pnl_change: P&L change of the worst position
        total_margin_estimate: Estimated total margin after stress
        margin_call_risk: Whether margin call is likely
        position_impacts: Per-position breakdown
    """

    scenario_name: str
    description: str
    total_pnl_change: float
    worst_position: str
    worst_pnl_change: float
    total_margin_estimate: float
    margin_call_risk: bool
    position_impacts: list[PositionImpact] = field(default_factory=list)


class PortfolioStressTest:
    """Estimate portfolio impact under adverse market scenarios.

    Uses simplified models for quick estimation:
    - Option premium approximated via delta × stock move
    - Margin estimated via 25% of strike formula
    - No vol surface modeling (approximation only)

    Example:
        >>> tester = PortfolioStressTest(account_equity=100000)
        >>> results = tester.run_all_scenarios(positions)
        >>> for name, result in results.items():
        ...     print(f"{name}: P&L change ${result.total_pnl_change:,.0f}")
    """

    # Standard scenarios
    SCENARIOS = {
        "market_drop_5pct": {
            "description": "All underlyings drop 5%",
            "stock_move_pct": -0.05,
            "margin_multiplier": 1.1,
        },
        "market_drop_10pct": {
            "description": "All underlyings drop 10%",
            "stock_move_pct": -0.10,
            "margin_multiplier": 1.25,
        },
        "market_drop_20pct": {
            "description": "All underlyings drop 20% (crash)",
            "stock_move_pct": -0.20,
            "margin_multiplier": 1.5,
        },
        "vix_spike_35": {
            "description": "VIX spikes to 35+ (margin expansion, premium spike)",
            "stock_move_pct": -0.03,
            "margin_multiplier": 1.5,
        },
        "correlation_crisis": {
            "description": "All positions move against simultaneously (tail risk)",
            "stock_move_pct": -0.15,
            "margin_multiplier": 1.4,
        },
    }

    def __init__(self, account_equity: float = 100_000):
        """Initialize stress tester.

        Args:
            account_equity: Current account equity (NetLiquidation)
        """
        self.account_equity = account_equity

    def run_scenario(
        self,
        scenario_name: str,
        positions: list[PositionStatus],
    ) -> StressTestResult:
        """Run a single stress scenario against current portfolio.

        Args:
            scenario_name: Key from SCENARIOS dict
            positions: Current open positions

        Returns:
            StressTestResult with estimated impacts
        """
        if scenario_name not in self.SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario_name}")

        scenario = self.SCENARIOS[scenario_name]
        stock_move_pct = scenario["stock_move_pct"]
        margin_multiplier = scenario["margin_multiplier"]
        description = scenario["description"]

        impacts = []
        total_pnl_change = 0.0
        total_margin = 0.0
        worst_symbol = ""
        worst_change = 0.0

        for pos in positions:
            impact = self._estimate_position_impact(
                pos, stock_move_pct, margin_multiplier
            )
            impacts.append(impact)
            total_pnl_change += impact.pnl_change
            total_margin += impact.margin_estimate

            if impact.pnl_change < worst_change:
                worst_change = impact.pnl_change
                worst_symbol = impact.symbol

        # Margin call risk: if total margin > account equity
        margin_call = total_margin > self.account_equity * 0.90

        return StressTestResult(
            scenario_name=scenario_name,
            description=description,
            total_pnl_change=total_pnl_change,
            worst_position=worst_symbol,
            worst_pnl_change=worst_change,
            total_margin_estimate=total_margin,
            margin_call_risk=margin_call,
            position_impacts=impacts,
        )

    def run_all_scenarios(
        self,
        positions: list[PositionStatus],
    ) -> dict[str, StressTestResult]:
        """Run all standard stress scenarios.

        Args:
            positions: Current open positions

        Returns:
            Dict of scenario_name → StressTestResult
        """
        if not positions:
            logger.info("No positions to stress test")
            return {}

        results = {}
        for name in self.SCENARIOS:
            results[name] = self.run_scenario(name, positions)

        logger.info(
            f"Stress test complete: {len(self.SCENARIOS)} scenarios, "
            f"{len(positions)} positions"
        )

        return results

    def run_single_stock_crash(
        self,
        positions: list[PositionStatus],
        crash_pct: float = -0.20,
    ) -> dict[str, StressTestResult]:
        """Run single-stock crash scenario for each symbol.

        Tests: what if ONE stock crashes while others stay flat?

        Args:
            positions: Current open positions
            crash_pct: How much the single stock drops (default -20%)

        Returns:
            Dict of symbol → StressTestResult
        """
        results = {}
        symbols = {pos.symbol for pos in positions}

        for target_symbol in symbols:
            impacts = []
            total_pnl_change = 0.0
            total_margin = 0.0

            for pos in positions:
                if pos.symbol == target_symbol:
                    impact = self._estimate_position_impact(pos, crash_pct, 1.3)
                else:
                    # Other positions unaffected
                    impact = PositionImpact(
                        symbol=pos.symbol,
                        strike=pos.strike,
                        contracts=pos.contracts,
                        current_pnl=pos.current_pnl,
                        stressed_pnl=pos.current_pnl,
                        pnl_change=0.0,
                        new_underlying=pos.underlying_price or pos.strike * 1.15,
                        margin_estimate=pos.strike * 0.20 * 100 * pos.contracts,
                    )

                impacts.append(impact)
                total_pnl_change += impact.pnl_change
                total_margin += impact.margin_estimate

            margin_call = total_margin > self.account_equity * 0.90

            results[target_symbol] = StressTestResult(
                scenario_name=f"single_stock_crash_{target_symbol}",
                description=f"{target_symbol} drops {abs(crash_pct):.0%}, others flat",
                total_pnl_change=total_pnl_change,
                worst_position=target_symbol,
                worst_pnl_change=min(
                    (i.pnl_change for i in impacts if i.symbol == target_symbol),
                    default=0.0,
                ),
                total_margin_estimate=total_margin,
                margin_call_risk=margin_call,
                position_impacts=impacts,
            )

        return results

    def _estimate_position_impact(
        self,
        position: PositionStatus,
        stock_move_pct: float,
        margin_multiplier: float,
    ) -> PositionImpact:
        """Estimate impact on a single position from a stock move.

        Uses delta approximation for premium change:
        - Premium change ≈ delta × stock_price_change × contracts × 100
        - For short puts, a stock drop INCREASES the premium (bad for us)

        Args:
            position: Position to stress
            stock_move_pct: How much the stock moves (negative = drop)
            margin_multiplier: How much margin expands

        Returns:
            PositionImpact with estimated values
        """
        # Current underlying price (use strike * 1.15 as fallback for ~15% OTM)
        underlying = position.underlying_price or position.strike * 1.15
        new_underlying = underlying * (1 + stock_move_pct)

        # Delta approximation for premium change
        # For short puts: stock drops → put premium increases → loss for us
        delta = position.delta if position.delta is not None else -0.20
        stock_dollar_move = underlying * stock_move_pct
        premium_change = abs(delta) * abs(stock_dollar_move)

        # If stock dropped, put premium increased (loss for short put seller)
        if stock_move_pct < 0:
            pnl_change = -premium_change * position.contracts * 100
        else:
            pnl_change = premium_change * position.contracts * 100

        stressed_pnl = position.current_pnl + pnl_change

        # Margin estimate: base margin × multiplier
        base_margin = position.strike * 0.20 * 100 * position.contracts
        margin_estimate = base_margin * margin_multiplier

        return PositionImpact(
            symbol=position.symbol,
            strike=position.strike,
            contracts=position.contracts,
            current_pnl=position.current_pnl,
            stressed_pnl=stressed_pnl,
            pnl_change=pnl_change,
            new_underlying=new_underlying,
            margin_estimate=margin_estimate,
        )
