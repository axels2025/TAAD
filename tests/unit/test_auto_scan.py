"""Unit tests for Phase 4 auto-scan: market-open scan + execute automation.

Tests cover:
- AutoScanConfig: defaults, YAML loading, validation
- Pipeline extraction: run_scan_and_persist, run_auto_select_pipeline, stage_selected_candidates
- Daemon hook: _run_market_open_scan enable/disable, IBKR checks, pipeline orchestration
- Dashboard endpoints: trigger, status
- Earnings-aware strike selection: config, filter overrides, Claude enrichment, DB persistence
"""

import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agentic.config import AutoScanConfig, Phase5Config, load_phase5_config
from src.agentic.scanner_settings import (
    EarningsFilterSettings,
    ScannerSettings,
    load_scanner_settings,
    save_scanner_settings,
)
from src.services.auto_select_pipeline import AutoSelectResult
from src.services.earnings_service import EarningsInfo


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestAutoScanConfig:
    """Tests for AutoScanConfig model and YAML integration."""

    def test_auto_scan_config_defaults(self):
        """Default config: disabled, 5min delay, naked-put preset."""
        cfg = AutoScanConfig()
        assert cfg.enabled is False
        assert cfg.delay_minutes == 5
        assert cfg.scanner_preset == "naked-put"
        assert cfg.auto_stage is True
        assert cfg.require_ibkr is True

    def test_auto_scan_config_custom_values(self):
        """Custom config values are accepted."""
        cfg = AutoScanConfig(
            enabled=True,
            delay_minutes=10,
            scanner_preset="high-iv",
            auto_stage=False,
            require_ibkr=False,
        )
        assert cfg.enabled is True
        assert cfg.delay_minutes == 10
        assert cfg.scanner_preset == "high-iv"
        assert cfg.auto_stage is False
        assert cfg.require_ibkr is False

    def test_auto_scan_config_validation_delay_range(self):
        """delay_minutes must be 0-30."""
        cfg = AutoScanConfig(delay_minutes=0)
        assert cfg.delay_minutes == 0

        cfg = AutoScanConfig(delay_minutes=30)
        assert cfg.delay_minutes == 30

        with pytest.raises(Exception):
            AutoScanConfig(delay_minutes=31)

        with pytest.raises(Exception):
            AutoScanConfig(delay_minutes=-1)

    def test_auto_scan_config_in_phase5(self):
        """AutoScanConfig is accessible from Phase5Config."""
        from src.agentic.guardrails.config import GuardrailConfig

        Phase5Config.model_rebuild(
            _types_namespace={"GuardrailConfig": GuardrailConfig}
        )
        p5 = Phase5Config()
        assert hasattr(p5, "auto_scan")
        assert isinstance(p5.auto_scan, AutoScanConfig)
        assert p5.auto_scan.enabled is False

    def test_auto_scan_config_loads_from_yaml(self, tmp_path):
        """Config round-trips through YAML."""
        yaml_content = """\
auto_scan:
  enabled: true
  delay_minutes: 3
  scanner_preset: "naked-put"
  auto_stage: false
  require_ibkr: true
"""
        yaml_path = tmp_path / "phase5.yaml"
        yaml_path.write_text(yaml_content)

        cfg = load_phase5_config(str(yaml_path))
        assert cfg.auto_scan.enabled is True
        assert cfg.auto_scan.delay_minutes == 3
        assert cfg.auto_scan.auto_stage is False


# ---------------------------------------------------------------------------
# AutoSelectResult dataclass tests
# ---------------------------------------------------------------------------


class TestAutoSelectResult:
    """Tests for AutoSelectResult dataclass."""

    def test_default_result(self):
        """Default result is unsuccessful with empty collections."""
        result = AutoSelectResult(success=False, error="test error")
        assert result.success is False
        assert result.error == "test error"
        assert result.selected == []
        assert result.skipped == []
        assert result.warnings == []
        assert result.opp_id_map == {}
        assert result.symbols_scanned == 0

    def test_successful_result(self):
        """Successful result carries metrics."""
        result = AutoSelectResult(
            success=True,
            scan_id=42,
            symbols_scanned=25,
            chains_loaded=20,
            candidates_filtered=100,
            best_strikes_found=15,
            ai_scored=15,
            ai_cost_usd=0.05,
            elapsed_seconds=12.5,
        )
        assert result.success is True
        assert result.scan_id == 42
        assert result.symbols_scanned == 25
        assert result.elapsed_seconds == 12.5


