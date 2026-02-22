"""FastAPI endpoints and HTML page for the IBKR Option Scanner.

Provides endpoints to run IBKR market scans, get AI recommendations
on scanned symbols, and stage selected candidates for the enrichment
pipeline. The embedded HTML page matches the dark terminal UI of the
main TAAD dashboard.
"""

import json
import time
from datetime import date, datetime
from typing import Optional

from loguru import logger

try:
    from fastapi import APIRouter, Depends, HTTPException
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from src.data.database import get_db_session
from src.data.models import ClaudeApiCost, ScanOpportunity, ScanResult
from src.services.ibkr_scanner import (
    SCAN_CODES,
    SCANNER_PRESETS,
    IBKRScannerService,
    ScannerConfig,
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    preset: Optional[str] = None
    scan_code: str = "HIGH_OPT_IMP_VOLAT"
    instrument: str = "STK"
    location: str = "STK.US.MAJOR"
    min_price: float = 20.0
    max_price: float = 200.0
    num_rows: int = 50
    market_cap_above: float = 0
    market_cap_below: float = 0
    avg_volume_above: int = 0
    avg_opt_volume_above: int = 0
    stock_type: str = ""


class RecommendRequest(BaseModel):
    scan_id: int
    best_strike_data: list[dict] | None = None


class SelectBestRequest(BaseModel):
    chains: dict[str, dict]  # symbol -> chain_data from /chain endpoint
    opportunity_ids: dict[str, int]  # symbol -> opportunity_id


class ChainRequest(BaseModel):
    symbol: str
    max_dte: int = 7


class AutoSelectRequest(BaseModel):
    scan_id: int
    override_market_hours: bool = False


class StageContractSelection(BaseModel):
    opportunity_id: int
    symbol: str
    strike: float
    expiration: str  # "2026-02-28"
    bid: float = 0.0
    ask: float = 0.0
    delta: float | None = None
    iv: float | None = None
    stock_price: float = 0.0
    otm_pct: float = 0.0
    contracts: int = 1
    # Phase 3: portfolio-level fields
    margin_actual: float | None = None  # IBKR whatif margin (overrides Reg-T)
    margin_source: str | None = None  # "ibkr_whatif" or "estimated"
    composite_score: float | None = None
    portfolio_rank: int | None = None
    config_snapshot: dict | None = None  # For enrichment_snapshot


class StageRequest(BaseModel):
    # Legacy: stage by IDs only (kept for backwards compat)
    opportunity_ids: list[int] = []
    # New: stage with full contract details
    selections: list[StageContractSelection] = []


class QuantityRequest(BaseModel):
    symbol: str
    strike: float
    stock_price: float = 0.0


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def _build_symbol_data(opps: list) -> list[dict]:
    """Build symbol data list from ScanOpportunity objects.

    Extracts symbol info and scanner metadata (rank, exchange, industry, etc.)
    from each opportunity's entry_notes JSON.

    Args:
        opps: List of ScanOpportunity objects.

    Returns:
        List of dicts with symbol data for Claude prompt.
    """
    symbol_data = []
    for opp in opps:
        notes = {}
        if opp.entry_notes:
            try:
                notes = json.loads(opp.entry_notes) if isinstance(opp.entry_notes, str) else opp.entry_notes
            except (json.JSONDecodeError, TypeError):
                pass

        symbol_data.append({
            "symbol": opp.symbol,
            "rank": notes.get("rank", "?"),
            "exchange": notes.get("exchange", ""),
            "industry": notes.get("industry", ""),
            "category": notes.get("category", ""),
            "distance": notes.get("distance", ""),
            "benchmark": notes.get("benchmark", ""),
            "projection": notes.get("projection", ""),
        })
    return symbol_data


def _get_ai_recommendations(
    symbol_data: list[dict],
    best_strike_data: list[dict] | None,
    scan_type: str,
    db_session,
) -> dict:
    """Call Claude for AI recommendations on scanned symbols.

    Constructs the prompt (with or without enriched strike data), calls
    Claude, tracks cost, and parses the JSON response.

    Args:
        symbol_data: List of dicts with symbol/rank/exchange/industry.
        best_strike_data: Optional list of best-strike dicts per symbol
                          (from select-best endpoint).
        scan_type: Scanner scan code for context (e.g. "HIGH_OPT_IMP_VOLAT").
        db_session: SQLAlchemy session for cost tracking.

    Returns:
        Dict with keys: recommendations (list), cost_usd (float),
        input_tokens, output_tokens. On error: {"error": "message"}.
    """
    from src.agents.base_agent import BaseAgent
    from src.agentic.reasoning_engine import CostTracker

    cost_tracker = CostTracker(db_session, daily_cap_usd=10.0)

    agent = BaseAgent(model="claude-sonnet-4-5-20250929", timeout=120.0)

    has_strike_data = best_strike_data and len(best_strike_data) > 0

    system_prompt = (
        "You are an expert options analyst specializing in naked put selling. "
        "You will receive a list of stock symbols from an IBKR market scanner"
    )

    if has_strike_data:
        system_prompt += (
            ", along with quantitative data for each symbol's best strike "
            "(delta, OTM%, IV, margin, premium/margin ratio, annualized return, "
            "open interest, volume, bid/ask spread). "
            "Use this data to make informed assessments. "
            "Pay special attention to:\n"
            "- Premium/margin efficiency (annualized return > 20% is good)\n"
            "- Liquidity quality (OI > 500, tight spreads)\n"
            "- Risk/reward tradeoff (delta vs premium collected)\n"
            "- Sector diversification across the portfolio\n"
            "- Whether the IV justifies the risk\n\n"
        )
    else:
        system_prompt += ". "

    system_prompt += (
        "Rate each symbol from 1-10 for naked put suitability based on:\n"
        "- Implied volatility (higher = more premium, but higher risk)\n"
        "- Stock trend and stability\n"
        "- Liquidity (exchange, market cap implied by category)\n"
        "- Industry/sector diversification value\n"
        "- Earnings risk (if identifiable from the symbol)\n"
        "- General risk factors\n\n"
        "Respond with ONLY a JSON array (no markdown, no explanation outside the JSON). "
        "Each element must have exactly these fields:\n"
        '  {"symbol": "AAPL", "score": 8, "recommendation": "strong_buy", '
        '"reasoning": "High liquidity, stable trend, rich premiums from elevated IV", '
        '"risk_flags": ["earnings_soon"]}\n\n'
        "recommendation must be one of: strong_buy, buy, neutral, avoid\n"
        "risk_flags is an array of 0+ strings from: "
        "earnings_soon, high_volatility, low_liquidity, downtrend, "
        "small_cap, sector_concentration, binary_event, overvalued"
    )

    # Build user message — enriched version includes per-symbol quantitative data
    if has_strike_data:
        strike_map = {
            d["symbol"]: d for d in best_strike_data
            if isinstance(d, dict)
        }
        enriched_symbols = []
        for sd in symbol_data:
            sym = sd["symbol"]
            entry = dict(sd)
            if sym in strike_map:
                bs = strike_map[sym]
                entry.update({
                    "stock_price": bs.get("stock_price"),
                    "best_strike": bs.get("strike"),
                    "delta": bs.get("delta"),
                    "otm_pct": round(bs.get("otm_pct", 0) * 100, 1),
                    "iv": round(bs.get("iv", 0) * 100, 1) if bs.get("iv") else None,
                    "dte": bs.get("dte"),
                    "premium_bid": bs.get("bid"),
                    "bid_ask_spread_pct": (
                        round((bs["ask"] - bs["bid"]) / ((bs["bid"] + bs["ask"]) / 2) * 100, 1)
                        if bs.get("bid") and bs.get("ask") and bs["bid"] > 0
                        else None
                    ),
                    "open_interest": bs.get("open_interest"),
                    "volume": bs.get("volume"),
                    "margin_per_contract": bs.get("margin"),
                    "margin_source": bs.get("margin_source"),
                    "premium_margin_ratio": round(bs.get("premium_margin_ratio", 0) * 100, 2),
                    "annualized_return_pct": bs.get("annualized_return_pct"),
                    "composite_score": bs.get("composite_score"),
                    "sector": bs.get("sector"),
                })
            enriched_symbols.append(entry)

        symbols_text = json.dumps(enriched_symbols, indent=2)
        user_message = (
            f"Analyze these {len(enriched_symbols)} symbols from an IBKR scanner "
            f"(scan type: {scan_type}) for naked put suitability. "
            f"Each symbol includes its best strike with real IBKR data "
            f"(margins, Greeks, liquidity):\n\n{symbols_text}"
        )
    else:
        symbols_text = json.dumps(symbol_data, indent=2)
        user_message = (
            f"Analyze these {len(symbol_data)} symbols from an IBKR scanner "
            f"(scan type: {scan_type}) "
            f"for naked put suitability:\n\n{symbols_text}"
        )

    try:
        response = agent.send_message(
            system_prompt=system_prompt,
            user_message=user_message,
            max_tokens=8192,
            temperature=0.3,
        )
    except Exception as e:
        logger.error(f"Claude recommendation error: {e}")
        return {"error": f"AI recommendation failed: {e}"}

    # Record cost
    cost = agent.estimate_cost(response["input_tokens"], response["output_tokens"])
    cost_tracker.record(
        model="claude-sonnet-4-5-20250929",
        purpose="scanner_recommendation",
        input_tokens=response["input_tokens"],
        output_tokens=response["output_tokens"],
        cost_usd=cost,
    )

    # Parse Claude's response
    content = response["content"].strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        content = content.strip()

    try:
        recommendations = json.loads(content)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse AI recommendations: {content[:500]}")
        return {"error": "AI returned invalid JSON. Try again."}

    return {
        "recommendations": recommendations,
        "cost_usd": cost,
        "input_tokens": response["input_tokens"],
        "output_tokens": response["output_tokens"],
    }


def create_scanner_router(verify_token) -> "APIRouter":
    """Create the scanner API router.

    Args:
        verify_token: Dependency callable for bearer token auth

    Returns:
        FastAPI APIRouter with scanner endpoints
    """
    router = APIRouter(prefix="/api/scanner", tags=["scanner"])

    # ------------------------------------------------------------------
    # GET /api/scanner/presets — list available presets + scan codes
    # ------------------------------------------------------------------
    @router.get("/presets")
    def get_presets(token: None = Depends(verify_token)):
        service = IBKRScannerService()
        return {
            "presets": service.get_available_presets(),
            "scan_codes": SCAN_CODES,
        }

    # ------------------------------------------------------------------
    # GET /api/scanner/settings — return current scanner settings
    # ------------------------------------------------------------------
    @router.get("/settings")
    def get_settings(token: None = Depends(verify_token)):
        from src.agentic.scanner_settings import load_scanner_settings

        settings = load_scanner_settings()
        return settings.model_dump()

    # ------------------------------------------------------------------
    # POST /api/scanner/settings — validate and save scanner settings
    # ------------------------------------------------------------------
    @router.post("/settings")
    def save_settings(payload: dict, token: None = Depends(verify_token)):
        from src.agentic.scanner_settings import (
            ScannerSettings,
            save_scanner_settings,
        )

        try:
            settings = ScannerSettings(**payload)
        except Exception as e:
            return {"error": str(e)}

        save_scanner_settings(settings)
        return {"ok": True, "settings": settings.model_dump()}

    # ------------------------------------------------------------------
    # GET /api/scanner/budget — margin budget info from IBKR + staged
    # ------------------------------------------------------------------
    @router.get("/budget")
    def get_budget(token: None = Depends(verify_token)):
        from src.agentic.scanner_settings import load_scanner_settings

        settings = load_scanner_settings()
        margin_budget_pct = settings.budget.margin_budget_pct

        # Try to get NLV and current margin from IBKR
        nlv = None
        current_margin = None
        ibkr_connected = False
        try:
            service = IBKRScannerService()
            summary = service.get_account_summary()
            nlv = summary.get("NetLiquidation")
            # FullMaintMarginReq = total maintenance margin across all positions
            current_margin = summary.get("FullMaintMarginReq")
            ibkr_connected = nlv is not None
        except Exception as e:
            logger.warning(f"Budget: IBKR offline — {e}")

        # Get staged margin from DB (not-yet-executed scanner trades)
        staged_margin = 0.0
        staged_count = 0
        try:
            with get_db_session() as db:
                staged_opps = (
                    db.query(ScanOpportunity)
                    .filter(ScanOpportunity.state == "STAGED")
                    .all()
                )
                staged_margin = sum(
                    opp.staged_margin for opp in staged_opps if opp.staged_margin
                )
                staged_count = len(staged_opps)
        except Exception as e:
            logger.warning(f"Budget: staged margin query failed — {e}")

        # ceiling = max margin we allow (NLV * pct)
        # available = ceiling - current IBKR margin - staged (not yet placed)
        ceiling = round(nlv * margin_budget_pct, 2) if nlv else None
        available = (
            round(ceiling - (current_margin or 0) - staged_margin, 2)
            if ceiling is not None
            else None
        )

        return {
            "nlv": nlv,
            "ceiling": ceiling,
            "current_margin": current_margin,
            "staged_margin": round(staged_margin, 2),
            "staged_count": staged_count,
            "available": available,
            "ibkr_connected": ibkr_connected,
            "margin_budget_pct": margin_budget_pct,
        }

    # ------------------------------------------------------------------
    # POST /api/scanner/calculate-quantity — recommended contract count
    # ------------------------------------------------------------------
    @router.post("/calculate-quantity")
    def calculate_quantity(
        request: QuantityRequest, token: None = Depends(verify_token)
    ):
        from src.agentic.scanner_settings import load_scanner_settings
        from src.services.position_sizer import PositionSizer

        settings = load_scanner_settings()
        budget = settings.budget

        # Determine price-based max from settings
        price_based_max = (
            budget.max_contracts_expensive
            if request.stock_price > budget.price_threshold
            else budget.max_contracts_cheap
        )

        # Try to get NLV from IBKR for risk-based sizing
        nlv = None
        try:
            service = IBKRScannerService()
            summary = service.get_account_summary()
            nlv = summary.get("NetLiquidation")
        except Exception as e:
            logger.warning(f"Quantity calc: IBKR offline — {e}")

        if nlv is None or nlv <= 0:
            return {
                "quantity": 1,
                "source": "default",
                "nlv": None,
                "strike": request.strike,
                "price_based_max": price_based_max,
            }

        sizer = PositionSizer(account_equity=nlv)
        quantity = sizer.calculate_contracts(
            strike=request.strike,
            price_based_max=price_based_max,
        )

        return {
            "quantity": max(1, quantity),
            "source": "position_sizer",
            "nlv": nlv,
            "strike": request.strike,
            "price_based_max": price_based_max,
        }

    # ------------------------------------------------------------------
    # POST /api/scanner/run — execute a scan and persist results
    # ------------------------------------------------------------------
    @router.post("/run")
    def run_scan(request: ScanRequest, token: None = Depends(verify_token)):
        # Build config: preset defaults, then explicit overrides
        if request.preset and request.preset in SCANNER_PRESETS:
            preset = SCANNER_PRESETS[request.preset]
            config = ScannerConfig(
                scan_code=request.scan_code if request.scan_code != "HIGH_OPT_IMP_VOLAT" else preset.get("scan_code", "HIGH_OPT_IMP_VOLAT"),
                instrument=request.instrument if request.instrument != "STK" else preset.get("instrument", "STK"),
                location=request.location if request.location != "STK.US.MAJOR" else preset.get("location", "STK.US.MAJOR"),
                min_price=request.min_price if request.min_price != 20.0 else preset.get("min_price", 20.0),
                max_price=request.max_price if request.max_price != 200.0 else preset.get("max_price", 200.0),
                num_rows=request.num_rows if request.num_rows != 50 else preset.get("num_rows", 50),
                market_cap_above=request.market_cap_above if request.market_cap_above != 0 else preset.get("market_cap_above", 0),
                market_cap_below=request.market_cap_below if request.market_cap_below != 0 else preset.get("market_cap_below", 0),
                avg_volume_above=request.avg_volume_above if request.avg_volume_above != 0 else preset.get("avg_volume_above", 0),
                avg_opt_volume_above=request.avg_opt_volume_above if request.avg_opt_volume_above != 0 else preset.get("avg_opt_volume_above", 0),
                stock_type=request.stock_type or preset.get("stock_type", ""),
            )
        else:
            config = ScannerConfig(
                scan_code=request.scan_code,
                instrument=request.instrument,
                location=request.location,
                min_price=request.min_price,
                max_price=request.max_price,
                num_rows=request.num_rows,
                market_cap_above=request.market_cap_above,
                market_cap_below=request.market_cap_below,
                avg_volume_above=request.avg_volume_above,
                avg_opt_volume_above=request.avg_opt_volume_above,
                stock_type=request.stock_type,
            )

        service = IBKRScannerService()
        start_time = time.time()

        try:
            results = service.run_scan(config)
        except Exception as e:
            logger.error(f"Scanner error: {e}")
            return {
                "error": f"IBKR scanner failed: {e}. Is TWS/Gateway running?",
                "results_count": 0,
                "results": [],
            }

        elapsed = time.time() - start_time

        # Persist scan result + opportunities to DB
        with get_db_session() as db:
            scan = ScanResult(
                scan_timestamp=datetime.utcnow(),
                source="ibkr_scanner",
                config_used={
                    "scan_code": config.scan_code,
                    "instrument": config.instrument,
                    "location": config.location,
                    "min_price": config.min_price,
                    "max_price": config.max_price,
                    "num_rows": config.num_rows,
                    "market_cap_above": config.market_cap_above,
                    "avg_volume_above": config.avg_volume_above,
                    "avg_opt_volume_above": config.avg_opt_volume_above,
                    "preset": request.preset,
                },
                total_candidates=len(results),
                execution_time_seconds=round(elapsed, 2),
            )
            db.add(scan)
            db.flush()  # Get scan.id

            opportunities = []
            for r in results:
                opp = ScanOpportunity(
                    scan_id=scan.id,
                    symbol=r.symbol,
                    strike=0,  # Not yet selected
                    expiration=date.today(),  # Placeholder until enrichment
                    option_type="PUT",
                    source="ibkr_scanner",
                    state="PENDING",
                    entry_notes=json.dumps({
                        "rank": r.rank,
                        "con_id": r.con_id,
                        "exchange": r.exchange,
                        "long_name": r.long_name,
                        "industry": r.industry,
                        "category": r.category,
                        "distance": r.distance,
                        "benchmark": r.benchmark,
                        "projection": r.projection,
                    }),
                )
                db.add(opp)
                opportunities.append(opp)

            db.commit()
            scan_id = scan.id

            # Build response
            result_list = []
            for r, opp in zip(results, opportunities):
                result_list.append({
                    "id": opp.id,
                    "rank": r.rank,
                    "symbol": r.symbol,
                    "con_id": r.con_id,
                    "exchange": r.exchange,
                    "long_name": r.long_name,
                    "industry": r.industry,
                    "category": r.category,
                    "distance": r.distance,
                    "benchmark": r.benchmark,
                    "projection": r.projection,
                    "ai_recommendation": None,
                })

        return {
            "scan_id": scan_id,
            "results_count": len(results),
            "elapsed_seconds": round(elapsed, 2),
            "config": {
                "scan_code": config.scan_code,
                "preset": request.preset,
                "min_price": config.min_price,
                "max_price": config.max_price,
                "num_rows": config.num_rows,
            },
            "results": result_list,
        }

    # ------------------------------------------------------------------
    # GET /api/scanner/results — fetch latest scan results from DB
    # ------------------------------------------------------------------
    @router.get("/results")
    def get_results(scan_id: Optional[int] = None, token: None = Depends(verify_token)):
        with get_db_session() as db:
            # Get the latest scan or a specific one
            if scan_id:
                scan = db.query(ScanResult).get(scan_id)
            else:
                scan = (
                    db.query(ScanResult)
                    .filter(ScanResult.source == "ibkr_scanner")
                    .order_by(ScanResult.scan_timestamp.desc())
                    .first()
                )

            if not scan:
                return {"scan_id": None, "results_count": 0, "results": []}

            opps = (
                db.query(ScanOpportunity)
                .filter(ScanOpportunity.scan_id == scan.id)
                .order_by(ScanOpportunity.id.asc())
                .all()
            )

            result_list = []
            for opp in opps:
                notes = {}
                if opp.entry_notes:
                    try:
                        notes = json.loads(opp.entry_notes) if isinstance(opp.entry_notes, str) else opp.entry_notes
                    except (json.JSONDecodeError, TypeError):
                        pass

                result_list.append({
                    "id": opp.id,
                    "rank": notes.get("rank", 0),
                    "symbol": opp.symbol,
                    "con_id": notes.get("con_id", 0),
                    "exchange": notes.get("exchange", ""),
                    "long_name": notes.get("long_name", ""),
                    "industry": notes.get("industry", ""),
                    "category": notes.get("category", ""),
                    "distance": notes.get("distance", ""),
                    "benchmark": notes.get("benchmark", ""),
                    "projection": notes.get("projection", ""),
                    "state": opp.state,
                    "ai_recommendation": opp.ai_recommendation,
                })

            return {
                "scan_id": scan.id,
                "scan_timestamp": str(scan.scan_timestamp),
                "config": scan.config_used,
                "results_count": len(result_list),
                "elapsed_seconds": scan.execution_time_seconds,
                "results": result_list,
            }

    # ------------------------------------------------------------------
    # POST /api/scanner/recommend — Claude AI analysis of scanned symbols
    # ------------------------------------------------------------------
    @router.post("/recommend")
    def recommend(request: RecommendRequest, token: None = Depends(verify_token)):
        from src.agentic.reasoning_engine import CostTracker

        with get_db_session() as db:
            # Check cost cap
            cost_tracker = CostTracker(db, daily_cap_usd=10.0)
            if not cost_tracker.can_call():
                return {"error": "Daily Claude API cost cap exceeded. Try again tomorrow."}

            # Fetch opportunities for this scan
            opps = (
                db.query(ScanOpportunity)
                .filter(ScanOpportunity.scan_id == request.scan_id)
                .order_by(ScanOpportunity.id.asc())
                .all()
            )

            if not opps:
                return {"error": "No opportunities found for this scan."}

            # Build symbol list with available data
            symbol_data = _build_symbol_data(opps)

            # Determine scan type
            scan_type = (
                opps[0].scan.config_used.get("scan_code", "?")
                if opps[0].scan and opps[0].scan.config_used
                else "?"
            )

            # Call Claude via shared helper
            result = _get_ai_recommendations(
                symbol_data=symbol_data,
                best_strike_data=request.best_strike_data,
                scan_type=scan_type,
                db_session=db,
            )

            if "error" in result:
                return result

            recommendations = result["recommendations"]
            cost = result["cost_usd"]

            # Save recommendations to DB
            rec_map = {r["symbol"]: r for r in recommendations if isinstance(r, dict)}
            updated = 0
            for opp in opps:
                if opp.symbol in rec_map:
                    opp.ai_recommendation = rec_map[opp.symbol]
                    updated += 1
            db.commit()

            logger.info(
                f"Scanner AI recommendations: {updated}/{len(opps)} symbols rated, "
                f"cost=${cost:.4f}"
            )

            return {
                "scan_id": request.scan_id,
                "recommendations": recommendations,
                "updated_count": updated,
                "cost_usd": round(cost, 4),
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
            }

    # ------------------------------------------------------------------
    # POST /api/scanner/auto-select — full pipeline: chains → scores → AI → portfolio
    # ------------------------------------------------------------------
    @router.post("/auto-select")
    def auto_select(request: AutoSelectRequest, token: None = Depends(verify_token)):
        from dataclasses import asdict

        from src.agentic.reasoning_engine import CostTracker
        from src.agentic.scanner_settings import load_scanner_settings
        from src.data.sector_map import get_sector
        from src.services.auto_selector import (
            AutoSelector,
            PortfolioCandidate,
            build_auto_select_portfolio,
            compute_composite_score_4w,
        )
        from src.services.position_sizer import PositionSizer

        settings = load_scanner_settings()
        t0 = time.time()

        with get_db_session() as db:
            # Check cost cap
            cost_tracker = CostTracker(db, daily_cap_usd=10.0)
            if not cost_tracker.can_call():
                return {"error": "Daily Claude API cost cap exceeded. Try again tomorrow."}

            # Step 1: Verify IBKR → get NLV, current margin
            nlv = None
            current_margin = 0.0
            vix = None
            ibkr_connected = False

            try:
                service = IBKRScannerService()
                summary = service.get_account_summary()
                nlv = summary.get("NetLiquidation")
                current_margin = summary.get("FullMaintMarginReq") or 0.0
                ibkr_connected = nlv is not None
            except Exception as e:
                logger.warning(f"Auto-select: IBKR account summary failed — {e}")

            if not ibkr_connected and not request.override_market_hours:
                return {"error": "IBKR offline. Cannot auto-select without live account data."}

            # Step 2: Get VIX for position sizing
            try:
                service = IBKRScannerService()
                vix = service.get_vix()
            except Exception as e:
                logger.warning(f"Auto-select: VIX fetch failed — {e}")

            # Step 3: Calculate available budget
            margin_budget_pct = settings.budget.margin_budget_pct

            # Get staged margin
            staged_margin = 0.0
            try:
                staged_opps = (
                    db.query(ScanOpportunity)
                    .filter(ScanOpportunity.state == "STAGED")
                    .all()
                )
                staged_margin = sum(
                    opp.staged_margin for opp in staged_opps if opp.staged_margin
                )
            except Exception as e:
                logger.warning(f"Auto-select: staged margin query failed — {e}")

            ceiling = round(nlv * margin_budget_pct, 2) if nlv else 0
            available_budget = max(0, ceiling - current_margin - staged_margin)

            if available_budget <= 0 and not request.override_market_hours:
                return {
                    "error": "No available margin budget. Reduce staged positions or increase budget %.",
                    "budget": {
                        "nlv": nlv,
                        "ceiling": ceiling,
                        "current_margin": current_margin,
                        "staged_margin": round(staged_margin, 2),
                        "available": 0,
                    },
                }

            # Step 4: Fetch PENDING opportunities for this scan
            opps = (
                db.query(ScanOpportunity)
                .filter(
                    ScanOpportunity.scan_id == request.scan_id,
                    ScanOpportunity.state == "PENDING",
                )
                .order_by(ScanOpportunity.id.asc())
                .all()
            )

            if not opps:
                return {"error": "No PENDING opportunities for this scan."}

            symbols = [opp.symbol for opp in opps]
            opp_id_map = {opp.symbol: opp.id for opp in opps}

            logger.info(
                f"Auto-select: {len(symbols)} symbols, "
                f"budget=${available_budget:,.0f} "
                f"(NLV=${nlv:,.0f}, ceiling=${ceiling:,.0f})"
            )

            # Step 5: Load chains batch
            max_dte = settings.filters.max_dte
            try:
                service = IBKRScannerService()
                all_chains = service.get_option_chains_batch(symbols, max_dte=max_dte)
            except Exception as e:
                logger.error(f"Auto-select: batch chain load failed — {e}")
                return {"error": f"Chain loading failed: {e}"}

            chains_loaded = sum(
                1 for v in all_chains.values()
                if v.get("stock_price") and v.get("expirations")
            )

            # Step 6: Filter candidates + batch margin queries
            selector = AutoSelector(settings)
            all_candidates: dict[str, list] = {}
            margin_queries: list[dict] = []

            for symbol, chain_data in all_chains.items():
                candidates = selector.filter_candidates(chain_data)
                all_candidates[symbol] = candidates
                for c in candidates:
                    exp_yyyymmdd = c.expiration.replace("-", "")
                    margin_queries.append({
                        "symbol": c.symbol,
                        "strike": c.strike,
                        "expiration_yyyymmdd": exp_yyyymmdd,
                        "stock_price": c.stock_price,
                        "bid": c.bid,
                    })

            total_candidates = sum(len(v) for v in all_candidates.values())

            # Batch margin query
            margins: dict[str, float | None] = {}
            if margin_queries:
                try:
                    service = IBKRScannerService()
                    margins = service.get_option_margins_batch(margin_queries)
                except Exception as e:
                    logger.warning(f"Auto-select: margin query failed — {e}")

            # Step 7: Select best strike per symbol (3-weight)
            best_results = selector.select_best_per_symbol(all_candidates, margins)

            # Enrich with sector and contracts
            for result in best_results:
                if result.status == "skipped":
                    continue
                result.sector = get_sector(result.symbol)
                if nlv and nlv > 0:
                    budget = settings.budget
                    price_based_max = (
                        budget.max_contracts_expensive
                        if result.stock_price > budget.price_threshold
                        else budget.max_contracts_cheap
                    )
                    sizer = PositionSizer(account_equity=nlv)
                    result.contracts = max(
                        1, sizer.calculate_contracts(
                            strike=result.strike,
                            price_based_max=price_based_max,
                            vix=vix,
                        )
                    )

            # Step 8: Call Claude for AI recommendations
            symbol_data = _build_symbol_data(opps)
            active_best = [r for r in best_results if r.status != "skipped"]
            best_strike_dicts = []
            for r in active_best:
                best_strike_dicts.append({
                    "symbol": r.symbol,
                    "stock_price": r.stock_price,
                    "strike": r.strike,
                    "delta": r.delta,
                    "otm_pct": r.otm_pct,
                    "iv": r.iv,
                    "dte": r.dte,
                    "bid": r.bid,
                    "ask": r.ask,
                    "open_interest": r.open_interest,
                    "volume": r.volume,
                    "margin": r.margin,
                    "margin_source": r.margin_source,
                    "premium_margin_ratio": r.premium_margin_ratio,
                    "annualized_return_pct": r.annualized_return_pct,
                    "composite_score": r.composite_score,
                    "sector": r.sector,
                })

            scan_type = (
                opps[0].scan.config_used.get("scan_code", "?")
                if opps[0].scan and opps[0].scan.config_used
                else "?"
            )

            ai_map: dict[str, dict] = {}
            ai_cost = 0.0
            ai_result = _get_ai_recommendations(
                symbol_data=symbol_data,
                best_strike_data=best_strike_dicts if best_strike_dicts else None,
                scan_type=scan_type,
                db_session=db,
            )

            if "error" not in ai_result:
                ai_cost = ai_result.get("cost_usd", 0)
                for rec in ai_result.get("recommendations", []):
                    if isinstance(rec, dict) and "symbol" in rec:
                        ai_map[rec["symbol"]] = rec
                # Save to DB
                for opp in opps:
                    if opp.symbol in ai_map:
                        opp.ai_recommendation = ai_map[opp.symbol]
                db.commit()
            else:
                logger.warning(f"Auto-select: AI recommendations failed — {ai_result.get('error')}")

            # Step 9: Build PortfolioCandidates with 4-weight composite scores
            r = settings.ranking
            portfolio_candidates: list[PortfolioCandidate] = []

            for bs in best_results:
                if bs.status == "skipped":
                    continue

                ai_data = ai_map.get(bs.symbol)
                pc = PortfolioCandidate.from_best_strike(bs, ai_data=ai_data)

                ai_score_raw = ai_data.get("score") if ai_data else None
                pc.composite_score = compute_composite_score_4w(
                    safety=bs.safety_score,
                    liquidity=bs.liquidity_score,
                    efficiency=bs.efficiency_score,
                    ai_score_raw=ai_score_raw,
                    w_safety=r.safety,
                    w_liquidity=r.liquidity,
                    w_ai=r.ai_score,
                    w_efficiency=r.efficiency,
                )
                portfolio_candidates.append(pc)

            # Step 10: Greedy portfolio selection within budget
            selected, skipped, warnings = build_auto_select_portfolio(
                portfolio_candidates,
                available_budget=available_budget,
                max_positions=settings.budget.max_positions,
                max_per_sector=settings.budget.max_per_sector,
            )

            # Step 11: Build config snapshot
            config_snapshot = {
                "auto_select_config": settings.model_dump(),
                "vix": vix,
                "nlv": nlv,
                "available_budget": available_budget,
                "scan_type": scan_type,
                "timestamp": datetime.utcnow().isoformat(),
            }

            # Build response
            elapsed = time.time() - t0

            def _candidate_dict(pc: PortfolioCandidate) -> dict:
                return {
                    "symbol": pc.symbol,
                    "stock_price": pc.stock_price,
                    "strike": pc.strike,
                    "expiration": pc.expiration,
                    "dte": pc.dte,
                    "bid": pc.bid,
                    "ask": pc.ask,
                    "delta": pc.delta,
                    "iv": pc.iv,
                    "otm_pct": pc.otm_pct,
                    "volume": pc.volume,
                    "open_interest": pc.open_interest,
                    "margin": pc.margin,
                    "margin_source": pc.margin_source,
                    "safety_score": pc.safety_score,
                    "liquidity_score": pc.liquidity_score,
                    "efficiency_score": pc.efficiency_score,
                    "composite_score": pc.composite_score,
                    "premium_margin_ratio": pc.premium_margin_ratio,
                    "annualized_return_pct": pc.annualized_return_pct,
                    "contracts": pc.contracts,
                    "sector": pc.sector,
                    "ai_score": pc.ai_score,
                    "ai_recommendation": pc.ai_recommendation,
                    "ai_reasoning": pc.ai_reasoning,
                    "ai_risk_flags": pc.ai_risk_flags,
                    "total_margin": pc.total_margin,
                    "portfolio_rank": pc.portfolio_rank,
                    "selected": pc.selected,
                    "skip_reason": pc.skip_reason,
                    "opportunity_id": opp_id_map.get(pc.symbol),
                }

            used_by_selection = sum(s.total_margin for s in selected)

            logger.info(
                f"Auto-select complete: {len(selected)} selected, "
                f"{len(skipped)} skipped, ${used_by_selection:,.0f} margin used, "
                f"{elapsed:.1f}s elapsed, AI cost=${ai_cost:.4f}"
            )

            return {
                "portfolio": {
                    "selected": [_candidate_dict(s) for s in selected],
                    "skipped": [_candidate_dict(s) for s in skipped],
                    "warnings": warnings,
                },
                "budget": {
                    "nlv": nlv,
                    "ceiling": ceiling,
                    "current_margin": current_margin,
                    "staged_margin": round(staged_margin, 2),
                    "available": round(available_budget, 2),
                    "used_by_selection": round(used_by_selection, 2),
                    "remaining": round(available_budget - used_by_selection, 2),
                },
                "summary": {
                    "symbols_scanned": len(symbols),
                    "chains_loaded": chains_loaded,
                    "candidates_filtered": total_candidates,
                    "best_strikes_found": len(active_best),
                    "ai_scored": len(ai_map),
                    "selected": len(selected),
                    "skipped": len(skipped),
                    "elapsed_seconds": round(elapsed, 1),
                    "ai_cost_usd": round(ai_cost, 4),
                },
                "config_snapshot": config_snapshot,
                "stale_data": request.override_market_hours,
            }

    # ------------------------------------------------------------------
    # POST /api/scanner/chain — fetch PUT option chain for a symbol
    # ------------------------------------------------------------------
    @router.post("/chain")
    def get_chain(request: ChainRequest, token: None = Depends(verify_token)):
        service = IBKRScannerService()
        try:
            result = service.get_option_chain(request.symbol, max_dte=request.max_dte)
        except Exception as e:
            logger.error(f"Chain fetch error for {request.symbol}: {e}")
            return {"error": f"IBKR chain fetch failed: {e}. Is TWS/Gateway running?"}

        return result

    # ------------------------------------------------------------------
    # POST /api/scanner/select-best — auto-select best strike per symbol
    # ------------------------------------------------------------------
    @router.post("/select-best")
    def select_best(request: SelectBestRequest, token: None = Depends(verify_token)):
        from src.agentic.scanner_settings import load_scanner_settings
        from src.data.sector_map import get_sector
        from src.services.auto_selector import AutoSelector
        from src.services.position_sizer import PositionSizer

        settings = load_scanner_settings()
        selector = AutoSelector(settings)

        # Step 1: Filter candidates across all symbols
        all_candidates: dict[str, list] = {}
        margin_queries: list[dict] = []

        for symbol, chain_data in request.chains.items():
            candidates = selector.filter_candidates(chain_data)
            all_candidates[symbol] = candidates
            for c in candidates:
                exp_yyyymmdd = c.expiration.replace("-", "")
                margin_queries.append({
                    "symbol": c.symbol,
                    "strike": c.strike,
                    "expiration_yyyymmdd": exp_yyyymmdd,
                    "stock_price": c.stock_price,
                    "bid": c.bid,
                })

        total_candidates = sum(len(v) for v in all_candidates.values())
        if total_candidates == 0:
            return {
                "results": [],
                "margins_queried": 0,
                "symbols_skipped": list(request.chains.keys()),
            }

        # Step 2: Query IBKR margins for all candidates
        margins: dict[str, float | None] = {}
        try:
            service = IBKRScannerService()
            margins = service.get_option_margins_batch(margin_queries)
        except Exception as e:
            logger.warning(f"Select-best: margin query failed — {e}")
            # Continue with Reg-T fallbacks (handled inside select_best_per_symbol)

        # Step 3: Score and select best per symbol
        best_results = selector.select_best_per_symbol(all_candidates, margins)

        # Step 4: Enrich with sector and contracts
        nlv = None
        try:
            service = IBKRScannerService()
            summary = service.get_account_summary()
            nlv = summary.get("NetLiquidation")
        except Exception as e:
            logger.warning(f"Select-best: NLV query failed — {e}")

        for result in best_results:
            if result.status == "skipped":
                continue

            # Sector
            result.sector = get_sector(result.symbol)

            # Contracts via PositionSizer
            if nlv and nlv > 0:
                budget = settings.budget
                price_based_max = (
                    budget.max_contracts_expensive
                    if result.stock_price > budget.price_threshold
                    else budget.max_contracts_cheap
                )
                sizer = PositionSizer(account_equity=nlv)
                result.contracts = max(
                    1, sizer.calculate_contracts(
                        strike=result.strike,
                        price_based_max=price_based_max,
                    )
                )

        # Build response
        symbols_skipped = [
            r.symbol for r in best_results if r.status == "skipped"
        ]
        result_dicts = []
        for r in best_results:
            d = {
                "symbol": r.symbol,
                "stock_price": r.stock_price,
                "strike": r.strike,
                "expiration": r.expiration,
                "dte": r.dte,
                "bid": r.bid,
                "ask": r.ask,
                "delta": r.delta,
                "iv": r.iv,
                "otm_pct": r.otm_pct,
                "volume": r.volume,
                "open_interest": r.open_interest,
                "margin": r.margin,
                "margin_source": r.margin_source,
                "safety_score": r.safety_score,
                "liquidity_score": r.liquidity_score,
                "efficiency_score": r.efficiency_score,
                "composite_score": r.composite_score,
                "premium_margin_ratio": r.premium_margin_ratio,
                "annualized_return_pct": r.annualized_return_pct,
                "contracts": r.contracts,
                "sector": r.sector,
                "status": r.status,
                "skip_reason": r.skip_reason,
                "opportunity_id": request.opportunity_ids.get(r.symbol),
            }
            result_dicts.append(d)

        logger.info(
            f"Select-best: {len(best_results)} symbols, "
            f"{len(margins)} margins queried, "
            f"{len(symbols_skipped)} skipped"
        )

        return {
            "results": result_dicts,
            "margins_queried": len(margins),
            "symbols_skipped": symbols_skipped,
        }

    # ------------------------------------------------------------------
    # POST /api/scanner/stage — stage candidates with full contract data
    # ------------------------------------------------------------------
    @router.post("/stage")
    def stage_candidates(request: StageRequest, token: None = Depends(verify_token)):
        # New flow: stage with full contract details
        if request.selections:
            return _stage_with_contracts(request.selections)

        # Legacy flow: stage by IDs only (kept for backwards compat)
        if not request.opportunity_ids:
            return {"error": "No opportunities selected."}

        with get_db_session() as db:
            opps = (
                db.query(ScanOpportunity)
                .filter(ScanOpportunity.id.in_(request.opportunity_ids))
                .all()
            )

            staged_count = 0
            for opp in opps:
                opp.state = "STAGED"
                opp.staged_at = datetime.utcnow()
                staged_count += 1

            db.commit()

            logger.info(f"Scanner: staged {staged_count} candidates for pipeline")

            return {
                "staged_count": staged_count,
                "opportunity_ids": [opp.id for opp in opps],
            }

    return router


def _stage_with_contracts(selections: list) -> dict:
    """Stage opportunities with full contract details from chain selection.

    Updates each ScanOpportunity with the user-selected strike, expiration,
    bid/ask, delta, and IV — replacing the placeholder values that were set
    when the scanner first created the opportunity (strike=0, exp=today).

    Args:
        selections: List of StageContractSelection objects

    Returns:
        Dict with staged_count and opportunity_ids
    """
    with get_db_session() as db:
        staged_count = 0
        staged_ids = []

        for sel in selections:
            opp = db.query(ScanOpportunity).get(sel.opportunity_id)
            if not opp:
                logger.warning(f"Stage: opportunity {sel.opportunity_id} not found")
                continue

            # Parse expiration string "2026-02-28" → date object
            try:
                exp_parts = sel.expiration.split("-")
                exp_date = date(int(exp_parts[0]), int(exp_parts[1]), int(exp_parts[2]))
            except (ValueError, IndexError):
                logger.warning(f"Stage: invalid expiration '{sel.expiration}'")
                continue

            # Update with real contract data
            opp.strike = sel.strike
            opp.expiration = exp_date
            opp.bid = sel.bid
            opp.ask = sel.ask
            premium = round((sel.bid + sel.ask) / 2, 2) if sel.bid and sel.ask else sel.bid
            opp.premium = premium
            opp.delta = sel.delta
            opp.iv = sel.iv
            opp.stock_price = sel.stock_price
            opp.otm_pct = sel.otm_pct
            opp.dte = (exp_date - date.today()).days
            opp.staged_contracts = sel.contracts
            opp.staged_limit_price = premium
            opp.state = "STAGED"
            opp.staged_at = datetime.utcnow()

            # Use Phase 3 IBKR margin if provided, else Reg-T estimate
            if sel.margin_actual is not None and sel.margin_actual > 0:
                opp.margin_required = round(sel.margin_actual, 2)
                opp.staged_margin = round(sel.margin_actual * sel.contracts, 2)
                opp.staged_margin_source = sel.margin_source or "ibkr_whatif"
            elif sel.stock_price and sel.stock_price > 0 and sel.strike > 0:
                otm_amount = max(0, sel.stock_price - sel.strike)
                margin = (0.20 * sel.stock_price - otm_amount + (premium or 0)) * 100
                min_margin = 0.10 * sel.stock_price * 100
                opp.margin_required = round(max(margin, min_margin), 2)
                opp.staged_margin = opp.margin_required
                opp.staged_margin_source = "estimated"

            if opp.margin_required and opp.margin_required > 0 and premium:
                opp.margin_efficiency = round((premium * 100) / opp.margin_required, 4)

            # Phase 3: portfolio rank and config snapshot
            if sel.portfolio_rank is not None:
                opp.portfolio_rank = sel.portfolio_rank
            if sel.config_snapshot is not None:
                opp.enrichment_snapshot = sel.config_snapshot

            staged_count += 1
            staged_ids.append(opp.id)

        db.commit()

        logger.info(
            f"Scanner: staged {staged_count} candidates with contract details"
        )

        return {
            "staged_count": staged_count,
            "opportunity_ids": staged_ids,
        }


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------


def get_scanner_html() -> str:
    """Return the scanner HTML page."""
    return _SCANNER_HTML


_SCANNER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TAAD Option Scanner</title>
<style>
  :root {
    --bg: #0f1923; --bg2: #172a3a; --bg3: #1e3a50;
    --border: #2a4a6b; --text: #c8d6e5; --text-dim: #6b8299;
    --accent: #00d4ff; --green: #00e676; --yellow: #ffd600;
    --red: #ff5252; --orange: #ff9100;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; background: var(--bg); color: var(--text); font-size: 13px; }

  /* Header */
  .header { background: var(--bg2); border-bottom: 1px solid var(--border); padding: 12px 24px; display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 16px; color: var(--accent); font-weight: 600; }
  .header h1 span { color: var(--text-dim); font-weight: 400; }
  .back-link { color: var(--text-dim); text-decoration: none; font-size: 12px; border: 1px solid var(--border); padding: 4px 12px; border-radius: 4px; }
  .back-link:hover { border-color: var(--accent); color: var(--accent); }

  /* Layout */
  .container { max-width: 1400px; padding: 16px 24px; }

  /* Cards */
  .card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 16px; }
  .card-header { padding: 10px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
  .card-header h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); font-weight: 600; }
  .card-body { padding: 16px; }

  /* Form controls */
  .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
  .form-group { display: flex; flex-direction: column; gap: 4px; }
  .form-group label { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); font-weight: 600; }
  .form-group select, .form-group input {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    padding: 6px 10px; border-radius: 4px; font-family: inherit; font-size: 12px;
  }
  .form-group select:focus, .form-group input:focus { border-color: var(--accent); outline: none; }
  .form-group select option { background: var(--bg2); color: var(--text); }

  /* Buttons */
  .btn { border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; font-weight: 600; transition: all 0.15s; }
  .btn-primary { background: rgba(0, 212, 255, 0.15); color: var(--accent); border: 1px solid rgba(0, 212, 255, 0.3); }
  .btn-primary:hover { background: rgba(0, 212, 255, 0.3); }
  .btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-green { background: rgba(0, 230, 118, 0.15); color: var(--green); border: 1px solid rgba(0, 230, 118, 0.3); }
  .btn-green:hover { background: rgba(0, 230, 118, 0.3); }
  .btn-green:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-orange { background: rgba(255, 145, 0, 0.15); color: var(--orange); border: 1px solid rgba(255, 145, 0, 0.3); }
  .btn-orange:hover { background: rgba(255, 145, 0, 0.3); }
  .btn-orange:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-row { display: flex; gap: 8px; align-items: center; margin-top: 12px; }

  /* Table */
  table { width: 100%; border-collapse: collapse; }
  th { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); font-weight: 600; position: sticky; top: 0; background: var(--bg2); }
  td { padding: 8px 10px; border-bottom: 1px solid rgba(42, 74, 107, 0.4); font-size: 12px; }
  tr:hover td { background: rgba(0, 212, 255, 0.03); }
  tr.selected td { background: rgba(0, 212, 255, 0.08); }
  input[type="checkbox"] { cursor: pointer; accent-color: var(--accent); }
  input[type="radio"] { cursor: pointer; accent-color: var(--green); }

  /* Badges */
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
  .badge-strong-buy { background: rgba(0, 230, 118, 0.2); color: var(--green); }
  .badge-buy { background: rgba(0, 230, 118, 0.1); color: #80ffc0; }
  .badge-neutral { background: rgba(255, 214, 0, 0.15); color: var(--yellow); }
  .badge-avoid { background: rgba(255, 82, 82, 0.15); color: var(--red); }
  .badge-pending { background: rgba(107, 130, 153, 0.15); color: var(--text-dim); }
  .badge-staged { background: rgba(0, 230, 118, 0.15); color: var(--green); }
  .badge-rec { background: rgba(0, 230, 118, 0.15); color: var(--green); font-size: 9px; padding: 1px 6px; }

  .score { font-weight: 700; font-size: 14px; }
  .score-high { color: var(--green); }
  .score-mid { color: var(--yellow); }
  .score-low { color: var(--red); }

  /* Actions bar */
  .actions-bar { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; background: var(--bg3); border-top: 1px solid var(--border); }
  .actions-bar .selected-info { font-size: 12px; color: var(--text-dim); }
  .actions-bar .selected-info b { color: var(--accent); }

  /* Status text */
  .status-msg { padding: 20px; text-align: center; color: var(--text-dim); font-style: italic; }
  .error-msg { color: var(--red); }

  /* Scan info */
  .scan-info { display: flex; gap: 20px; flex-wrap: wrap; font-size: 12px; color: var(--text-dim); margin-bottom: 12px; }
  .scan-info span b { color: var(--accent); font-weight: 600; }

  /* Spinner */
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid var(--border); border-top: 2px solid var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle; margin-right: 6px; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Risk flags */
  .risk-flag { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 9px; font-weight: 600; text-transform: uppercase; background: rgba(255, 82, 82, 0.1); color: var(--red); margin-right: 3px; margin-top: 2px; }

  /* Toast */
  .toast { position: fixed; bottom: 20px; right: 20px; background: var(--bg3); border: 1px solid var(--green); color: var(--green); padding: 12px 20px; border-radius: 6px; font-size: 13px; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100; }
  .toast.show { opacity: 1; }
  .toast.error { border-color: var(--red); color: var(--red); }

  /* Tooltip */
  .tip { position: relative; cursor: help; }
  .tip:hover::after { content: attr(data-tip); position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); background: var(--bg3); border: 1px solid var(--border); color: var(--text); padding: 4px 8px; border-radius: 4px; font-size: 11px; white-space: nowrap; z-index: 10; }

  /* Chain panel */
  .chain-symbol { border: 1px solid var(--border); border-radius: 6px; margin-bottom: 12px; overflow: hidden; }
  .chain-symbol-header { display: flex; align-items: center; justify-content: space-between; padding: 10px 16px; background: var(--bg3); cursor: pointer; }
  .chain-symbol-header:hover { background: rgba(30, 58, 80, 0.8); }
  .chain-symbol-header h3 { font-size: 14px; color: var(--accent); font-weight: 700; }
  .chain-symbol-header .chain-price { font-size: 13px; color: var(--text); }
  .chain-symbol-header .chain-status { font-size: 11px; }
  .chain-symbol-body { display: none; }
  .chain-symbol-body.open { display: block; }

  /* Expiration tabs */
  .exp-tabs { display: flex; gap: 0; border-bottom: 1px solid var(--border); }
  .exp-tab { padding: 8px 16px; font-size: 11px; font-weight: 600; color: var(--text-dim); cursor: pointer; border-bottom: 2px solid transparent; transition: all 0.15s; }
  .exp-tab:hover { color: var(--text); background: rgba(0, 212, 255, 0.03); }
  .exp-tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  /* Chain table rows */
  .chain-table tr.recommended td { background: rgba(0, 230, 118, 0.06); }
  .chain-table tr.chain-selected td { background: rgba(0, 212, 255, 0.12); }
  .chain-table tr.no-greeks td { opacity: 0.5; }
  .chain-table td.strike-col { font-weight: 700; }

  /* Settings panel */
  .settings-toggle { cursor: pointer; user-select: none; }
  .settings-toggle .arrow { display: inline-block; transition: transform 0.2s; font-size: 10px; margin-right: 6px; }
  .settings-toggle .arrow.open { transform: rotate(90deg); }
  .settings-body { display: none; }
  .settings-body.open { display: block; }
  .settings-section { margin-bottom: 16px; }
  .settings-section h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--accent); margin-bottom: 8px; font-weight: 600; }
  .weights-sum { font-size: 11px; margin-top: 6px; }
  .weights-sum.valid { color: var(--green); }
  .weights-sum.invalid { color: var(--red); }

  /* Budget bar */
  .budget-bar { display: flex; align-items: center; gap: 16px; padding: 8px 16px; background: var(--bg3); border-bottom: 1px solid var(--border); font-size: 12px; flex-wrap: wrap; }
  .budget-bar .budget-item { display: flex; align-items: center; gap: 4px; }
  .budget-bar .budget-item b { color: var(--accent); font-weight: 600; }
  .budget-bar .budget-item.warn b { color: var(--red); }
  .budget-bar .budget-offline { color: var(--red); font-weight: 600; }
  .budget-meter { flex: 1; min-width: 120px; height: 6px; background: var(--bg); border-radius: 3px; overflow: hidden; }
  .budget-meter-fill { height: 100%; border-radius: 3px; transition: width 0.3s; }

  /* Quantity input in chain header */
  .qty-group { display: flex; align-items: center; gap: 6px; }
  .qty-input { width: 52px; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 3px 6px; border-radius: 4px; font-family: inherit; font-size: 12px; text-align: center; }
  .qty-input:focus { border-color: var(--accent); outline: none; }
  .qty-label { font-size: 10px; color: var(--text-dim); }

  /* Best-strike score badge */
  .best-score-badge { font-size: 12px; font-weight: 700; margin-left: 12px; display: inline-block; }
  .best-score-badge .score-pct { font-size: 14px; }
  .best-score-badge .score-detail { font-size: 10px; color: var(--text-dim); margin-left: 4px; }

  /* Portfolio preview */
  .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr)); gap: 8px; padding: 12px 16px; }
  .summary-stat { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; text-align: center; }
  .summary-stat .stat-value { font-size: 18px; font-weight: 700; color: var(--accent); }
  .summary-stat .stat-label { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-dim); margin-top: 2px; }
  .portfolio-table tr.removable:hover { background: rgba(255, 82, 82, 0.05); }
  .portfolio-table .btn-remove { background: none; border: 1px solid rgba(255, 82, 82, 0.3); color: var(--red); padding: 2px 8px; border-radius: 3px; font-size: 10px; cursor: pointer; font-family: inherit; }
  .portfolio-table .btn-remove:hover { background: rgba(255, 82, 82, 0.15); }
  .skipped-toggle { cursor: pointer; padding: 10px 16px; color: var(--text-dim); font-size: 12px; border-top: 1px solid var(--border); }
  .skipped-toggle:hover { color: var(--text); }
  .skipped-body { display: none; }
  .skipped-body.open { display: block; }
