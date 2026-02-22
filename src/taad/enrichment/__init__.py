"""TAAD Phase 2 — Historical trade enrichment pipeline.

Enriches imported trades with reconstructed market context from yfinance,
IBKR historical data, and Black-Scholes approximations.
"""

from src.taad.enrichment.bs_iv_solver import (
    BSResult,
    bs_put_price,
    bs_call_price,
    solve_iv,
    calculate_greeks,
    solve_iv_and_greeks,
    get_risk_free_rate,
)
from src.taad.enrichment.engine import (
    EnrichmentResult,
    EnrichmentBatchResult,
    HistoricalEnrichmentEngine,
    calculate_historical_quality,
)
from src.taad.enrichment.historical_context import (
    HistoricalMarketContext,
    build_historical_context,
    classify_vol_regime,
    classify_market_regime,
    is_opex_week,
    days_to_next_fomc,
    FOMC_DATES,
)
from src.taad.enrichment.historical_indicators import (
    TechnicalIndicators,
    calculate_indicators_from_bars,
    calculate_trend_from_bars,
    calculate_hv_20,
    calculate_hv_rank,
    calculate_beta,
)
from src.taad.enrichment.providers import (
    OHLCV,
    OptionSnapshot,
    HistoricalDataProvider,
    YFinanceProvider,
    IBKRHistoricalProvider,
    FallbackChainProvider,
)
from src.taad.enrichment.barchart_scraper import (
    BarchartScraperProvider,
    BarchartHistoricalCache,
    BARCHART_EARLIEST_DATE,
)
from src.taad.enrichment.barchart_playwright import (
    PlaywrightBarchartProvider,
    PLAYWRIGHT_EARLIEST_DATE,
)

__all__ = [
    # B-S IV Solver
    "BSResult",
    "bs_put_price",
    "bs_call_price",
    "solve_iv",
    "calculate_greeks",
    "solve_iv_and_greeks",
    "get_risk_free_rate",
    # Engine
    "EnrichmentResult",
    "EnrichmentBatchResult",
    "HistoricalEnrichmentEngine",
    "calculate_historical_quality",
    # Context
    "HistoricalMarketContext",
    "build_historical_context",
    "classify_vol_regime",
    "classify_market_regime",
    "is_opex_week",
    "days_to_next_fomc",
    "FOMC_DATES",
    # Indicators
    "TechnicalIndicators",
    "calculate_indicators_from_bars",
    "calculate_trend_from_bars",
    "calculate_hv_20",
    "calculate_hv_rank",
    "calculate_beta",
    # Providers
    "OHLCV",
    "OptionSnapshot",
    "HistoricalDataProvider",
    "YFinanceProvider",
    "IBKRHistoricalProvider",
    "FallbackChainProvider",
    # Barchart Scraper
    "BarchartScraperProvider",
    "BarchartHistoricalCache",
    "BARCHART_EARLIEST_DATE",
    # Barchart Playwright Scraper
    "PlaywrightBarchartProvider",
    "PLAYWRIGHT_EARLIEST_DATE",
]