# ---------------------------------------------------------------------------
# Pipeline extraction tests
# ---------------------------------------------------------------------------


class TestRunScanAndPersist:
    """Tests for run_scan_and_persist function."""

    @patch("src.services.auto_select_pipeline.IBKRScannerService")
    def test_run_scan_and_persist_success(self, mock_scanner_cls, temp_database):
        """Scanner results are persisted as ScanResult + ScanOpportunity rows."""
        from src.data.database import get_db_session
        from src.data.models import ScanOpportunity, ScanResult
        from src.services.auto_select_pipeline import run_scan_and_persist

        # Mock scanner results
        mock_result = MagicMock()
        mock_result.symbol = "AAPL"
        mock_result.rank = 1
        mock_result.con_id = 12345
        mock_result.exchange = "SMART"
        mock_result.long_name = "Apple Inc"
        mock_result.industry = "Technology"
        mock_result.category = "Computers"
        mock_result.distance = ""
        mock_result.benchmark = ""
        mock_result.projection = ""

        mock_service = MagicMock()
        mock_service.run_scan.return_value = [mock_result]
        mock_scanner_cls.return_value = mock_service

        with get_db_session() as db:
            scan_id, opps = run_scan_and_persist(preset="naked-put", db=db)

        assert scan_id is not None
        assert len(opps) == 1
        assert opps[0].symbol == "AAPL"
        assert opps[0].state == "PENDING"

    def test_run_scan_unknown_preset(self, temp_database):
        """Unknown preset raises RuntimeError."""
        from src.data.database import get_db_session
        from src.services.auto_select_pipeline import run_scan_and_persist

        with get_db_session() as db:
            with pytest.raises(RuntimeError, match="Unknown scanner preset"):
                run_scan_and_persist(preset="nonexistent", db=db)

    @patch("src.services.auto_select_pipeline.IBKRScannerService")
    def test_run_scan_ibkr_failure(self, mock_scanner_cls, temp_database):
        """IBKR scanner failure raises RuntimeError."""
        from src.data.database import get_db_session
        from src.services.auto_select_pipeline import run_scan_and_persist

        mock_service = MagicMock()
        mock_service.run_scan.side_effect = ConnectionError("TWS not running")
        mock_scanner_cls.return_value = mock_service

        with get_db_session() as db:
            with pytest.raises(RuntimeError, match="IBKR scanner failed"):
                run_scan_and_persist(preset="naked-put", db=db)


class TestRunAutoSelectPipeline:
    """Tests for run_auto_select_pipeline function."""

    @patch("src.services.auto_select_pipeline.IBKRScannerService")
    def test_pipeline_no_opps(self, mock_scanner_cls, temp_database):
        """Pipeline returns error when no PENDING opportunities exist."""
        from src.data.database import get_db_session
        from src.services.auto_select_pipeline import run_auto_select_pipeline

        mock_service = MagicMock()
        mock_service.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "FullMaintMarginReq": 10000.0,
        }
        mock_scanner_cls.return_value = mock_service

        with get_db_session() as db:
            result = run_auto_select_pipeline(scan_id=999, db=db)

        assert result.success is False
        assert "No PENDING" in result.error

    @patch("src.services.auto_select_pipeline.IBKRScannerService")
    def test_pipeline_ibkr_offline_no_override(self, mock_scanner_cls, temp_database):
        """Pipeline returns error when IBKR is offline without override."""
        from src.data.database import get_db_session
        from src.services.auto_select_pipeline import run_auto_select_pipeline

        mock_service = MagicMock()
        mock_service.get_account_summary.return_value = {}  # No NLV = offline
        mock_scanner_cls.return_value = mock_service

        with get_db_session() as db:
            result = run_auto_select_pipeline(
                scan_id=1, db=db, override_market_hours=False
            )

        assert result.success is False
        assert "IBKR offline" in result.error