</style>
</head>
<body>

<div class="header">
  <h1>TAAD <span>Option Scanner</span></h1>
  <a href="/" class="back-link">Back to Dashboard</a>
</div>

<div class="container">
  <!-- Config Card -->
  <div class="card">
    <div class="card-header">
      <h2>Scan Configuration</h2>
    </div>
    <div class="card-body">
      <div class="form-grid">
        <div class="form-group">
          <label>Preset</label>
          <select id="preset" onchange="applyPreset()">
            <option value="">-- Custom --</option>
          </select>
        </div>
        <div class="form-group">
          <label>Scan Code</label>
          <select id="scan_code"></select>
        </div>
        <div class="form-group">
          <label>Min Price ($)</label>
          <input type="number" id="min_price" value="20" min="0" step="5">
        </div>
        <div class="form-group">
          <label>Max Price ($)</label>
          <input type="number" id="max_price" value="200" min="0" step="10">
        </div>
        <div class="form-group">
          <label>Max Results</label>
          <input type="number" id="num_rows" value="50" min="10" max="100" step="10">
        </div>
        <div class="form-group">
          <label>Market Cap Above (M$)</label>
          <input type="number" id="market_cap_above" value="2000" min="0" step="500">
        </div>
        <div class="form-group">
          <label>Avg Volume Above</label>
          <input type="number" id="avg_volume_above" value="500000" min="0" step="100000">
        </div>
        <div class="form-group">
          <label>Avg Opt Volume Above</label>
          <input type="number" id="avg_opt_volume_above" value="1000" min="0" step="500">
        </div>
        <div class="form-group">
          <label>Max DTE (chains)</label>
          <input type="number" id="max_dte" value="7" min="1" max="90" step="1" title="Maximum days to expiration when loading option chains">
        </div>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary" id="btn-scan" onclick="runScan()">Run Scan</button>
        <span id="scan-status" style="font-size:12px;color:var(--text-dim);"></span>
      </div>
    </div>
  </div>

  <!-- Scanner Settings Card (collapsed by default) -->
  <div class="card" id="settings-card">
    <div class="card-header settings-toggle" onclick="toggleSettings()">
      <h2><span class="arrow" id="settings-arrow">&#9654;</span> Scanner Settings</h2>
      <span style="font-size:11px;color:var(--text-dim)">Auto-Select parameters</span>
    </div>
    <div class="card-body settings-body" id="settings-body">
      <div class="settings-section">
        <h3>Filter Criteria</h3>
        <div class="form-grid">
          <div class="form-group">
            <label>Delta Min</label>
            <input type="number" id="set-delta-min" step="0.01" min="0" max="1">
          </div>
          <div class="form-group">
            <label>Delta Max</label>
            <input type="number" id="set-delta-max" step="0.01" min="0" max="1">
          </div>
          <div class="form-group">
            <label>Delta Target</label>
            <input type="number" id="set-delta-target" step="0.001" min="0" max="1">
          </div>
          <div class="form-group">
            <label>Min Premium ($)</label>
            <input type="number" id="set-min-premium" step="0.05" min="0">
          </div>
          <div class="form-group">
            <label>Min OTM %</label>
            <input type="number" id="set-min-otm-pct" step="0.01" min="0" max="1">
          </div>
          <div class="form-group">
            <label>Max DTE</label>
            <input type="number" id="set-max-dte" min="1" max="90">
          </div>
          <div class="form-group">
            <label>Prefer Shortest DTE</label>
            <select id="set-dte-prefer-shortest">
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </div>
        </div>
      </div>
      <div class="settings-section">
        <h3>Ranking Weights</h3>
        <div class="form-grid">
          <div class="form-group">
            <label>Safety</label>
            <input type="number" id="set-safety" min="0" max="100" oninput="updateWeightsSum()">
          </div>
          <div class="form-group">
            <label>Liquidity</label>
            <input type="number" id="set-liquidity" min="0" max="100" oninput="updateWeightsSum()">
          </div>
          <div class="form-group">
            <label>AI Score</label>
            <input type="number" id="set-ai-score" min="0" max="100" oninput="updateWeightsSum()">
          </div>
          <div class="form-group">
            <label>Efficiency</label>
            <input type="number" id="set-efficiency" min="0" max="100" oninput="updateWeightsSum()">
          </div>
        </div>
        <div class="weights-sum" id="weights-sum">Sum: 100 / 100</div>
      </div>
      <div class="settings-section">
        <h3>Budget &amp; Limits</h3>
        <div class="form-grid">
          <div class="form-group">
            <label>Margin Budget %</label>
            <input type="number" id="set-margin-budget-pct" step="0.01" min="0.01" max="1">
          </div>
          <div class="form-group">
            <label>Max Positions</label>
            <input type="number" id="set-max-positions" min="1" max="50">
          </div>
          <div class="form-group">
            <label>Max Per Sector</label>
            <input type="number" id="set-max-per-sector" min="1" max="50">
          </div>
          <div class="form-group">
            <label>Price Threshold ($)</label>
            <input type="number" id="set-price-threshold" step="5" min="0">
          </div>
          <div class="form-group">
            <label>Max Contracts (Expensive)</label>
            <input type="number" id="set-max-contracts-expensive" min="1" max="50">
          </div>
          <div class="form-group">
            <label>Max Contracts (Cheap)</label>
            <input type="number" id="set-max-contracts-cheap" min="1" max="50">
          </div>
        </div>
      </div>
      <div class="btn-row">
        <button class="btn btn-green" id="btn-save-settings" onclick="saveSettings()">Save Settings</button>
        <span id="settings-status" style="font-size:12px;color:var(--text-dim);"></span>
      </div>
    </div>
  </div>

  <!-- Results Card -->
  <div class="card" id="results-card">
    <div class="card-header">
      <h2>Scan Results</h2>
      <span id="result-count" style="font-size:11px;color:var(--text-dim);"></span>
    </div>
    <div class="card-body" style="padding:0;">
      <div id="scan-info" class="scan-info" style="padding:12px 16px 0;display:none;"></div>
      <div id="results-body" style="max-height:600px;overflow-y:auto;">
        <div class="status-msg">No results yet. Configure scan and click "Run Scan".</div>
      </div>
    </div>
    <div class="actions-bar" id="actions-bar" style="display:none;">
      <div class="selected-info">
        <b id="selected-count">0</b> selected
      </div>
      <div style="display:flex;gap:8px;">
        <button class="btn btn-green" id="btn-auto-select" onclick="autoSelect()" disabled>Auto-Select Best</button>
        <button class="btn btn-orange" id="btn-recommend" onclick="aiRecommend()" disabled>AI Recommend</button>
        <button class="btn btn-primary" id="btn-load-chains" onclick="loadChains()" disabled>Load Chains</button>
      </div>
    </div>
  </div>

  <!-- Portfolio Preview Card (Phase 3: Auto-Select) -->
  <div class="card" id="portfolio-card" style="display:none;">
    <div class="card-header">
      <h2>Portfolio Preview</h2>
      <span id="portfolio-status" style="font-size:11px;color:var(--text-dim);"></span>
    </div>
    <div id="portfolio-stale-warning" style="display:none;background:rgba(255,145,0,0.15);border-bottom:1px solid var(--orange);padding:8px 16px;font-size:12px;font-weight:700;color:var(--orange);text-align:center;">
      STALE DATA &mdash; FOR TESTING ONLY (market closed, override active)
    </div>
    <div id="portfolio-summary" class="budget-bar" style="display:none;"></div>
    <div id="portfolio-budget-bar" class="budget-bar" style="display:none;"></div>
    <div class="card-body" id="portfolio-body" style="padding:12px;">
      <div class="status-msg">Click "Auto-Select Best" to build a portfolio.</div>
    </div>
    <div class="actions-bar" id="portfolio-actions" style="display:none;">
      <div class="selected-info" id="portfolio-count-info">
        <b>0</b> trades | $0 margin
      </div>
      <button class="btn btn-green" id="btn-stage-portfolio" onclick="stagePortfolio()">Stage All</button>
    </div>
  </div>

  <!-- Chain Selection Card -->
  <div class="card" id="chain-card" style="display:none;">
    <div class="card-header">
      <h2>Option Chain Selection</h2>
      <span id="chain-status" style="font-size:11px;color:var(--text-dim);"></span>
    </div>
    <div class="budget-bar" id="budget-bar" style="display:none;"></div>
    <div class="card-body" id="chain-body" style="padding:12px;">
      <div class="status-msg">Select symbols above and click "Load Chains".</div>
    </div>
    <div class="actions-bar" id="chain-actions" style="display:none;">
      <div class="selected-info" id="contract-count-info">
        <b>0</b> trades selected
      </div>
      <div style="display:flex;gap:8px;">
        <button class="btn btn-primary" id="btn-select-best" onclick="selectBest()" disabled>Select Best</button>
        <button class="btn btn-green" id="btn-stage-contracts" onclick="stageContracts()" disabled>Stage Contracts</button>
      </div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let _scanId = null;
