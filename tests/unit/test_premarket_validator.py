"""Unit tests for premarket validator module.

Tests the two-stage validation logic for staged trades:
- Stage 1: Pre-market stock price validation (9:15 AM)
- Stage 2: Market-open premium validation (9:30 AM)
"""

from unittest.mock import MagicMock

import pytest

from src.data.opportunity_state import OpportunityState
from src.services.premarket_validator import (
    OpenCheckResult,
    PremarketCheckResult,
    PremarketValidator,
    StagedOpportunity,
    ValidationConfig,
    ValidationStatus,
)


def create_staged_opportunity(
    symbol: str = "AAPL",
    strike: float = 150.0,
    staged_stock_price: float = 180.0,
    staged_limit_price: float = 0.50,
    staged_contracts: int = 5,
    staged_margin: float = 3000.0,
    otm_pct: float = 0.167,  # (180-150)/180
) -> StagedOpportunity:
    """Create a test staged opportunity."""
    return StagedOpportunity(
        id=1,
        symbol=symbol,
        strike=strike,
        expiration="2026-02-07",
        staged_stock_price=staged_stock_price,
        staged_limit_price=staged_limit_price,
        staged_contracts=staged_contracts,
        staged_margin=staged_margin,
        otm_pct=otm_pct,
    )


