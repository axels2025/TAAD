"""Unit tests for Barchart CSV parser."""

from datetime import date
from pathlib import Path

import pytest

from src.data.candidates import BarchartCandidate
from src.tools.barchart_csv_parser import (
    is_metadata_row,
    parse_barchart_csv,
    parse_expiration,
    parse_percentage,
)


class TestParsePercentage:
    """Test parse_percentage function."""

    def test_parse_positive_percentage(self):
        """Test parsing positive percentage string."""
        assert parse_percentage("44.81%") == pytest.approx(0.4481)

    def test_parse_negative_percentage(self):
        """Test parsing negative percentage string."""
        assert parse_percentage("-11.53%") == pytest.approx(-0.1153)

    def test_parse_small_percentage(self):
        """Test parsing small percentage string."""
        assert parse_percentage("1.0%") == pytest.approx(0.01)

    def test_parse_percentage_with_spaces(self):
        """Test parsing percentage with leading/trailing spaces."""
        assert parse_percentage("  12.5%  ") == pytest.approx(0.125)

    def test_parse_zero_percentage(self):
        """Test parsing zero percentage."""
        assert parse_percentage("0.0%") == pytest.approx(0.0)

    def test_parse_large_percentage(self):
        """Test parsing percentage over 100%."""
        assert parse_percentage("189.5%") == pytest.approx(1.895)

    def test_parse_percentage_invalid_format(self):
        """Test parsing invalid percentage format raises ValueError."""
        with pytest.raises(ValueError, match="Cannot parse percentage"):
            parse_percentage("invalid")

    def test_parse_percentage_missing_percent_sign(self):
        """Test parsing without percent sign raises ValueError."""
        with pytest.raises(ValueError, match="Cannot parse percentage"):
            parse_percentage("44.81")


class TestParseExpiration:
    """Test parse_expiration function."""

    def test_parse_valid_date(self):
        """Test parsing valid ISO date string."""
        result = parse_expiration("2026-02-27")
        assert result == date(2026, 2, 27)

    def test_parse_another_valid_date(self):
        """Test parsing another valid date."""
        result = parse_expiration("2026-01-30")
        assert result == date(2026, 1, 30)

    def test_parse_invalid_date_format(self):
        """Test parsing invalid date format raises ValueError."""
        with pytest.raises(ValueError, match="Cannot parse expiration date"):
            parse_expiration("01/30/2026")

    def test_parse_empty_date(self):
        """Test parsing empty date raises ValueError."""
        with pytest.raises(ValueError, match="Cannot parse expiration date"):
            parse_expiration("")


class TestIsMetadataRow:
    """Test is_metadata_row function."""

    def test_metadata_row_with_downloaded(self):
        """Test metadata row detection with 'Downloaded' keyword."""
        row = {"Symbol": "Downloaded from Barchart.com as of 01-28-2026 11:20pm CST"}
        assert is_metadata_row(row) is True

    def test_metadata_row_with_barchart(self):
        """Test metadata row detection with 'Barchart' keyword."""
        row = {"Symbol": "Barchart data export"}
        assert is_metadata_row(row) is True

    def test_valid_row_not_metadata(self):
        """Test valid data row is not detected as metadata."""
        row = {"Symbol": "AMZN", "Strike": "215.00"}
        assert is_metadata_row(row) is False

    def test_empty_row_not_metadata(self):
        """Test empty row is not detected as metadata."""
        row = {"Symbol": ""}
        assert is_metadata_row(row) is False