let _results = [];
let _selected = new Set();       // Selected symbol opportunity IDs
let _presets = {};
let _scanCodes = {};
let _chains = {};                // symbol → chain data from API
let _contractSelections = {};    // symbol → {opp_id, strike, expiration, bid, ask, delta, iv}
let _bestStrikeResults = null;   // Results from select-best endpoint
let _portfolioPreview = null;    // Phase 3: auto-select portfolio preview data

function esc(s) { if (s == null) return ''; const d = document.createElement('div'); d.textContent = String(s); return d.innerHTML; }

function showToast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (isError ? ' error' : '');
  setTimeout(() => t.className = 'toast', 3000);
}

function recBadge(rec) {
  if (!rec) return '<span class="badge badge-pending">--</span>';
  const map = {
    'strong_buy': 'badge-strong-buy',
    'buy': 'badge-buy',
    'neutral': 'badge-neutral',
    'avoid': 'badge-avoid',
  };
  const label = rec.replace('_', ' ').toUpperCase();
  return `<span class="badge ${map[rec] || 'badge-pending'}">${label}</span>`;
}

function scoreHtml(score) {
  if (score == null) return '<span class="score" style="color:var(--text-dim)">--</span>';
  const cls = score >= 7 ? 'score-high' : score >= 5 ? 'score-mid' : 'score-low';
  return `<span class="score ${cls}">${score}</span>`;
}