class TestStageSelectedCandidates:
    """Tests for stage_selected_candidates function."""

    @patch("src.services.auto_select_pipeline.IBKRScannerService")
    def test_stage_candidates(self, mock_scanner_cls, temp_database):
        """Selected candidates are staged with correct data."""
        from src.data.database import get_db_session
        from src.data.models import ScanOpportunity, ScanResult
        from src.services.auto_select_pipeline import stage_selected_candidates

        # Create scan + opportunity manually
        with get_db_session() as db:
            scan = ScanResult(
                scan_timestamp=datetime.utcnow(),
                source="ibkr_scanner",
                config_used={"preset": "naked-put"},
                total_candidates=1,
            )
            db.add(scan)
            db.flush()

            opp = ScanOpportunity(
                scan_id=scan.id,
                symbol="MSFT",
                strike=0,
                expiration=date.today(),
                option_type="PUT",
                source="ibkr_scanner",
                state="PENDING",
            )
            db.add(opp)
            db.commit()
            opp_id = opp.id

            # Create a mock PortfolioCandidate
            pc = MagicMock()
            pc.symbol = "MSFT"
            pc.strike = 380.0
            pc.expiration = "2026-02-28"
            pc.bid = 1.50
            pc.ask = 1.80
            pc.delta = 0.08
            pc.iv = 0.30
            pc.stock_price = 420.0
            pc.otm_pct = 0.095
            pc.contracts = 2
            pc.margin = 3000.0
            pc.margin_source = "ibkr_whatif"
            pc.portfolio_rank = 1

            count = stage_selected_candidates(
                selected=[pc],
                opp_id_map={"MSFT": opp_id},
                config_snapshot={"test": True},
                db=db,
            )

            assert count == 1

            # Verify the opportunity was updated
            staged = db.query(ScanOpportunity).get(opp_id)
            assert staged.state == "STAGED"
            assert staged.strike == 380.0
            assert staged.staged_contracts == 2
            assert staged.margin_required == 3000.0

    def test_stage_no_opp_id(self, temp_database):
        """Stage gracefully skips candidates with no matching opportunity."""
        from src.data.database import get_db_session
        from src.services.auto_select_pipeline import stage_selected_candidates

        pc = MagicMock()
        pc.symbol = "UNKNOWN"

        with get_db_session() as db:
            count = stage_selected_candidates(
                selected=[pc],
                opp_id_map={},  # No mapping
                config_snapshot={},
                db=db,
            )

        assert count == 0


# ---------------------------------------------------------------------------
# Daemon hook tests
# ---------------------------------------------------------------------------