class TestParseBarchartCsv:
    """Test parse_barchart_csv function."""

    @pytest.fixture
    def sample_csv_path(self):
        """Get path to sample CSV fixture."""
        return Path("tests/fixtures/sample_barchart.csv")

    def test_parse_csv_returns_candidates(self, sample_csv_path):
        """Test parsing CSV returns list of BarchartCandidate objects."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)

        assert len(candidates) > 0
        assert all(isinstance(c, BarchartCandidate) for c in candidates)

    def test_parse_csv_candidate_count(self, sample_csv_path):
        """Test correct number of candidates parsed (excludes metadata row)."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)

        # Sample CSV has 10 data rows + 1 metadata row
        assert len(candidates) == 10

    def test_parse_csv_first_candidate_fields(self, sample_csv_path):
        """Test first candidate has correct field values."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)
        first = candidates[0]

        assert first.symbol == "AMZN"
        assert first.strike == 215.00
        assert first.expiration == date(2026, 2, 27)
        assert first.dte == 30
        assert first.option_type == "PUT"
        assert first.underlying_price == 243.01
        assert first.bid == 2.17
        assert first.moneyness_pct == pytest.approx(-0.1153)
        assert first.volume == 271
        assert first.open_interest == 1047
        assert first.iv_rank == pytest.approx(0.4481)
        assert first.premium_return_pct == pytest.approx(0.01)
        assert first.annualized_return_pct == pytest.approx(0.124)
        assert first.profit_probability == pytest.approx(0.8554)

    def test_delta_sign_convention(self, sample_csv_path):
        """Test all deltas are negative for puts."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)

        # All candidates should be puts with negative deltas
        assert all(c.option_type == "PUT" for c in candidates)
        assert all(c.delta < 0 for c in candidates)

    def test_skip_metadata_row(self, sample_csv_path):
        """Test metadata row is not included in candidates."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)

        # Should not include any candidate with "Downloaded" in symbol
        assert not any("Downloaded" in c.symbol for c in candidates)
        assert not any("Barchart" in c.symbol for c in candidates)

    def test_parse_csv_different_symbols(self, sample_csv_path):
        """Test CSV contains different symbols."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)

        symbols = {c.symbol for c in candidates}
        assert len(symbols) > 1  # Should have multiple symbols
        assert "AMZN" in symbols
        assert "APLD" in symbols
        assert "ASTS" in symbols
        assert "SLV" in symbols

    def test_parse_csv_different_dte_ranges(self, sample_csv_path):
        """Test CSV contains different DTE ranges."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)

        dte_values = {c.dte for c in candidates}
        assert 2 in dte_values  # Short term
        assert 9 in dte_values  # Medium term
        assert 30 in dte_values  # Longer term

    def test_parse_csv_preserves_raw_row(self, sample_csv_path):
        """Test raw CSV row is preserved in candidate."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)

        first = candidates[0]
        assert first.raw_row is not None
        assert isinstance(first.raw_row, dict)
        assert first.raw_row["Symbol"] == "AMZN"

    def test_parse_csv_source_field(self, sample_csv_path):
        """Test source field is set correctly."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)

        assert all(c.source == "barchart_csv" for c in candidates)

    def test_parse_csv_file_not_found(self):
        """Test parsing non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="CSV file not found"):
            parse_barchart_csv("nonexistent.csv")

    def test_parse_csv_to_dict_serialization(self, sample_csv_path):
        """Test candidate can be serialized to dict."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)
        first = candidates[0]

        data = first.to_dict()
        assert isinstance(data, dict)
        assert data["symbol"] == "AMZN"
        assert data["strike"] == 215.00
        assert data["expiration"] == "2026-02-27"

    def test_parse_csv_from_dict_deserialization(self, sample_csv_path):
        """Test candidate can be deserialized from dict."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)
        first = candidates[0]

        data = first.to_dict()
        restored = BarchartCandidate.from_dict(data)

        assert restored.symbol == first.symbol
        assert restored.strike == first.strike
        assert restored.expiration == first.expiration
        assert restored.delta == first.delta

    def test_parse_csv_percentage_fields_converted(self, sample_csv_path):
        """Test all percentage fields are converted to decimals."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)

        for candidate in candidates:
            # All percentage fields should be decimals (not strings with %)
            assert isinstance(candidate.moneyness_pct, float)
            assert isinstance(candidate.breakeven_pct, float)
            assert isinstance(candidate.iv_rank, float)
            assert isinstance(candidate.premium_return_pct, float)
            assert isinstance(candidate.annualized_return_pct, float)
            assert isinstance(candidate.profit_probability, float)

            # Check ranges are reasonable (not 100x too large)
            assert -1.0 <= candidate.moneyness_pct <= 1.0
            assert 0.0 <= candidate.iv_rank <= 1.0
            assert 0.0 <= candidate.profit_probability <= 1.0

    def test_parse_csv_price_tilde_column(self, sample_csv_path):
        """Test 'Price~' column with tilde is parsed correctly."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)

        # All candidates should have valid underlying prices
        assert all(c.underlying_price > 0 for c in candidates)

        # Verify specific known values
        amzn_candidates = [c for c in candidates if c.symbol == "AMZN"]
        assert all(c.underlying_price == 243.01 for c in amzn_candidates)

    def test_otm_filter_default_range(self, sample_csv_path):
        """Test OTM% filter with default 10-25% range excludes near-money candidates."""
        # SLV (5.5%, 3.7%) and INTC (7.7%) are below 10% OTM
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.10, otm_pct_max=0.25, check_earnings=False)

        symbols = {c.symbol for c in candidates}
        assert "AMZN" in symbols  # 11.53% OTM - passes
        assert "APLD" in symbols  # 10.49% OTM - passes
        assert "ASTS" in symbols  # 10.09% and 17.51% OTM - passes
        assert "SLV" not in symbols  # 5.5% and 3.7% OTM - filtered
        assert "INTC" not in symbols  # 7.7% OTM - filtered
        assert "CRWV" in symbols  # 13.96% OTM - passes
        assert len(candidates) == 7  # 10 total - 3 filtered

    def test_otm_filter_tight_range(self, sample_csv_path):
        """Test OTM% filter with tight range filters most candidates."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.12, otm_pct_max=0.15, check_earnings=False)

        # Only AMZN (11.53% rounds but is actually 11.53 so excluded) and CRWV (13.96%) pass
        # AMZN is 11.53% - below 12%, so excluded
        # CRWV is 13.96% - within 12-15%
        symbols = {c.symbol for c in candidates}
        assert "CRWV" in symbols

    def test_otm_filter_excludes_too_far_otm(self, sample_csv_path):
        """Test candidates too far OTM are excluded."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=0.10, check_earnings=False)

        # Only SLV (5.5%, 3.7%) and INTC (7.7%) pass
        symbols = {c.symbol for c in candidates}
        assert "SLV" in symbols
        assert "INTC" in symbols
        assert "AMZN" not in symbols  # 11.53% - too far OTM for this range

    def test_otm_filter_wide_range_passes_all(self, sample_csv_path):
        """Test wide OTM% range passes all candidates."""
        candidates = parse_barchart_csv(sample_csv_path, otm_pct_min=0.0, otm_pct_max=1.0, check_earnings=False)

        assert len(candidates) == 10  # All rows pass