function riskFlags(flags) {
  if (!flags || !flags.length) return '';
  return flags.map(f => `<span class="risk-flag">${esc(f)}</span>`).join('');
}

function updateSelectedCount() {
  document.getElementById('selected-count').textContent = _selected.size;
  document.getElementById('btn-load-chains').disabled = _selected.size === 0;
  document.getElementById('btn-recommend').disabled = !_scanId;
  document.getElementById('btn-auto-select').disabled = !_scanId || _selected.size === 0;
  // Highlight selected rows
  document.querySelectorAll('#results-table tbody tr').forEach(row => {
    const id = parseInt(row.dataset.id);
    row.classList.toggle('selected', _selected.has(id));
  });
}

function toggleAll(cb) {
  if (cb.checked) {
    _results.forEach(r => { if (r.state !== 'STAGED') _selected.add(r.id); });
  } else {
    _selected.clear();
  }
  document.querySelectorAll('.row-cb').forEach(c => {
    c.checked = _selected.has(parseInt(c.closest('tr').dataset.id));
  });
  updateSelectedCount();
}

function toggleRow(id, cb) {
  if (cb.checked) _selected.add(id); else _selected.delete(id);
  updateSelectedCount();
}

// Load presets
async function loadPresets() {
  try {
    const data = await (await fetch('/api/scanner/presets')).json();
    _presets = data.presets || {};
    _scanCodes = data.scan_codes || {};

    const sel = document.getElementById('preset');
    for (const [key, p] of Object.entries(_presets)) {
      const opt = document.createElement('option');
      opt.value = key;
      opt.textContent = p.label;
      opt.title = p.description;
      sel.appendChild(opt);
    }

    const scSel = document.getElementById('scan_code');
    for (const [code, label] of Object.entries(_scanCodes)) {
      const opt = document.createElement('option');
      opt.value = code;
      opt.textContent = `${code} - ${label}`;
      scSel.appendChild(opt);
    }
  } catch(e) { console.error('Failed to load presets:', e); }
}