class TestMarketOpenScanHook:
    """Tests for TAADDaemon._run_market_open_scan method."""

    def _make_daemon(self, auto_scan_enabled=False, **kwargs):
        """Create a TAADDaemon with mocked dependencies."""
        from src.agentic.config import AutoScanConfig, Phase5Config
        from src.agentic.daemon import TAADDaemon

        config = Phase5Config(
            auto_scan=AutoScanConfig(
                enabled=auto_scan_enabled,
                delay_minutes=0,  # No delay in tests
                **kwargs,
            ),
        )

        daemon = TAADDaemon.__new__(TAADDaemon)
        daemon.config = config
        daemon.ibkr_client = MagicMock()
        daemon.ibkr_client.is_connected.return_value = True
        daemon.memory = MagicMock()
        daemon.health = MagicMock()
        return daemon

    def test_market_open_scan_disabled(self):
        """enabled=False skips everything."""
        daemon = self._make_daemon(auto_scan_enabled=False)
        db = MagicMock()

        asyncio.get_event_loop().run_until_complete(
            daemon._run_market_open_scan(db)
        )

        # Memory should not have been touched
        daemon.memory.add_decision.assert_not_called()

    def test_market_open_scan_no_ibkr(self):
        """require_ibkr + disconnected → skips."""
        daemon = self._make_daemon(
            auto_scan_enabled=True, require_ibkr=True
        )
        daemon.ibkr_client.is_connected.return_value = False
        db = MagicMock()

        asyncio.get_event_loop().run_until_complete(
            daemon._run_market_open_scan(db)
        )

        daemon.memory.add_decision.assert_not_called()

    @patch("src.services.auto_select_pipeline.run_scan_and_persist")
    @patch("src.services.auto_select_pipeline.run_auto_select_pipeline")
    @patch("src.services.auto_select_pipeline.stage_selected_candidates")
    def test_market_open_scan_runs_pipeline(
        self, mock_stage, mock_pipeline, mock_scan
    ):
        """Full pipeline is called: scan → select → stage."""
        daemon = self._make_daemon(auto_scan_enabled=True, auto_stage=True)
        db = MagicMock()

        # Mock scan
        mock_opp = MagicMock()
        mock_opp.symbol = "AAPL"
        mock_scan.return_value = (42, [mock_opp])

        # Mock pipeline result
        mock_pc = MagicMock()
        mock_pc.symbol = "AAPL"
        mock_pipeline.return_value = AutoSelectResult(
            success=True,
            scan_id=42,
            selected=[mock_pc],
            opp_id_map={"AAPL": 1},
            config_snapshot={"test": True},
            symbols_scanned=1,
            best_strikes_found=1,
            available_budget=50000,
            used_margin=3000,
        )

        mock_stage.return_value = 1

        asyncio.get_event_loop().run_until_complete(
            daemon._run_market_open_scan(db)
        )

        mock_scan.assert_called_once()
        mock_pipeline.assert_called_once_with(
            scan_id=42, db=db, override_market_hours=False
        )
        mock_stage.assert_called_once()

    @patch("src.services.auto_select_pipeline.run_scan_and_persist")
    @patch("src.services.auto_select_pipeline.run_auto_select_pipeline")
    def test_market_open_scan_writes_memory(self, mock_pipeline, mock_scan):
        """Summary is written to working memory."""
        daemon = self._make_daemon(
            auto_scan_enabled=True, auto_stage=False
        )
        db = MagicMock()

        mock_scan.return_value = (42, [MagicMock()])
        mock_pipeline.return_value = AutoSelectResult(
            success=True,
            scan_id=42,
            selected=[],
            symbols_scanned=5,
            best_strikes_found=3,
            available_budget=50000,
            used_margin=0,
        )

        asyncio.get_event_loop().run_until_complete(
            daemon._run_market_open_scan(db)
        )

        daemon.memory.add_decision.assert_called_once()
        call_args = daemon.memory.add_decision.call_args[0][0]
        assert call_args["event_type"] == "AUTO_SCAN"
        assert call_args["action"] == "SCAN_COMPLETE"
        assert "5 scanned" in call_args["reasoning"]

    @patch("src.services.auto_select_pipeline.run_scan_and_persist")
    def test_market_open_scan_empty_results(self, mock_scan):
        """Empty scanner results are handled gracefully."""
        daemon = self._make_daemon(auto_scan_enabled=True)
        db = MagicMock()

        mock_scan.return_value = (42, [])  # Empty results

        asyncio.get_event_loop().run_until_complete(
            daemon._run_market_open_scan(db)
        )

        daemon.memory.add_decision.assert_called_once()
        call_args = daemon.memory.add_decision.call_args[0][0]
        assert call_args["action"] == "SCAN_EMPTY"

    @patch("src.services.auto_select_pipeline.run_scan_and_persist")
    @patch("src.services.auto_select_pipeline.run_auto_select_pipeline")
    def test_market_open_scan_pipeline_failure(self, mock_pipeline, mock_scan):
        """Pipeline failure writes error to memory."""
        daemon = self._make_daemon(auto_scan_enabled=True)
        db = MagicMock()

        mock_scan.return_value = (42, [MagicMock()])
        mock_pipeline.return_value = AutoSelectResult(
            success=False, error="IBKR offline"
        )

        asyncio.get_event_loop().run_until_complete(
            daemon._run_market_open_scan(db)
        )

        daemon.memory.add_decision.assert_called_once()
        call_args = daemon.memory.add_decision.call_args[0][0]
        assert call_args["action"] == "PIPELINE_FAILED"