class TestBarchartCandidateDataclass:
    """Test BarchartCandidate dataclass."""

    def test_candidate_creation(self):
        """Test creating BarchartCandidate with all required fields."""
        candidate = BarchartCandidate(
            symbol="TEST",
            expiration=date(2026, 3, 15),
            strike=100.0,
            option_type="PUT",
            underlying_price=110.0,
            bid=1.50,
            dte=45,
            moneyness_pct=-0.10,
            breakeven=98.50,
            breakeven_pct=-0.105,
            volume=500,
            open_interest=1000,
            iv_rank=0.50,
            delta=-0.15,
            premium_return_pct=0.015,
            annualized_return_pct=0.12,
            profit_probability=0.85,
        )

        assert candidate.symbol == "TEST"
        assert candidate.strike == 100.0
        assert candidate.source == "barchart_csv"  # Default value

    def test_candidate_with_raw_row(self):
        """Test candidate with raw_row preserved."""
        raw_data = {"Symbol": "TEST", "Strike": "100"}

        candidate = BarchartCandidate(
            symbol="TEST",
            expiration=date(2026, 3, 15),
            strike=100.0,
            option_type="PUT",
            underlying_price=110.0,
            bid=1.50,
            dte=45,
            moneyness_pct=-0.10,
            breakeven=98.50,
            breakeven_pct=-0.105,
            volume=500,
            open_interest=1000,
            iv_rank=0.50,
            delta=-0.15,
            premium_return_pct=0.015,
            annualized_return_pct=0.12,
            profit_probability=0.85,
            raw_row=raw_data,
        )

        assert candidate.raw_row == raw_data