function applyPreset() {
  const key = document.getElementById('preset').value;
  if (!key || !_presets[key]) return;
  const d = _presets[key].defaults;
  if (d.scan_code) document.getElementById('scan_code').value = d.scan_code;
  if (d.min_price != null) document.getElementById('min_price').value = d.min_price;
  if (d.max_price != null) document.getElementById('max_price').value = d.max_price;
  if (d.num_rows != null) document.getElementById('num_rows').value = d.num_rows;
  if (d.market_cap_above != null) document.getElementById('market_cap_above').value = d.market_cap_above;
  if (d.avg_volume_above != null) document.getElementById('avg_volume_above').value = d.avg_volume_above;
  if (d.avg_opt_volume_above != null) document.getElementById('avg_opt_volume_above').value = d.avg_opt_volume_above;
}

async function runScan() {
  const btn = document.getElementById('btn-scan');
  const status = document.getElementById('scan-status');
  btn.disabled = true;
  status.innerHTML = '<span class="spinner"></span>Scanning IBKR...';

  const body = {
    preset: document.getElementById('preset').value || null,
    scan_code: document.getElementById('scan_code').value,
    min_price: parseFloat(document.getElementById('min_price').value) || 20,
    max_price: parseFloat(document.getElementById('max_price').value) || 200,
    num_rows: parseInt(document.getElementById('num_rows').value) || 50,
    market_cap_above: parseFloat(document.getElementById('market_cap_above').value) || 0,
    avg_volume_above: parseInt(document.getElementById('avg_volume_above').value) || 0,
    avg_opt_volume_above: parseInt(document.getElementById('avg_opt_volume_above').value) || 0,
  };

  try {
    const resp = await fetch('/api/scanner/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await resp.json();

    if (data.error) {
      status.textContent = '';
      showToast(data.error, true);
      btn.disabled = false;
      return;
    }

    _scanId = data.scan_id;
    _results = data.results || [];
    _selected.clear();
    _chains = {};
    _contractSelections = {};
    _bestStrikeResults = null;
    _portfolioPreview = null;
    document.getElementById('chain-card').style.display = 'none';
    document.getElementById('portfolio-card').style.display = 'none';

    renderResults(data);
    status.textContent = `${data.results_count} results in ${data.elapsed_seconds}s`;
    showToast(`Scan complete: ${data.results_count} results`);
  } catch(e) {
    status.textContent = '';
    showToast('Scan failed: ' + e.message, true);
  }
  btn.disabled = false;
}

function renderResults(data) {
  const body = document.getElementById('results-body');
  const bar = document.getElementById('actions-bar');
  const info = document.getElementById('scan-info');
  const count = document.getElementById('result-count');

  if (!data.results || data.results.length === 0) {
    body.innerHTML = '<div class="status-msg">No results. Try adjusting filters or check IBKR connection.</div>';
    bar.style.display = 'none';
    info.style.display = 'none';
    count.textContent = '';
    return;
  }

  count.textContent = `${data.results_count} results`;
  bar.style.display = 'flex';

  if (data.config) {
    info.style.display = 'flex';
    const c = data.config;
    info.innerHTML =
      `<span>Scan: <b>${esc(c.scan_code)}</b></span>` +
      (c.preset ? `<span>Preset: <b>${esc(c.preset)}</b></span>` : '') +
      `<span>Price: <b>$${c.min_price}-$${c.max_price}</b></span>` +
      (data.elapsed_seconds ? `<span>Time: <b>${data.elapsed_seconds}s</b></span>` : '') +
      (data.scan_timestamp ? `<span>At: <b>${esc(data.scan_timestamp).substring(0, 19)}</b></span>` : '');
  }

  body.innerHTML = `
    <table id="results-table">
      <thead>
        <tr>
          <th><input type="checkbox" onchange="toggleAll(this)" title="Select all"></th>
          <th>#</th>
          <th>Symbol</th>
          <th>Exchange</th>
          <th>Industry</th>
          <th>Score</th>
          <th>Recommendation</th>
          <th class="tip" data-tip="Scanner distance metric">Distance</th>
          <th class="tip" data-tip="Scanner benchmark metric">Benchmark</th>
          <th class="tip" data-tip="Scanner value/projection">Value</th>
          <th>State</th>
        </tr>
      </thead>
      <tbody>
        ${data.results.map((r, i) => {
          const ai = r.ai_recommendation;
          const score = ai ? ai.score : null;
          const rec = ai ? ai.recommendation : null;
          const flags = ai ? ai.risk_flags : null;
          const reasoning = ai ? ai.reasoning : '';
          const stateClass = r.state === 'STAGED' ? 'badge-staged' : 'badge-pending';
          return `<tr data-id="${r.id}" title="${esc(reasoning)}">
            <td><input type="checkbox" class="row-cb" onchange="toggleRow(${r.id}, this)" ${_selected.has(r.id) ? 'checked' : ''} ${r.state === 'STAGED' ? 'disabled' : ''}></td>
            <td style="color:var(--text-dim)">${i + 1}</td>
            <td style="font-weight:700;color:var(--accent)">${esc(r.symbol)}</td>
            <td>${esc(r.exchange)}</td>
            <td style="color:var(--text-dim);max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(r.industry)}">${esc(r.industry || r.category || '')}</td>
            <td>${scoreHtml(score)}</td>
            <td>${recBadge(rec)}${flags ? '<br>' + riskFlags(flags) : ''}</td>
            <td>${esc(r.distance)}</td>
            <td>${esc(r.benchmark)}</td>
            <td>${esc(r.projection)}</td>
            <td><span class="badge ${stateClass}">${esc(r.state || 'PENDING')}</span></td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;

  updateSelectedCount();
}

async function aiRecommend() {
  if (!_scanId) return;
  const btn = document.getElementById('btn-recommend');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Analyzing...';

  try {
    const body = {scan_id: _scanId};
    // Include enriched best-strike data if available
    if (_bestStrikeResults && _bestStrikeResults.length > 0) {
      body.best_strike_data = _bestStrikeResults.filter(r => r.status !== 'skipped');
    }

    const resp = await fetch('/api/scanner/recommend', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await resp.json();

    if (data.error) {
      showToast(data.error, true);
      btn.textContent = 'AI Recommend';
      btn.disabled = false;
      return;
    }

    const recMap = {};
    (data.recommendations || []).forEach(r => { recMap[r.symbol] = r; });
    _results.forEach(r => {
      if (recMap[r.symbol]) r.ai_recommendation = recMap[r.symbol];
    });

    renderResults({
      results: _results,
      results_count: _results.length,
      config: null,
      scan_timestamp: null,
    });

    showToast(`AI rated ${data.updated_count} symbols ($${data.cost_usd} cost)`);
  } catch(e) {
    showToast('AI recommendation failed: ' + e.message, true);
  }

  btn.innerHTML = 'AI Recommend';
  btn.disabled = false;
}

// ---------------------------------------------------------------
// Chain loading + rendering
// ---------------------------------------------------------------

async function loadChains() {
  if (_selected.size === 0) return;
  const btn = document.getElementById('btn-load-chains');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Loading chains...';

  const chainCard = document.getElementById('chain-card');
  const chainBody = document.getElementById('chain-body');
  chainCard.style.display = 'block';
  chainBody.innerHTML = '<div class="status-msg"><span class="spinner"></span> Fetching option chains from IBKR...</div>';

  // Get symbols for selected IDs
  const selectedSymbols = [];
  _results.forEach(r => {
    if (_selected.has(r.id)) selectedSymbols.push({id: r.id, symbol: r.symbol});
  });

  let loaded = 0;
  let errors = 0;
  _chains = {};
  _contractSelections = {};
  chainBody.innerHTML = '';

  // Fetch chains sequentially (IBKR can only handle one connection at a time on CLIENT_ID=21)
  for (const {id, symbol} of selectedSymbols) {
    const statusEl = document.getElementById('chain-status');
    statusEl.innerHTML = `<span class="spinner"></span> Loading ${symbol} (${loaded + 1}/${selectedSymbols.length})...`;

    try {
      const maxDte = parseInt(document.getElementById('max_dte').value) || 7;
      const resp = await fetch('/api/scanner/chain', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({symbol: symbol, max_dte: maxDte}),
      });
      const data = await resp.json();

      if (data.error) {
        chainBody.innerHTML += renderChainError(symbol, data.error);
        errors++;
      } else {
        _chains[symbol] = data;
        _chains[symbol]._opp_id = id;
        chainBody.innerHTML += renderChainSymbol(symbol, data, id);
      }
    } catch(e) {
      chainBody.innerHTML += renderChainError(symbol, e.message);
      errors++;
    }
    loaded++;
  }

  document.getElementById('chain-status').textContent =
    `${loaded} symbols loaded` + (errors ? `, ${errors} errors` : '');
  document.getElementById('chain-actions').style.display = 'flex';
  _bestStrikeResults = null;
  updateContractCount();

  // Enable "Select Best" if we have at least one loaded chain
  const chainsLoaded = Object.keys(_chains).length;
  document.getElementById('btn-select-best').disabled = chainsLoaded === 0;

  // Load budget bar then auto-fill quantities (sequential to avoid IBKR contention)
  await loadBudget();
  for (const {symbol} of selectedSymbols) {
    if (_chains[symbol]) await autoFillQuantity(symbol);
  }

  btn.innerHTML = 'Load Chains';
  btn.disabled = false;
}

function renderChainError(symbol, error) {
  return `<div class="chain-symbol">
    <div class="chain-symbol-header">
      <h3>${esc(symbol)}</h3>
      <span class="chain-status" style="color:var(--red)">${esc(error)}</span>
    </div>
  </div>`;
}

function renderChainSymbol(symbol, data, oppId) {
  if (!data.expirations || data.expirations.length === 0) {
    return `<div class="chain-symbol">
      <div class="chain-symbol-header" onclick="toggleChainBody('${esc(symbol)}')">
        <h3>${esc(symbol)} <span style="color:var(--text-dim);font-size:12px;font-weight:400">$${data.stock_price}</span></h3>
        <span style="color:var(--yellow);font-size:11px">No options within ${document.getElementById('max_dte').value || 7} DTE</span>
      </div>
    </div>`;
  }

  const firstExp = data.expirations[0].date;
  const recCount = data.expirations.reduce((acc, e) => acc + e.puts.filter(p => p.meets_criteria).length, 0);

  let html = `<div class="chain-symbol">
    <div class="chain-symbol-header" onclick="toggleChainBody('${esc(symbol)}')">
      <h3>${esc(symbol)} <span style="color:var(--text-dim);font-size:12px;font-weight:400">$${data.stock_price}</span></h3>
      <div style="display:flex;align-items:center;gap:16px;">
        <div class="qty-group" onclick="event.stopPropagation()">
          <label style="font-size:10px;color:var(--text-dim);text-transform:uppercase;letter-spacing:1px;">Qty</label>
          <input type="number" class="qty-input" id="qty-${esc(symbol)}" value="1" min="1" max="50"
            onchange="updateQuantityForSymbol('${esc(symbol)}')">
          <span class="qty-label" id="qty-label-${esc(symbol)}">(auto)</span>
        </div>
        <span style="font-size:11px">
          ${data.expirations.length} exp${data.expirations.length > 1 ? 's' : ''} |
          ${recCount > 0 ? `<span style="color:var(--green)">${recCount} recommended</span>` : '<span style="color:var(--text-dim)">0 recommended</span>'}
        </span>
      </div>
    </div>
    <div class="chain-symbol-body open" id="chain-body-${esc(symbol)}">`;

  // Expiration tabs
  html += `<div class="exp-tabs" id="exp-tabs-${esc(symbol)}">`;
  data.expirations.forEach((exp, i) => {
    const tabRecCount = exp.puts.filter(p => p.meets_criteria).length;
    html += `<div class="exp-tab${i === 0 ? ' active' : ''}"
      onclick="switchExpTab('${esc(symbol)}', ${i})"
      data-exp-idx="${i}">
      ${esc(exp.date)} (${exp.dte}d)${tabRecCount > 0 ? ' *' : ''}
    </div>`;
  });
  html += `</div>`;

  // Expiration panels (one per expiration)
  data.expirations.forEach((exp, i) => {
    html += `<div class="exp-panel" id="exp-panel-${esc(symbol)}-${i}" style="${i > 0 ? 'display:none' : ''}">`;
    html += renderChainTable(symbol, exp, oppId, data.stock_price);
    html += `</div>`;
  });

  html += `</div></div>`;
  return html;
}

function renderChainTable(symbol, exp, oppId, stockPrice) {
  if (!exp.puts || exp.puts.length === 0) {
    return '<div class="status-msg">No OTM puts available for this expiration.</div>';
  }

  let html = `<table class="chain-table">
    <thead><tr>
      <th></th>
      <th>Strike</th>
      <th>Bid</th>
      <th>Ask</th>
      <th>Mid</th>
      <th class="tip" data-tip="Absolute delta (probability of ITM)">Delta</th>
      <th class="tip" data-tip="Out-of-the-money percentage">OTM%</th>
      <th class="tip" data-tip="Implied volatility">IV</th>
      <th>Theta</th>
      <th>Vol</th>
      <th>OI</th>
      <th></th>
    </tr></thead><tbody>`;

  exp.puts.forEach(p => {
    const rowClass = [];
    if (p.meets_criteria) rowClass.push('recommended');
    if (p.delta == null) rowClass.push('no-greeks');

    const selKey = `${symbol}|${exp.date}|${p.strike}`;
    const isSelected = _contractSelections[symbol] &&
      _contractSelections[symbol].strike === p.strike &&
      _contractSelections[symbol].expiration === exp.date;
    if (isSelected) rowClass.push('chain-selected');

    html += `<tr class="${rowClass.join(' ')}"
      onclick="selectContract('${esc(symbol)}', ${oppId}, ${p.strike}, '${esc(exp.date)}', ${p.bid}, ${p.ask}, ${p.delta}, ${p.iv}, ${stockPrice}, ${p.otm_pct})"
      style="cursor:pointer">
      <td><input type="radio" name="chain-${esc(symbol)}" ${isSelected ? 'checked' : ''}></td>
      <td class="strike-col">$${p.strike.toFixed(1)}</td>
      <td>${p.bid > 0 ? '$' + p.bid.toFixed(2) : '--'}</td>
      <td>${p.ask > 0 ? '$' + p.ask.toFixed(2) : '--'}</td>
      <td>${p.mid > 0 ? '$' + p.mid.toFixed(2) : '--'}</td>
      <td>${p.delta != null ? p.delta.toFixed(3) : '<span style="color:var(--text-dim)">--</span>'}</td>
      <td>${(p.otm_pct * 100).toFixed(1)}%</td>
      <td>${p.iv != null ? (p.iv * 100).toFixed(1) + '%' : '--'}</td>
      <td>${p.theta != null ? p.theta.toFixed(3) : '--'}</td>
      <td style="color:var(--text-dim)">${p.volume != null ? p.volume : '--'}</td>
      <td style="color:var(--text-dim)">${p.open_interest != null ? p.open_interest.toLocaleString() : '--'}</td>
      <td>${p.meets_criteria ? '<span class="badge badge-rec">REC</span>' : ''}</td>
    </tr>`;
  });

  html += '</tbody></table>';
  return html;
}

function toggleChainBody(symbol) {
  const body = document.getElementById('chain-body-' + symbol);
  if (body) body.classList.toggle('open');
}

function switchExpTab(symbol, idx) {
  // Update tabs
  document.querySelectorAll('#exp-tabs-' + symbol + ' .exp-tab').forEach((tab, i) => {
    tab.classList.toggle('active', i === idx);
  });
  // Show/hide panels
  const data = _chains[symbol];
  if (!data) return;
  data.expirations.forEach((_, i) => {
    const panel = document.getElementById('exp-panel-' + symbol + '-' + i);
    if (panel) panel.style.display = i === idx ? '' : 'none';
  });
}

function selectContract(symbol, oppId, strike, expiration, bid, ask, delta, iv, stockPrice, otmPct) {
  const qty = parseInt(document.getElementById('qty-' + symbol)?.value) || 1;
  _contractSelections[symbol] = {
    opportunity_id: oppId,
    symbol: symbol,
    strike: strike,
    expiration: expiration,
    bid: bid || 0,
    ask: ask || 0,
    delta: delta,
    iv: iv,
    stock_price: stockPrice || 0,
    otm_pct: otmPct || 0,
    contracts: qty,
  };

  // Update radio buttons for this symbol
  document.querySelectorAll(`input[name="chain-${symbol}"]`).forEach(radio => {
    const row = radio.closest('tr');
    const rowStrike = parseFloat(row.querySelector('.strike-col').textContent.replace('$', ''));
    radio.checked = rowStrike === strike;
    row.classList.toggle('chain-selected', radio.checked);
  });

  updateContractCount();
}

function updateContractCount() {
  const selections = Object.values(_contractSelections);
  const tradeCount = selections.length;
  const totalContracts = selections.reduce((sum, s) => sum + (s.contracts || 1), 0);
  const el = document.getElementById('contract-count-info');
  if (tradeCount === 0) {
    el.innerHTML = '<b>0</b> trades selected';
  } else {
    el.innerHTML = `<b>${tradeCount}</b> trade${tradeCount > 1 ? 's' : ''} (<b>${totalContracts}</b> contracts)`;
  }
  document.getElementById('btn-stage-contracts').disabled = tradeCount === 0;
}

async function stageContracts() {
  const selections = Object.values(_contractSelections);
  if (selections.length === 0) return;

  const btn = document.getElementById('btn-stage-contracts');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Staging...';

  try {
    const resp = await fetch('/api/scanner/stage', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({selections: selections}),
    });
    const data = await resp.json();

    if (data.error) {
      showToast(data.error, true);
    } else {
      // Update local state: mark staged results
      const stagedIds = new Set(data.opportunity_ids || []);
      _results.forEach(r => {
        if (stagedIds.has(r.id)) r.state = 'STAGED';
      });
      _selected = new Set([..._selected].filter(id => !stagedIds.has(id)));
      _contractSelections = {};

      renderResults({
        results: _results,
        results_count: _results.length,
        config: null,
        scan_timestamp: null,
      });

      // Hide chain card
      document.getElementById('chain-card').style.display = 'none';

      const symbols = selections.map(s => s.symbol).join(', ');
      showToast(`Staged ${data.staged_count} contracts: ${symbols}`);
    }
  } catch(e) {
    showToast('Stage failed: ' + e.message, true);
  }

  btn.innerHTML = 'Stage Contracts';
  btn.disabled = false;
}