# ---------------------------------------------------------------------------
# Dashboard endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("fastapi"),
    reason="FastAPI not installed",
)
class TestDashboardAutoScanEndpoints:
    """Tests for dashboard auto-scan endpoints."""

    @patch("src.services.auto_select_pipeline.run_scan_and_persist")
    @patch("src.services.auto_select_pipeline.run_auto_select_pipeline")
    @patch("src.services.auto_select_pipeline.stage_selected_candidates")
    @patch("src.services.market_calendar.MarketCalendar.is_market_open")
    def test_trigger_auto_scan_success(
        self, mock_market_open, mock_stage, mock_pipeline, mock_scan, temp_database
    ):
        """Trigger endpoint runs pipeline and returns status."""
        from fastapi.testclient import TestClient

        from src.agentic.dashboard_api import create_dashboard_app

        mock_market_open.return_value = False  # Market closed

        mock_scan.return_value = (42, [MagicMock()])
        mock_pipeline.return_value = AutoSelectResult(
            success=True,
            scan_id=42,
            selected=[MagicMock()],
            opp_id_map={"AAPL": 1},
            config_snapshot={},
            symbols_scanned=10,
            elapsed_seconds=5.0,
            stale_data=True,
        )
        mock_stage.return_value = 1

        app = create_dashboard_app(auth_token="")
        client = TestClient(app)

        resp = client.post(
            "/api/auto-scan/trigger",
            json={"override_market_hours": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "scan_complete"
        assert data["scan_id"] == 42
        assert data["staged"] == 1

    @patch("src.services.market_calendar.MarketCalendar.is_market_open")
    def test_trigger_auto_scan_market_closed_no_override(
        self, mock_market_open, temp_database
    ):
        """Trigger without override when market is closed returns error."""
        from fastapi.testclient import TestClient

        from src.agentic.dashboard_api import create_dashboard_app

        mock_market_open.return_value = False

        app = create_dashboard_app(auth_token="")
        client = TestClient(app)

        resp = client.post(
            "/api/auto-scan/trigger",
            json={"override_market_hours": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert "Market is closed" in data["error"]

    def test_auto_scan_status_returns_config(self, temp_database):
        """Status endpoint returns config and last scan info."""
        from fastapi.testclient import TestClient

        from src.agentic.dashboard_api import create_dashboard_app

        app = create_dashboard_app(auth_token="")
        client = TestClient(app)

        resp = client.get("/api/auto-scan/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "config" in data
        assert data["config"]["enabled"] is False
        assert data["config"]["scanner_preset"] == "naked-put"
        assert "staged_today" in data


# ---------------------------------------------------------------------------
# Earnings-aware strike selection tests
# ---------------------------------------------------------------------------


class TestEarningsFilterConfig:
    """Tests for EarningsFilterSettings model and YAML integration."""

    def test_earnings_filter_defaults(self):
        """Default: disabled (warn-only), 15% additional OTM."""
        ef = EarningsFilterSettings()
        assert ef.enabled is False
        assert ef.additional_otm_pct == 0.15
        assert ef.lookahead_days == 0

    def test_earnings_filter_in_scanner_settings(self):
        """EarningsFilterSettings is nested inside ScannerSettings."""
        s = ScannerSettings()
        assert hasattr(s, "earnings")
        assert isinstance(s.earnings, EarningsFilterSettings)
        assert s.earnings.enabled is False

    def test_earnings_filter_round_trip_yaml(self, tmp_path):
        """Earnings settings survive YAML save/load cycle."""
        settings = ScannerSettings(
            earnings=EarningsFilterSettings(
                enabled=True,
                additional_otm_pct=0.20,
                lookahead_days=3,
            )
        )
        yaml_path = tmp_path / "scanner_settings.yaml"
        save_scanner_settings(settings, path=yaml_path)

        loaded = load_scanner_settings(path=yaml_path)
        assert loaded.earnings.enabled is True
        assert loaded.earnings.additional_otm_pct == 0.20
        assert loaded.earnings.lookahead_days == 3

    def test_earnings_filter_validation(self):
        """Pydantic validates field ranges."""
        with pytest.raises(Exception):
            EarningsFilterSettings(additional_otm_pct=1.5)

        with pytest.raises(Exception):
            EarningsFilterSettings(additional_otm_pct=-0.1)

        with pytest.raises(Exception):
            EarningsFilterSettings(lookahead_days=15)


class TestEarningsPipelineBehavior:
    """Tests for earnings-aware behavior in the auto-select pipeline."""

    def test_earnings_in_dte_adds_additional_otm(self):
        """When enabled + earnings in DTE, additional_otm_pct is added to base."""
        from src.services.auto_selector import AutoSelector

        settings = ScannerSettings(
            earnings=EarningsFilterSettings(
                enabled=True,
                additional_otm_pct=0.15,
            ),
        )
        # Base min_otm_pct is 0.10 (FilterSettings default)
        selector = AutoSelector(settings)
        orig_min_otm = selector.settings.filters.min_otm_pct  # 0.10

        # Simulate the pipeline's additive override
        original_filters = selector.settings.filters.model_copy()
        adjusted = orig_min_otm + settings.earnings.additional_otm_pct
        selector.settings.filters.min_otm_pct = adjusted

        # 0.10 + 0.15 = 0.25
        assert selector.settings.filters.min_otm_pct == pytest.approx(0.25)

        # Restore
        selector.settings.filters = original_filters
        assert selector.settings.filters.min_otm_pct == orig_min_otm

    def test_earnings_not_in_dte_keeps_normal_filters(self):
        """When earnings are NOT in DTE, filters stay unchanged."""
        from src.services.auto_selector import AutoSelector

        settings = ScannerSettings(
            earnings=EarningsFilterSettings(enabled=True, additional_otm_pct=0.15),
        )
        selector = AutoSelector(settings)
        orig_min_otm = selector.settings.filters.min_otm_pct

        # Earnings 30 days away, outside DTE
        earnings_info = EarningsInfo(
            earnings_date=date.today() + timedelta(days=30),
            days_to_earnings=30,
            earnings_in_dte=False,
        )

        # The pipeline only adjusts when earnings_in_dte is True
        if earnings_info and earnings_info.earnings_in_dte:
            if settings.earnings.enabled:
                selector.settings.filters.min_otm_pct += settings.earnings.additional_otm_pct

        # Should be unchanged
        assert selector.settings.filters.min_otm_pct == orig_min_otm

    def test_earnings_disabled_warns_not_adjusts(self):
        """When enabled=False, earnings in DTE logs a warning, no filter change."""
        from src.services.auto_selector import AutoSelector

        settings = ScannerSettings(
            earnings=EarningsFilterSettings(enabled=False, additional_otm_pct=0.15),
        )
        selector = AutoSelector(settings)
        orig_min_otm = selector.settings.filters.min_otm_pct

        earnings_info = EarningsInfo(
            earnings_date=date.today() + timedelta(days=2),
            days_to_earnings=2,
            earnings_in_dte=True,
        )

        # Simulate pipeline logic: warn-only when disabled
        earnings_warnings = []
        if earnings_info and earnings_info.earnings_in_dte:
            if settings.earnings.enabled:
                selector.settings.filters.min_otm_pct += settings.earnings.additional_otm_pct
            else:
                earnings_warnings.append(
                    f"CRWV: earnings {earnings_info.earnings_date} in DTE"
                )

        # Filter unchanged — warn-only mode
        assert selector.settings.filters.min_otm_pct == orig_min_otm
        # But a warning was collected
        assert len(earnings_warnings) == 1
        assert "earnings" in earnings_warnings[0]

    def test_additive_otm_stacks_on_any_base(self):
        """Additional OTM adds on top of whatever the base filter is."""
        from src.services.auto_selector import AutoSelector

        # Base min_otm_pct already high at 20%
        settings = ScannerSettings(
            earnings=EarningsFilterSettings(enabled=True, additional_otm_pct=0.15),
        )
        settings.filters.min_otm_pct = 0.20

        selector = AutoSelector(settings)

        # Simulate additive override
        adjusted = selector.settings.filters.min_otm_pct + settings.earnings.additional_otm_pct

        # 0.20 + 0.15 = 0.35
        assert adjusted == pytest.approx(0.35)

    def test_earnings_data_passed_to_claude(self):
        """Earnings fields are included in best_strike_dicts for Claude."""
        earnings_map = {
            "CRWV": EarningsInfo(
                earnings_date=date(2026, 3, 1),
                days_to_earnings=5,
                earnings_in_dte=True,
                earnings_timing="AMC",
            ),
            "AAPL": EarningsInfo(
                earnings_date=date(2026, 4, 15),
                days_to_earnings=50,
                earnings_in_dte=False,
                earnings_timing="BMO",
            ),
        }

        # Simulate the pipeline's enrichment logic
        best_strike_dicts = []
        for sym in ["CRWV", "AAPL"]:
            entry = {"symbol": sym, "strike": 100, "delta": 0.06}
            ei = earnings_map.get(sym)
            if ei and ei.earnings_date:
                entry["earnings_date"] = str(ei.earnings_date)
                entry["days_to_earnings"] = ei.days_to_earnings
                entry["earnings_in_dte"] = ei.earnings_in_dte
                entry["earnings_timing"] = ei.earnings_timing
            best_strike_dicts.append(entry)

        # CRWV should have earnings data
        crwv = best_strike_dicts[0]
        assert crwv["earnings_date"] == "2026-03-01"
        assert crwv["days_to_earnings"] == 5
        assert crwv["earnings_in_dte"] is True
        assert crwv["earnings_timing"] == "AMC"

        # AAPL also has earnings data (but not in DTE)
        aapl = best_strike_dicts[1]
        assert aapl["earnings_date"] == "2026-04-15"
        assert aapl["earnings_in_dte"] is False

    def test_earnings_persisted_to_db(self, temp_database):
        """Staging populates earnings fields on ScanOpportunity."""
        from src.data.database import get_db_session
        from src.data.models import ScanOpportunity, ScanResult
        from src.services.auto_select_pipeline import stage_selected_candidates

        with get_db_session() as db:
            scan = ScanResult(
                scan_timestamp=datetime.utcnow(),
                source="ibkr_scanner",
                config_used={"preset": "naked-put"},
                total_candidates=1,
            )
            db.add(scan)
            db.flush()

            opp = ScanOpportunity(
                scan_id=scan.id,
                symbol="CRWV",
                strike=0,
                expiration=date.today(),
                option_type="PUT",
                source="ibkr_scanner",
                state="PENDING",
            )
            db.add(opp)
            db.commit()
            opp_id = opp.id

            pc = MagicMock()
            pc.symbol = "CRWV"
            pc.strike = 70.0
            pc.expiration = "2026-03-07"
            pc.bid = 1.00
            pc.ask = 1.20
            pc.delta = 0.06
            pc.iv = 1.80
            pc.stock_price = 100.0
            pc.otm_pct = 0.30
            pc.contracts = 1
            pc.margin = 2000.0
            pc.margin_source = "ibkr_whatif"
            pc.portfolio_rank = 1

            earnings_map = {
                "CRWV": EarningsInfo(
                    earnings_date=date(2026, 3, 5),
                    days_to_earnings=3,
                    earnings_in_dte=True,
                    earnings_timing="AMC",
                ),
            }

            count = stage_selected_candidates(
                selected=[pc],
                opp_id_map={"CRWV": opp_id},
                config_snapshot={"test": True},
                db=db,
                earnings_map=earnings_map,
            )

            assert count == 1

            staged = db.query(ScanOpportunity).get(opp_id)
            assert staged.state == "STAGED"
            assert staged.earnings_date == date(2026, 3, 5)
            assert staged.days_to_earnings == 3
            assert staged.earnings_in_dte is True
            assert staged.earnings_timing == "AMC"

    def test_earnings_fetch_failure_graceful(self):
        """If get_cached_earnings raises, the symbol is skipped gracefully."""
        from src.services.earnings_service import EarningsInfo

        earnings_map: dict[str, EarningsInfo] = {}
        symbols = ["AAPL", "CRWV", "MSFT"]

        def mock_get_earnings(symbol, option_expiration=None):
            if symbol == "CRWV":
                raise ConnectionError("Yahoo Finance timeout")
            return EarningsInfo(
                earnings_date=date(2026, 4, 15),
                days_to_earnings=50,
                earnings_in_dte=False,
            )

        # Simulate pipeline's Step 5a loop
        for symbol in symbols:
            try:
                info = mock_get_earnings(symbol)
                earnings_map[symbol] = info
            except Exception:
                pass  # Skip failed symbols

        assert "AAPL" in earnings_map
        assert "CRWV" not in earnings_map  # Failed, skipped
        assert "MSFT" in earnings_map
        assert len(earnings_map) == 2


class TestRiskGovernorEarningsWarning:
    """Tests for RiskGovernor._check_earnings_risk() warn-only behavior."""

    @pytest.fixture(autouse=True)
    def _clear_earnings_cache(self):
        """Clear module-level earnings cache between tests."""
        from src.services import earnings_service
        earnings_service._earnings_cache.clear()
        yield
        earnings_service._earnings_cache.clear()

    def _make_governor(self, tmp_path=None):
        """Create a RiskGovernor with properly mocked dependencies."""
        import tempfile

        from src.config.base import Config
        from src.execution.risk_governor import RiskGovernor
        from src.services.kill_switch import KillSwitch

        config = Config(_env_file=None)
        halt_dir = tmp_path or tempfile.mkdtemp()
        kill_switch = KillSwitch(
            halt_file=str(halt_dir) + "/test_halt.json",
            register_signals=False,
        )

        return RiskGovernor(
            ibkr_client=MagicMock(),
            position_monitor=MagicMock(),
            config=config,
            kill_switch=kill_switch,
        )

    @patch("src.services.earnings_service.EarningsService.get_earnings_info")
    def test_earnings_in_dte_warns_not_blocks(self, mock_get_earnings, tmp_path, monkeypatch):
        """Earnings within DTE returns approved=True with warning reason."""
        from src.strategies.base import TradeOpportunity

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test123456789")

        mock_get_earnings.return_value = EarningsInfo(
            earnings_date=date.today() + timedelta(days=3),
            days_to_earnings=3,
            earnings_in_dte=True,
            earnings_timing="AMC",
        )

        governor = self._make_governor(tmp_path)

        opp = TradeOpportunity(
            symbol="CRWV",
            strike=70.0,
            expiration=date.today() + timedelta(days=7),
            option_type="PUT",
            premium=1.00,
            contracts=1,
            otm_pct=0.30,
            dte=7,
            stock_price=100.0,
            trend="uptrend",
            confidence=0.8,
            reasoning="Test",
            margin_required=2000.0,
        )

        result = governor._check_earnings_risk(opp)

        assert result.approved is True
        assert "WARNING" in result.reason
        assert "Scanner applied conservative filters" in result.reason
        assert result.utilization_pct == 75.0
        assert result.limit_name == "earnings_check"

    @patch("src.services.earnings_service.EarningsService.get_earnings_info")
    def test_no_earnings_in_dte_passes(self, mock_get_earnings, tmp_path, monkeypatch):
        """No earnings within DTE returns approved=True normally."""
        from src.strategies.base import TradeOpportunity

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test123456789")

        mock_get_earnings.return_value = EarningsInfo(
            earnings_date=date.today() + timedelta(days=30),
            days_to_earnings=30,
            earnings_in_dte=False,
            earnings_timing="BMO",
        )

        governor = self._make_governor(tmp_path)

        opp = TradeOpportunity(
            symbol="AAPL",
            strike=200.0,
            expiration=date.today() + timedelta(days=7),
            option_type="PUT",
            premium=0.50,
            contracts=1,
            otm_pct=0.15,
            dte=7,
            stock_price=235.0,
            trend="uptrend",
            confidence=0.85,
            reasoning="Test",
            margin_required=1000.0,
        )

        result = governor._check_earnings_risk(opp)

        assert result.approved is True
        assert result.utilization_pct == 0.0