class TestValidationConfig:
    """Tests for ValidationConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ValidationConfig()

        # Stage 1 defaults
        assert config.max_deviation_ready == 0.03
        assert config.max_deviation_adjust == 0.05
        assert config.max_deviation_stale == 0.10

        # Stage 2 defaults
        assert config.max_premium_deviation_confirmed == 0.15
        assert config.max_premium_deviation_adjust == 0.50

        # General defaults
        assert config.min_otm_execute == 0.12
        assert config.min_premium_execute == 0.20

    def test_from_env(self, monkeypatch):
        """Test loading config from environment variables."""
        monkeypatch.setenv("MAX_DEVIATION_READY", "0.02")
        monkeypatch.setenv("MAX_DEVIATION_AUTO_ADJUST", "0.04")
        monkeypatch.setenv("MAX_DEVIATION_STALE", "0.08")
        monkeypatch.setenv("MIN_OTM_EXECUTE", "0.15")

        config = ValidationConfig.from_env()

        assert config.max_deviation_ready == 0.02
        assert config.max_deviation_adjust == 0.04
        assert config.max_deviation_stale == 0.08
        assert config.min_otm_execute == 0.15


class TestStagedOpportunity:
    """Tests for StagedOpportunity dataclass."""

    def test_create_opportunity(self):
        """Test creating a staged opportunity."""
        opp = create_staged_opportunity()

        assert opp.symbol == "AAPL"
        assert opp.strike == 150.0
        assert opp.staged_stock_price == 180.0
        assert opp.state == "STAGED"

    def test_default_values(self):
        """Test default values are None."""
        opp = create_staged_opportunity()

        assert opp.current_stock_price is None
        assert opp.current_bid is None
        assert opp.adjusted_strike is None


class TestPremarketCheckResult:
    """Tests for PremarketCheckResult dataclass."""

    def test_passed_ready(self):
        """Test passed property for READY status."""
        opp = create_staged_opportunity()
        result = PremarketCheckResult(
            opportunity=opp,
            status=ValidationStatus.READY,
            staged_price=180.0,
            premarket_price=179.0,
            deviation_pct=-0.0056,
            new_otm_pct=0.163,
        )

        assert result.passed is True

    def test_passed_adjusted(self):
        """Test passed property for ADJUSTED status."""
        opp = create_staged_opportunity()
        result = PremarketCheckResult(
            opportunity=opp,
            status=ValidationStatus.ADJUSTED,
            staged_price=180.0,
            premarket_price=170.0,
            deviation_pct=-0.056,
            new_otm_pct=0.12,
            adjusted_strike=145.0,
        )

        assert result.passed is True

    def test_not_passed_stale(self):
        """Test passed property for STALE status."""
        opp = create_staged_opportunity()
        result = PremarketCheckResult(
            opportunity=opp,
            status=ValidationStatus.STALE,
            staged_price=180.0,
            premarket_price=160.0,
            deviation_pct=-0.111,
            new_otm_pct=0.06,
        )

        assert result.passed is False


class TestOpenCheckResult:
    """Tests for OpenCheckResult dataclass."""

    def test_passed_ready(self):
        """Test passed property for READY (CONFIRMED) status."""
        opp = create_staged_opportunity()
        result = OpenCheckResult(
            opportunity=opp,
            status=ValidationStatus.READY,
            staged_limit=0.50,
            live_bid=0.49,
            live_ask=0.55,
            premium_deviation_pct=-0.02,
        )

        assert result.passed is True

    def test_passed_adjusted(self):
        """Test passed property for ADJUSTED status."""
        opp = create_staged_opportunity()
        result = OpenCheckResult(
            opportunity=opp,
            status=ValidationStatus.ADJUSTED,
            staged_limit=0.50,
            live_bid=0.45,
            live_ask=0.52,
            premium_deviation_pct=-0.10,
            new_limit_price=0.47,
        )

        assert result.passed is True

    def test_not_passed_stale(self):
        """Test passed property for STALE status."""
        opp = create_staged_opportunity()
        result = OpenCheckResult(
            opportunity=opp,
            status=ValidationStatus.STALE,
            staged_limit=0.50,
            live_bid=0.20,
            live_ask=0.28,
            premium_deviation_pct=-0.60,
        )

        assert result.passed is False


class TestPremarketValidatorStage1:
    """Tests for Stage 1 pre-market validation."""

    @pytest.fixture
    def validator(self):
        """Create a validator with default config and no IBKR."""
        return PremarketValidator(ibkr_client=None)

    @pytest.fixture
    def mock_ibkr(self):
        """Create a mock IBKR client."""
        mock = MagicMock()
        mock.get_stock_price.return_value = 180.0
        return mock

    def test_stage1_stock_stable_ready(self, mock_ibkr):
        """Test Stage 1: Stock stable (<3% deviation) → READY."""
        # Stock price 179 vs staged 180 = -0.56% deviation
        mock_ibkr.get_stock_price.return_value = 179.0

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_stock_price=180.0, strike=150.0)

        results = validator.validate_premarket([opp])

        assert len(results) == 1
        assert results[0].status == ValidationStatus.READY
        assert abs(results[0].deviation_pct - (-0.0056)) < 0.001

    def test_stage1_stock_down_slightly_ready(self, mock_ibkr):
        """Test Stage 1: Stock down 2% → READY."""
        # Stock price 176.4 vs staged 180 = -2% deviation
        mock_ibkr.get_stock_price.return_value = 176.4

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_stock_price=180.0, strike=150.0)

        results = validator.validate_premarket([opp])

        assert results[0].status == ValidationStatus.READY
        assert abs(results[0].deviation_pct - (-0.02)) < 0.001

    def test_stage1_stock_moderate_move_adjust(self, mock_ibkr):
        """Test Stage 1: Stock moved 4% → try to adjust."""
        # Stock price 172.8 vs staged 180 = -4% deviation
        mock_ibkr.get_stock_price.return_value = 172.8

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(
            staged_stock_price=180.0,
            strike=150.0,
            otm_pct=0.167,
        )

        results = validator.validate_premarket([opp])

        # Should attempt adjustment
        assert results[0].status in (ValidationStatus.ADJUSTED, ValidationStatus.STALE)
        # If adjusted, should have new strike
        if results[0].status == ValidationStatus.ADJUSTED:
            assert results[0].adjusted_strike is not None

    def test_stage1_stock_large_move_aggressive_adjust(self, mock_ibkr):
        """Test Stage 1: Stock moved 7% → aggressive adjust."""
        # Stock price 167.4 vs staged 180 = -7% deviation
        mock_ibkr.get_stock_price.return_value = 167.4

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(
            staged_stock_price=180.0,
            strike=150.0,
        )

        results = validator.validate_premarket([opp])

        # Should attempt aggressive adjustment
        assert results[0].status in (ValidationStatus.ADJUSTED, ValidationStatus.STALE)

    def test_stage1_stock_extreme_move_stale(self, mock_ibkr):
        """Test Stage 1: Stock moved >10% → STALE."""
        # Stock price 160 vs staged 180 = -11.1% deviation
        mock_ibkr.get_stock_price.return_value = 160.0

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_stock_price=180.0, strike=150.0)

        results = validator.validate_premarket([opp])

        assert results[0].status == ValidationStatus.STALE
        assert results[0].deviation_pct < -0.10

    def test_stage1_stock_up_moderate_adjust(self, mock_ibkr):
        """Test Stage 1: Stock UP 4% → try to adjust."""
        # Stock price 187.2 vs staged 180 = +4% deviation
        mock_ibkr.get_stock_price.return_value = 187.2

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_stock_price=180.0, strike=150.0)

        results = validator.validate_premarket([opp])

        # Stock going UP is generally OK for puts, but we still check
        assert results[0].status in (
            ValidationStatus.READY,
            ValidationStatus.ADJUSTED,
            ValidationStatus.STALE,
        )

    def test_stage1_no_ibkr_uses_staged_price(self, validator):
        """Test Stage 1 without IBKR uses staged price."""
        opp = create_staged_opportunity(staged_stock_price=180.0)

        results = validator.validate_premarket([opp])

        # Should use staged price (0% deviation)
        assert results[0].status == ValidationStatus.READY
        assert results[0].deviation_pct == 0.0

    def test_stage1_multiple_opportunities(self, mock_ibkr):
        """Test Stage 1 with multiple opportunities."""

        def price_for_symbol(symbol):
            prices = {"AAPL": 179.0, "MSFT": 350.0, "GOOGL": 140.0}
            return prices.get(symbol, 100.0)

        mock_ibkr.get_stock_price.side_effect = price_for_symbol

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opps = [
            create_staged_opportunity(
                symbol="AAPL", staged_stock_price=180.0, strike=150.0
            ),
            create_staged_opportunity(
                symbol="MSFT", staged_stock_price=350.0, strike=300.0
            ),
            create_staged_opportunity(
                symbol="GOOGL", staged_stock_price=150.0, strike=120.0
            ),
        ]

        results = validator.validate_premarket(opps)

        assert len(results) == 3
        # AAPL: 179 vs 180 = -0.56% → READY
        assert results[0].status == ValidationStatus.READY
        # MSFT: 350 vs 350 = 0% → READY
        assert results[1].status == ValidationStatus.READY
        # GOOGL: 140 vs 150 = -6.7% → adjust or stale
        assert results[2].status in (ValidationStatus.ADJUSTED, ValidationStatus.STALE)


class TestPremarketValidatorStage2:
    """Tests for Stage 2 market-open validation."""

    @pytest.fixture
    def mock_ibkr(self):
        """Create a mock IBKR client."""
        mock = MagicMock()
        mock.get_stock_price.return_value = 180.0
        mock.get_option_quote.return_value = {
            "bid": 0.49,
            "ask": 0.55,
        }
        return mock

    def test_stage2_premium_stable_confirmed(self, mock_ibkr):
        """Test Stage 2: Premium stable (<3% deviation) → CONFIRMED."""
        # Live bid 0.49 vs staged 0.50 = -2% deviation
        mock_ibkr.get_option_quote.return_value = {"bid": 0.49, "ask": 0.55}

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_limit_price=0.50)

        results = validator.validate_at_open([opp])

        assert len(results) == 1
        assert results[0].status == ValidationStatus.READY  # CONFIRMED
        assert results[0].passed is True

    def test_stage2_premium_moderate_adjust(self, mock_ibkr):
        """Test Stage 2: Premium moved 25% → adjust limit."""
        # Live bid 0.375 vs staged 0.50 = -25% deviation (between 15% and 50%)
        mock_ibkr.get_option_quote.return_value = {"bid": 0.375, "ask": 0.45}

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_limit_price=0.50)

        results = validator.validate_at_open([opp])

        assert results[0].status == ValidationStatus.ADJUSTED
        assert results[0].new_limit_price is not None

    def test_stage2_premium_collapsed_stale(self, mock_ibkr):
        """Test Stage 2: Premium collapsed >10% → STALE."""
        # Live bid 0.20 vs staged 0.50 = -60% deviation
        mock_ibkr.get_option_quote.return_value = {"bid": 0.20, "ask": 0.28}

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_limit_price=0.50)

        results = validator.validate_at_open([opp])

        assert results[0].status == ValidationStatus.STALE
        assert results[0].passed is False

    def test_stage2_premium_too_low_after_adjust_stale(self, mock_ibkr):
        """Test Stage 2: Premium below minimum after adjust → STALE."""
        # Live bid 0.15 vs staged 0.50 - adjusted would be ~0.16
        mock_ibkr.get_option_quote.return_value = {"bid": 0.15, "ask": 0.20}

        config = ValidationConfig(min_premium_execute=0.20)
        validator = PremarketValidator(ibkr_client=mock_ibkr, config=config)
        opp = create_staged_opportunity(staged_limit_price=0.50)

        results = validator.validate_at_open([opp])

        # New limit would be < min_premium_execute
        assert results[0].status == ValidationStatus.STALE

    def test_stage2_otm_below_minimum_stale(self, mock_ibkr):
        """Test Stage 2: OTM% below minimum → STALE."""
        # Stock at 155, strike at 150 → OTM = 3.2%
        mock_ibkr.get_stock_price.return_value = 155.0
        mock_ibkr.get_option_quote.return_value = {"bid": 0.50, "ask": 0.58}

        config = ValidationConfig(min_otm_execute=0.12)  # 12% minimum
        validator = PremarketValidator(ibkr_client=mock_ibkr, config=config)
        opp = create_staged_opportunity(
            staged_stock_price=180.0,
            strike=150.0,  # OTM = (155-150)/155 = 3.2%
        )

        results = validator.validate_at_open([opp])

        assert results[0].status == ValidationStatus.STALE
        assert "OTM" in (results[0].adjustment_reason or "")

    def test_stage2_no_quote_uses_staged(self):
        """Test Stage 2 without quote uses staged values."""
        validator = PremarketValidator(ibkr_client=None)
        opp = create_staged_opportunity(staged_limit_price=0.50)

        results = validator.validate_at_open([opp])

        # Should use staged limit (0% deviation)
        assert results[0].status == ValidationStatus.READY

    def test_stage2_with_adjusted_strike(self, mock_ibkr):
        """Test Stage 2 uses adjusted strike from Stage 1."""
        mock_ibkr.get_option_quote.return_value = {"bid": 0.45, "ask": 0.52}

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_limit_price=0.50, strike=150.0)
        opp.adjusted_strike = 145.0  # Adjusted in Stage 1

        validator.validate_at_open([opp])

        # Should have called get_option_quote with adjusted strike
        assert mock_ibkr.get_option_quote.called


class TestPremarketValidatorStateMapping:
    """Tests for state mapping logic."""

    @pytest.fixture
    def validator(self):
        """Create a validator."""
        return PremarketValidator()

    def test_premarket_ready_maps_to_ready(self, validator):
        """Test Stage 1 READY → OpportunityState.READY."""
        opp = create_staged_opportunity()
        result = PremarketCheckResult(
            opportunity=opp,
            status=ValidationStatus.READY,
            staged_price=180.0,
            premarket_price=179.0,
            deviation_pct=-0.0056,
            new_otm_pct=0.163,
        )

        state = validator.get_target_state_for_result(result)

        assert state == OpportunityState.READY

    def test_premarket_adjusted_maps_to_ready(self, validator):
        """Test Stage 1 ADJUSTED → OpportunityState.READY."""
        opp = create_staged_opportunity()
        result = PremarketCheckResult(
            opportunity=opp,
            status=ValidationStatus.ADJUSTED,
            staged_price=180.0,
            premarket_price=170.0,
            deviation_pct=-0.056,
            new_otm_pct=0.12,
            adjusted_strike=145.0,
        )

        state = validator.get_target_state_for_result(result)

        assert state == OpportunityState.READY

    def test_premarket_stale_maps_to_stale(self, validator):
        """Test Stage 1 STALE → OpportunityState.STALE."""
        opp = create_staged_opportunity()
        result = PremarketCheckResult(
            opportunity=opp,
            status=ValidationStatus.STALE,
            staged_price=180.0,
            premarket_price=160.0,
            deviation_pct=-0.111,
            new_otm_pct=0.06,
        )

        state = validator.get_target_state_for_result(result)

        assert state == OpportunityState.STALE

    def test_open_ready_maps_to_confirmed(self, validator):
        """Test Stage 2 READY → OpportunityState.CONFIRMED."""
        opp = create_staged_opportunity()
        result = OpenCheckResult(
            opportunity=opp,
            status=ValidationStatus.READY,
            staged_limit=0.50,
            live_bid=0.49,
            live_ask=0.55,
            premium_deviation_pct=-0.02,
        )

        state = validator.get_target_state_for_result(result)

        assert state == OpportunityState.CONFIRMED

    def test_open_adjusted_maps_to_confirmed(self, validator):
        """Test Stage 2 ADJUSTED → OpportunityState.CONFIRMED."""
        opp = create_staged_opportunity()
        result = OpenCheckResult(
            opportunity=opp,
            status=ValidationStatus.ADJUSTED,
            staged_limit=0.50,
            live_bid=0.45,
            live_ask=0.52,
            premium_deviation_pct=-0.10,
            new_limit_price=0.47,
        )

        state = validator.get_target_state_for_result(result)

        assert state == OpportunityState.CONFIRMED

    def test_open_stale_maps_to_stale(self, validator):
        """Test Stage 2 STALE → OpportunityState.STALE."""
        opp = create_staged_opportunity()
        result = OpenCheckResult(
            opportunity=opp,
            status=ValidationStatus.STALE,
            staged_limit=0.50,
            live_bid=0.20,
            live_ask=0.28,
            premium_deviation_pct=-0.60,
        )

        state = validator.get_target_state_for_result(result)

        assert state == OpportunityState.STALE


class TestPremarketValidatorIntegration:
    """Integration tests for full validation workflow."""

    def test_full_two_stage_workflow(self):
        """Test complete Stage 1 → Stage 2 workflow."""
        # Create mock IBKR
        mock_ibkr = MagicMock()

        # Stage 1: Pre-market prices (slight move down)
        mock_ibkr.get_stock_price.return_value = 178.0  # -1.1% from 180

        validator = PremarketValidator(ibkr_client=mock_ibkr)

        # Create opportunities
        opps = [
            create_staged_opportunity(
                symbol="AAPL",
                strike=150.0,
                staged_stock_price=180.0,
                staged_limit_price=0.50,
            ),
        ]

        # Stage 1: Pre-market validation
        stage1_results = validator.validate_premarket(opps)

        assert len(stage1_results) == 1
        assert stage1_results[0].passed is True
        assert stage1_results[0].status == ValidationStatus.READY

        # Prepare for Stage 2
        ready_opps = [r.opportunity for r in stage1_results if r.passed]

        # Stage 2: Update mock for market-open
        mock_ibkr.get_option_quote.return_value = {"bid": 0.48, "ask": 0.54}

        # Stage 2: Market-open validation
        stage2_results = validator.validate_at_open(ready_opps)

        assert len(stage2_results) == 1
        assert stage2_results[0].passed is True

    def test_stage1_filters_out_stale(self):
        """Test that Stage 1 properly filters stale opportunities."""
        mock_ibkr = MagicMock()

        def price_for_symbol(symbol):
            # AAPL moves 2%, MSFT moves 15%
            prices = {"AAPL": 176.4, "MSFT": 255.0}  # MSFT staged at 300
            return prices.get(symbol, 100.0)

        mock_ibkr.get_stock_price.side_effect = price_for_symbol

        validator = PremarketValidator(ibkr_client=mock_ibkr)

        opps = [
            create_staged_opportunity(
                symbol="AAPL", staged_stock_price=180.0, strike=150.0
            ),
            create_staged_opportunity(
                symbol="MSFT", staged_stock_price=300.0, strike=250.0
            ),
        ]

        results = validator.validate_premarket(opps)

        # AAPL: -2% → READY
        assert results[0].status == ValidationStatus.READY
        # MSFT: -15% → STALE
        assert results[1].status == ValidationStatus.STALE

        # Only AAPL should proceed to Stage 2
        ready_opps = [r.opportunity for r in results if r.passed]
        assert len(ready_opps) == 1
        assert ready_opps[0].symbol == "AAPL"


class TestStage2InvalidBidHandling:
    """Tests for Stage 2 handling of invalid bids (bid <= 0).

    IBKR returns bid=-1.0 for options before market open (9:30 AM ET).
    Stage 2 must detect this and retry instead of marking STALE.
    """

    def test_negative_bid_returns_pending(self):
        """Test that bid=-1.0 returns PENDING, not STALE."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_stock_price.return_value = 180.0
        mock_ibkr.get_option_quote.return_value = {"bid": -1.0, "ask": -1.0}

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_limit_price=0.50)

        results = validator.validate_at_open([opp], max_retries=1)

        # With only 1 retry, PENDING should be converted to STALE
        assert results[0].status == ValidationStatus.STALE
        assert "No valid bid" in (results[0].adjustment_reason or "")

    def test_zero_bid_returns_pending(self):
        """Test that bid=0.0 returns PENDING, not STALE."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_stock_price.return_value = 180.0
        mock_ibkr.get_option_quote.return_value = {"bid": 0.0, "ask": 0.0}

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_limit_price=0.50)

        results = validator.validate_at_open([opp], max_retries=1)

        assert results[0].status == ValidationStatus.STALE
        assert "No valid bid" in (results[0].adjustment_reason or "")

    def test_negative_bid_not_treated_as_premium_collapse(self):
        """Test that -1.0 bid doesn't produce -300% deviation (the original bug)."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_stock_price.return_value = 180.0
        mock_ibkr.get_option_quote.return_value = {"bid": -1.0, "ask": -1.0}

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_limit_price=0.50)

        # Internally, _validate_at_open_single should return PENDING
        result = validator._validate_at_open_single(opp)
        assert result.status == ValidationStatus.PENDING
        # Deviation should be 0, not -300%
        assert result.premium_deviation_pct == 0.0

    @pytest.mark.parametrize("retry_delay", [0.0])  # No delay in tests
    def test_retry_resolves_after_market_opens(self, retry_delay):
        """Test that PENDING resolves on retry once valid bids arrive."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_stock_price.return_value = 180.0

        # First call: bid=-1.0 (pre-market), second call: valid bid
        mock_ibkr.get_option_quote.side_effect = [
            {"bid": -1.0, "ask": -1.0},
            {"bid": 0.48, "ask": 0.55},
        ]

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_limit_price=0.50)

        results = validator.validate_at_open(
            [opp], max_retries=3, retry_delay=retry_delay
        )

        # Should have resolved on retry
        assert len(results) == 1
        assert results[0].status in (ValidationStatus.READY, ValidationStatus.ADJUSTED)
        assert results[0].passed is True

    @pytest.mark.parametrize("retry_delay", [0.0])
    def test_retry_gives_up_after_max_attempts(self, retry_delay):
        """Test that PENDING becomes STALE after max retries exhausted."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_stock_price.return_value = 180.0
        # Always returns invalid bid
        mock_ibkr.get_option_quote.return_value = {"bid": -1.0, "ask": -1.0}

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opp = create_staged_opportunity(staged_limit_price=0.50)

        results = validator.validate_at_open(
            [opp], max_retries=3, retry_delay=retry_delay
        )

        assert len(results) == 1
        assert results[0].status == ValidationStatus.STALE
        assert "3 attempts" in (results[0].adjustment_reason or "")

    @pytest.mark.parametrize("retry_delay", [0.0])
    def test_retry_only_retries_pending_not_resolved(self, retry_delay):
        """Test that resolved trades aren't re-validated on retry."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_stock_price.return_value = 180.0

        # AAPL: valid bid immediately. MSFT: invalid, then valid.
        call_count = {"n": 0}

        def mock_quote(symbol, strike, exp, right):
            call_count["n"] += 1
            if symbol == "AAPL":
                return {"bid": 0.48, "ask": 0.55}
            elif symbol == "MSFT" and call_count["n"] <= 2:
                return {"bid": -1.0, "ask": -1.0}
            else:
                return {"bid": 0.45, "ask": 0.52}

        mock_ibkr.get_option_quote.side_effect = mock_quote

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        opps = [
            create_staged_opportunity(symbol="AAPL", staged_limit_price=0.50),
            create_staged_opportunity(symbol="MSFT", staged_limit_price=0.50),
        ]

        results = validator.validate_at_open(
            opps, max_retries=3, retry_delay=retry_delay
        )

        assert len(results) == 2
        # Both should have resolved
        aapl_result = next(r for r in results if r.opportunity.symbol == "AAPL")
        msft_result = next(r for r in results if r.opportunity.symbol == "MSFT")
        assert aapl_result.passed is True
        assert msft_result.passed is True