// ---------------------------------------------------------------
// Select Best — auto-select best strike per symbol
// ---------------------------------------------------------------

async function selectBest() {
  const btn = document.getElementById('btn-select-best');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Selecting...';

  // Build request: chains + opportunity_ids
  const chains = {};
  const opportunityIds = {};
  for (const [symbol, data] of Object.entries(_chains)) {
    chains[symbol] = data;
    opportunityIds[symbol] = data._opp_id;
  }

  try {
    const resp = await fetch('/api/scanner/select-best', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({chains: chains, opportunity_ids: opportunityIds}),
    });
    const data = await resp.json();

    if (data.error) {
      showToast(data.error, true);
      btn.innerHTML = 'Select Best';
      btn.disabled = false;
      return;
    }

    _bestStrikeResults = data.results || [];

    // Auto-select best strike in UI for each symbol
    let selected = 0;
    for (const result of _bestStrikeResults) {
      if (result.status === 'skipped') continue;

      const oppId = result.opportunity_id || opportunityIds[result.symbol];
      if (!oppId) continue;

      // Update quantity if provided
      const qtyInput = document.getElementById('qty-' + result.symbol);
      if (qtyInput && result.contracts > 0) {
        qtyInput.value = result.contracts;
        const label = document.getElementById('qty-label-' + result.symbol);
        if (label) label.textContent = '(auto)';
      }

      // Simulate selecting the contract
      selectContract(
        result.symbol, oppId, result.strike, result.expiration,
        result.bid, result.ask, result.delta, result.iv,
        result.stock_price, result.otm_pct
      );

      // Switch to the correct expiration tab
      const chainData = _chains[result.symbol];
      if (chainData && chainData.expirations) {
        const expIdx = chainData.expirations.findIndex(e => e.date === result.expiration);
        if (expIdx >= 0) switchExpTab(result.symbol, expIdx);
      }

      selected++;
    }

    // Render score badges on chain headers
    renderBestStrikeScores(_bestStrikeResults);

    const skipped = (data.symbols_skipped || []).length;
    showToast(
      `Best strikes selected: ${selected} symbols` +
      (skipped > 0 ? `, ${skipped} skipped` : '') +
      ` (${data.margins_queried} margins queried)`
    );
  } catch(e) {
    showToast('Select Best failed: ' + e.message, true);
  }

  btn.innerHTML = 'Select Best';
  btn.disabled = false;
}

function renderBestStrikeScores(results) {
  for (const r of results) {
    if (r.status === 'skipped') continue;

    // Find the chain header for this symbol
    const headerEl = document.querySelector('#chain-body-' + r.symbol)?.previousElementSibling;
    if (!headerEl) continue;

    // Remove existing badge if any
    const existing = headerEl.querySelector('.best-score-badge');
    if (existing) existing.remove();

    // Create score badge
    const pct = Math.round(r.composite_score * 100);
    const safetyPct = Math.round(r.safety_score * 100);
    const liqPct = Math.round(r.liquidity_score * 100);
    const effPct = Math.round(r.efficiency_score * 100);
    const color = pct >= 70 ? 'var(--green)' : pct >= 40 ? 'var(--yellow)' : 'var(--red)';

    const badge = document.createElement('span');
    badge.className = 'best-score-badge';
    badge.innerHTML =
      `<span class="score-pct" style="color:${color}">Score: ${pct}%</span>` +
      `<span class="score-detail">(S:${safetyPct} L:${liqPct} E:${effPct})</span>`;
    badge.title =
      `Margin: $${r.margin?.toLocaleString() || '--'} (${r.margin_source})\n` +
      `Annualized: ${r.annualized_return_pct?.toFixed(1) || '--'}%\n` +
      `Premium/Margin: ${(r.premium_margin_ratio * 100)?.toFixed(2) || '--'}%`;

    // Insert after the h3 element
    const h3 = headerEl.querySelector('h3');
    if (h3) h3.appendChild(badge);
  }
}

// ---------------------------------------------------------------
// Settings management
// ---------------------------------------------------------------

let _scannerSettings = null;
let _budgetData = null;
let _ibkrConnected = false;

function toggleSettings() {
  const body = document.getElementById('settings-body');
  const arrow = document.getElementById('settings-arrow');
  body.classList.toggle('open');
  arrow.classList.toggle('open');
}

async function loadSettings() {
  try {
    const resp = await fetch('/api/scanner/settings');
    _scannerSettings = await resp.json();
    populateSettings(_scannerSettings);
  } catch(e) { console.error('Failed to load settings:', e); }
}

function populateSettings(s) {
  if (!s) return;
  // Filters
  document.getElementById('set-delta-min').value = s.filters.delta_min;
  document.getElementById('set-delta-max').value = s.filters.delta_max;
  document.getElementById('set-delta-target').value = s.filters.delta_target;
  document.getElementById('set-min-premium').value = s.filters.min_premium;
  document.getElementById('set-min-otm-pct').value = s.filters.min_otm_pct;
  document.getElementById('set-max-dte').value = s.filters.max_dte;
  document.getElementById('set-dte-prefer-shortest').value = s.filters.dte_prefer_shortest ? 'true' : 'false';
  // Ranking
  document.getElementById('set-safety').value = s.ranking.safety;
  document.getElementById('set-liquidity').value = s.ranking.liquidity;
  document.getElementById('set-ai-score').value = s.ranking.ai_score;
  document.getElementById('set-efficiency').value = s.ranking.efficiency;
  updateWeightsSum();
  // Budget
  document.getElementById('set-margin-budget-pct').value = s.budget.margin_budget_pct;
  document.getElementById('set-max-positions').value = s.budget.max_positions;
  document.getElementById('set-max-per-sector').value = s.budget.max_per_sector;
  document.getElementById('set-price-threshold').value = s.budget.price_threshold;
  document.getElementById('set-max-contracts-expensive').value = s.budget.max_contracts_expensive;
  document.getElementById('set-max-contracts-cheap').value = s.budget.max_contracts_cheap;
}

function updateWeightsSum() {
  const safety = parseInt(document.getElementById('set-safety').value) || 0;
  const liquidity = parseInt(document.getElementById('set-liquidity').value) || 0;
  const ai = parseInt(document.getElementById('set-ai-score').value) || 0;
  const eff = parseInt(document.getElementById('set-efficiency').value) || 0;
  const sum = safety + liquidity + ai + eff;
  const el = document.getElementById('weights-sum');
  el.textContent = `Sum: ${sum} / 100`;
  el.className = 'weights-sum ' + (sum === 100 ? 'valid' : 'invalid');
  document.getElementById('btn-save-settings').disabled = sum !== 100;
}

async function saveSettings() {
  const btn = document.getElementById('btn-save-settings');
  const status = document.getElementById('settings-status');
  btn.disabled = true;
  status.innerHTML = '<span class="spinner"></span>Saving...';

  const payload = {
    filters: {
      delta_min: parseFloat(document.getElementById('set-delta-min').value),
      delta_max: parseFloat(document.getElementById('set-delta-max').value),
      delta_target: parseFloat(document.getElementById('set-delta-target').value),
      min_premium: parseFloat(document.getElementById('set-min-premium').value),
      min_otm_pct: parseFloat(document.getElementById('set-min-otm-pct').value),
      max_dte: parseInt(document.getElementById('set-max-dte').value),
      dte_prefer_shortest: document.getElementById('set-dte-prefer-shortest').value === 'true',
    },
    ranking: {
      safety: parseInt(document.getElementById('set-safety').value),
      liquidity: parseInt(document.getElementById('set-liquidity').value),
      ai_score: parseInt(document.getElementById('set-ai-score').value),
      efficiency: parseInt(document.getElementById('set-efficiency').value),
    },
    budget: {
      margin_budget_pct: parseFloat(document.getElementById('set-margin-budget-pct').value),
      max_positions: parseInt(document.getElementById('set-max-positions').value),
      max_per_sector: parseInt(document.getElementById('set-max-per-sector').value),
      price_threshold: parseFloat(document.getElementById('set-price-threshold').value),
      max_contracts_expensive: parseInt(document.getElementById('set-max-contracts-expensive').value),
      max_contracts_cheap: parseInt(document.getElementById('set-max-contracts-cheap').value),
    },
  };

  try {
    const resp = await fetch('/api/scanner/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (data.error) {
      status.textContent = '';
      showToast(data.error, true);
    } else {
      _scannerSettings = data.settings;
      status.textContent = 'Saved';
      showToast('Settings saved');
      setTimeout(() => status.textContent = '', 3000);
    }
  } catch(e) {
    status.textContent = '';
    showToast('Save failed: ' + e.message, true);
  }
  btn.disabled = false;
  updateWeightsSum(); // re-check to potentially disable button
}

// ---------------------------------------------------------------
// Budget display
// ---------------------------------------------------------------

async function loadBudget() {
  const bar = document.getElementById('budget-bar');
  bar.style.display = 'flex';
  bar.innerHTML = '<span class="spinner"></span> Loading budget...';

  try {
    const resp = await fetch('/api/scanner/budget');
    _budgetData = await resp.json();
    _ibkrConnected = _budgetData.ibkr_connected;
    renderBudgetBar(_budgetData);
  } catch(e) {
    bar.innerHTML = '<span class="budget-offline">Budget unavailable</span>';
  }
}

function renderBudgetBar(data) {
  const bar = document.getElementById('budget-bar');
  if (!data.ibkr_connected) {
    bar.innerHTML = '<span class="budget-offline">IBKR OFFLINE &mdash; budget unknown</span>';
    return;
  }

  const pct = data.margin_budget_pct * 100;
  const totalUsed = (data.current_margin || 0) + data.staged_margin;
  const usedPct = data.ceiling > 0 ? Math.min(100, (totalUsed / data.ceiling) * 100) : 0;
  const meterColor = usedPct > 80 ? 'var(--red)' : usedPct > 50 ? 'var(--yellow)' : 'var(--green)';
  const availColor = data.available <= 0 ? 'var(--red)' : 'var(--green)';

  bar.innerHTML =
    `<div class="budget-item">Headroom: <b style="color:${availColor}">$${fmtNum(data.available)}</b></div>` +
    `<div class="budget-item">Ceiling: <b>$${fmtNum(data.ceiling)}</b> (${pct.toFixed(0)}% of NLV)</div>` +
    `<div class="budget-meter"><div class="budget-meter-fill" style="width:${usedPct.toFixed(1)}%;background:${meterColor}"></div></div>` +
    `<div class="budget-item">IBKR Margin: <b>$${fmtNum(data.current_margin)}</b></div>` +
    (data.staged_count > 0 ? `<div class="budget-item warn">Staged: <b>$${fmtNum(data.staged_margin)}</b> (${data.staged_count})</div>` : '') +
    `<div class="budget-item">NLV: <b>$${fmtNum(data.nlv)}</b></div>`;
}

function fmtNum(n) {
  if (n == null) return '--';
  return n.toLocaleString('en-US', {minimumFractionDigits: 0, maximumFractionDigits: 0});
}

// ---------------------------------------------------------------
// Quantity auto-fill
// ---------------------------------------------------------------

async function autoFillQuantity(symbol) {
  const data = _chains[symbol];
  if (!data || !data.stock_price) return;

  // Use first recommended strike, or first strike with Greeks
  let strike = data.stock_price * 0.90; // fallback: 10% OTM
  for (const exp of data.expirations) {
    for (const p of exp.puts) {
      if (p.meets_criteria) { strike = p.strike; break; }
    }
    if (strike !== data.stock_price * 0.90) break;
  }

  try {
    const resp = await fetch('/api/scanner/calculate-quantity', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol, strike, stock_price: data.stock_price}),
    });
    const result = await resp.json();
    const input = document.getElementById('qty-' + symbol);
    const label = document.getElementById('qty-label-' + symbol);
    if (input) {
      input.value = result.quantity;
      if (label) label.textContent = result.source === 'position_sizer' ? '(auto)' : '(default)';
    }
  } catch(e) {
    console.error('Quantity calc failed for ' + symbol + ':', e);
  }
}

function updateQuantityForSymbol(symbol) {
  const label = document.getElementById('qty-label-' + symbol);
  if (label) label.textContent = '(manual)';
  // Update existing contract selection if one exists
  if (_contractSelections[symbol]) {
    const qty = parseInt(document.getElementById('qty-' + symbol)?.value) || 1;
    _contractSelections[symbol].contracts = qty;
  }
}

// ---------------------------------------------------------------
// Phase 3: Auto-Select Best pipeline
// ---------------------------------------------------------------

async function autoSelect() {
  if (!_scanId || _selected.size === 0) return;

  const btn = document.getElementById('btn-auto-select');
  btn.disabled = true;

  const card = document.getElementById('portfolio-card');
  card.style.display = 'block';
  const body = document.getElementById('portfolio-body');

  // Check if market is likely closed (rough heuristic)
  const now = new Date();
  const etHour = now.toLocaleString('en-US', {hour: 'numeric', hour12: false, timeZone: 'America/New_York'});
  const dayOfWeek = new Date().toLocaleString('en-US', {weekday: 'short', timeZone: 'America/New_York'});
  const isWeekend = dayOfWeek === 'Sat' || dayOfWeek === 'Sun';
  const marketClosed = isWeekend || parseInt(etHour) < 9 || parseInt(etHour) >= 16;

  const overrideMarketHours = marketClosed;
  if (marketClosed) {
    btn.innerHTML = '<span class="spinner"></span>Auto-Select (stale)...';
    body.innerHTML = '<div class="status-msg"><span class="spinner"></span> Running pipeline with stale data (market closed)...</div>';
  } else {
    btn.innerHTML = '<span class="spinner"></span>Auto-Selecting...';
    body.innerHTML = '<div class="status-msg"><span class="spinner"></span> Loading chains & scoring (1-3 min)...</div>';
  }

  // Use AbortController with 5-minute timeout
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 300000);

  try {
    const resp = await fetch('/api/scanner/auto-select', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({scan_id: _scanId, override_market_hours: overrideMarketHours}),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    const data = await resp.json();

    if (data.error) {
      body.innerHTML = `<div class="status-msg error-msg">${esc(data.error)}</div>`;
      showToast(data.error, true);
      btn.innerHTML = 'Auto-Select Best';
      btn.disabled = false;
      return;
    }

    _portfolioPreview = data;
    renderPortfolioPreview(data);

    const s = data.summary;
    showToast(
      `Portfolio: ${s.selected} selected, ${s.skipped} skipped ` +
      `(${s.elapsed_seconds}s, AI $${s.ai_cost_usd})`
    );
  } catch(e) {
    clearTimeout(timeoutId);
    if (e.name === 'AbortError') {
      body.innerHTML = '<div class="status-msg error-msg">Request timed out (5 min). Try fewer symbols.</div>';
      showToast('Auto-select timed out', true);
    } else {
      body.innerHTML = `<div class="status-msg error-msg">Failed: ${esc(e.message)}</div>`;
      showToast('Auto-select failed: ' + e.message, true);
    }
  }

  btn.innerHTML = 'Auto-Select Best';
  btn.disabled = false;
}

function renderPortfolioPreview(data) {
  const body = document.getElementById('portfolio-body');
  const summary = data.summary;
  const budget = data.budget;
  const portfolio = data.portfolio;

  // Stale data warning
  const staleEl = document.getElementById('portfolio-stale-warning');
  staleEl.style.display = data.stale_data ? 'block' : 'none';

  // Summary stats
  const summaryEl = document.getElementById('portfolio-summary');
  summaryEl.style.display = 'flex';
  summaryEl.innerHTML = '';
  const statsHtml = `<div class="summary-grid" style="width:100%;">
    <div class="summary-stat"><div class="stat-value">${summary.symbols_scanned}</div><div class="stat-label">Scanned</div></div>
    <div class="summary-stat"><div class="stat-value">${summary.chains_loaded}</div><div class="stat-label">Chains</div></div>
    <div class="summary-stat"><div class="stat-value">${summary.candidates_filtered}</div><div class="stat-label">Candidates</div></div>
    <div class="summary-stat"><div class="stat-value">${summary.ai_scored}</div><div class="stat-label">AI Scored</div></div>
    <div class="summary-stat"><div class="stat-value" style="color:var(--green)">${summary.selected}</div><div class="stat-label">Selected</div></div>
    <div class="summary-stat"><div class="stat-value" style="color:var(--text-dim)">${summary.skipped}</div><div class="stat-label">Skipped</div></div>
    <div class="summary-stat"><div class="stat-value">${summary.elapsed_seconds}s</div><div class="stat-label">Time</div></div>
  </div>`;
  summaryEl.innerHTML = statsHtml;

  // Budget bar
  const budgetEl = document.getElementById('portfolio-budget-bar');
  budgetEl.style.display = 'flex';
  const usedPct = budget.ceiling > 0 ? Math.min(100, ((budget.current_margin + budget.staged_margin + budget.used_by_selection) / budget.ceiling) * 100) : 0;
  const meterColor = usedPct > 80 ? 'var(--red)' : usedPct > 50 ? 'var(--yellow)' : 'var(--green)';
  budgetEl.innerHTML =
    `<div class="budget-item">Selected: <b style="color:var(--green)">$${fmtNum(budget.used_by_selection)}</b></div>` +
    `<div class="budget-item">Remaining: <b>$${fmtNum(budget.remaining)}</b></div>` +
    `<div class="budget-meter"><div class="budget-meter-fill" style="width:${usedPct.toFixed(1)}%;background:${meterColor}"></div></div>` +
    `<div class="budget-item">Ceiling: <b>$${fmtNum(budget.ceiling)}</b></div>` +
    `<div class="budget-item">NLV: <b>$${fmtNum(budget.nlv)}</b></div>`;

  // Selected trades table
  let html = '';
  if (portfolio.selected.length > 0) {
    html += `<table class="portfolio-table">
      <thead><tr>
        <th>#</th><th>Symbol</th><th>Strike</th><th>Exp</th><th>Delta</th><th>OTM%</th>
        <th>Bid</th><th>Margin</th><th>Qty</th><th>AI</th><th>Composite</th><th>Total$</th><th></th>
      </tr></thead><tbody>`;

    for (const t of portfolio.selected) {
      const compPct = Math.round(t.composite_score * 100);
      const compColor = compPct >= 70 ? 'var(--green)' : compPct >= 40 ? 'var(--yellow)' : 'var(--red)';
      html += `<tr class="removable" data-symbol="${esc(t.symbol)}">
        <td style="color:var(--text-dim)">${t.portfolio_rank}</td>
        <td style="font-weight:700;color:var(--accent)">${esc(t.symbol)}</td>
        <td>$${t.strike.toFixed(1)}</td>
        <td>${esc(t.expiration)} (${t.dte}d)</td>
        <td>${t.delta != null ? t.delta.toFixed(3) : '--'}</td>
        <td>${(t.otm_pct * 100).toFixed(1)}%</td>
        <td>$${t.bid.toFixed(2)}</td>
        <td>$${fmtNum(t.margin)}<br><span style="font-size:10px;color:var(--text-dim)">${esc(t.margin_source)}</span></td>
        <td>${t.contracts}</td>
        <td>${t.ai_score != null ? scoreHtml(t.ai_score) : '--'} ${t.ai_recommendation ? recBadge(t.ai_recommendation) : ''}</td>
        <td><span style="font-weight:700;color:${compColor}">${compPct}%</span></td>
        <td>$${fmtNum(t.total_margin)}</td>
        <td><button class="btn-remove" onclick="removeFromPortfolio('${esc(t.symbol)}')">Remove</button></td>
      </tr>`;
      if (t.ai_risk_flags && t.ai_risk_flags.length > 0) {
        html += `<tr><td></td><td colspan="12" style="padding:0 10px 6px">${riskFlags(t.ai_risk_flags)}</td></tr>`;
      }
    }
    html += '</tbody></table>';
  } else {
    html += '<div class="status-msg">No trades selected for portfolio.</div>';
  }

  // Warnings
  if (portfolio.warnings && portfolio.warnings.length > 0) {
    html += '<div style="padding:8px 16px;color:var(--yellow);font-size:12px;">';
    for (const w of portfolio.warnings) {
      html += `⚠ ${esc(w)}<br>`;
    }
    html += '</div>';
  }

  // Skipped trades (collapsed)
  if (portfolio.skipped.length > 0) {
    html += `<div class="skipped-toggle" onclick="toggleSkipped()">
      <span class="arrow" id="skipped-arrow">&#9654;</span>
      ${portfolio.skipped.length} skipped trades
    </div>
    <div class="skipped-body" id="skipped-body">
      <table><thead><tr>
        <th>Symbol</th><th>Composite</th><th>AI</th><th>Margin</th><th>Skip Reason</th>
      </tr></thead><tbody>`;
    for (const t of portfolio.skipped) {
      const compPct = Math.round(t.composite_score * 100);
      html += `<tr>
        <td style="font-weight:700">${esc(t.symbol)}</td>
        <td>${compPct}%</td>
        <td>${t.ai_score != null ? t.ai_score : '--'}</td>
        <td>$${fmtNum(t.margin)}</td>
        <td><span style="color:var(--yellow)">${esc(t.skip_reason || 'unknown')}</span></td>
      </tr>`;
    }
    html += '</tbody></table></div>';
  }

  body.innerHTML = html;

  // Actions bar
  const actions = document.getElementById('portfolio-actions');
  actions.style.display = portfolio.selected.length > 0 ? 'flex' : 'none';
  updatePortfolioCountInfo();

  document.getElementById('portfolio-status').textContent =
    `${summary.selected} trades | ${summary.elapsed_seconds}s`;
}

function toggleSkipped() {
  const body = document.getElementById('skipped-body');
  const arrow = document.getElementById('skipped-arrow');
  if (body) {
    body.classList.toggle('open');
    if (arrow) arrow.style.transform = body.classList.contains('open') ? 'rotate(90deg)' : '';
  }
}

function updatePortfolioCountInfo() {
  if (!_portfolioPreview) return;
  const selected = _portfolioPreview.portfolio.selected;
  const totalContracts = selected.reduce((s, t) => s + t.contracts, 0);
  const totalMargin = selected.reduce((s, t) => s + t.total_margin, 0);
  const el = document.getElementById('portfolio-count-info');
  el.innerHTML = `<b>${selected.length}</b> trade${selected.length !== 1 ? 's' : ''} (<b>${totalContracts}</b> contracts) | <b>$${fmtNum(totalMargin)}</b> margin`;
}

function removeFromPortfolio(symbol) {
  if (!_portfolioPreview) return;
  const portfolio = _portfolioPreview.portfolio;

  // Move from selected to skipped
  const idx = portfolio.selected.findIndex(t => t.symbol === symbol);
  if (idx === -1) return;

  const removed = portfolio.selected.splice(idx, 1)[0];
  removed.selected = false;
  removed.skip_reason = 'removed_by_user';
  portfolio.skipped.push(removed);

  // Update budget
  _portfolioPreview.budget.used_by_selection -= removed.total_margin;
  _portfolioPreview.budget.remaining += removed.total_margin;

  // Update summary
  _portfolioPreview.summary.selected = portfolio.selected.length;
  _portfolioPreview.summary.skipped = portfolio.skipped.length;

  // Re-rank remaining
  portfolio.selected.forEach((t, i) => { t.portfolio_rank = i + 1; });

  renderPortfolioPreview(_portfolioPreview);
  showToast(`Removed ${symbol} from portfolio`);
}

async function stagePortfolio() {
  if (!_portfolioPreview) return;
  const selected = _portfolioPreview.portfolio.selected;
  if (selected.length === 0) return;

  const btn = document.getElementById('btn-stage-portfolio');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>Staging...';

  // Build StageContractSelection array with Phase 3 fields
  const selections = selected.map(t => ({
    opportunity_id: t.opportunity_id,
    symbol: t.symbol,
    strike: t.strike,
    expiration: t.expiration,
    bid: t.bid,
    ask: t.ask,
    delta: t.delta,
    iv: t.iv,
    stock_price: t.stock_price,
    otm_pct: t.otm_pct,
    contracts: t.contracts,
    margin_actual: t.margin,
    margin_source: t.margin_source,
    composite_score: t.composite_score,
    portfolio_rank: t.portfolio_rank,
    config_snapshot: _portfolioPreview.config_snapshot,
  }));

  try {
    const resp = await fetch('/api/scanner/stage', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({selections: selections}),
    });
    const data = await resp.json();

    if (data.error) {
      showToast(data.error, true);
    } else {
      // Update local state: mark staged results
      const stagedIds = new Set(data.opportunity_ids || []);
      _results.forEach(r => {
        if (stagedIds.has(r.id)) r.state = 'STAGED';
      });
      _selected = new Set([..._selected].filter(id => !stagedIds.has(id)));

      renderResults({
        results: _results,
        results_count: _results.length,
        config: null,
        scan_timestamp: null,
      });

      // Hide portfolio card
      document.getElementById('portfolio-card').style.display = 'none';
      _portfolioPreview = null;

      const symbols = selected.map(s => s.symbol).join(', ');
      showToast(`Staged ${data.staged_count} trades: ${symbols}`);
    }
  } catch(e) {
    showToast('Stage failed: ' + e.message, true);
  }

  btn.innerHTML = 'Stage All';
  btn.disabled = false;
}

// Load last results from DB on page load
async function loadLastResults() {
  try {
    const resp = await fetch('/api/scanner/results');
    const data = await resp.json();
    if (data.scan_id && data.results && data.results.length > 0) {
      _scanId = data.scan_id;
      _results = data.results;
      renderResults(data);
      document.getElementById('result-count').textContent = `${data.results_count} results (from DB)`;
    }
  } catch(e) { console.error('Failed to load last results:', e); }
}

// Init
loadPresets();
loadLastResults();
loadSettings();
</script>
</body>
</html>"""
