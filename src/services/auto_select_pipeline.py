"""Reusable auto-select pipeline for scanner → select → stage workflow.

Extracted from scanner_api.py so both the FastAPI endpoints and the
daemon's MARKET_OPEN hook can share the same business logic.

Three entry points:
- run_scan_and_persist(): Run IBKR scanner and persist results to DB.
- run_auto_select_pipeline(): Full pipeline: chains → scores → AI → portfolio.
- stage_selected_candidates(): Stage PortfolioCandidates into ScanOpportunity rows.

Also re-exports two helpers used by scanner_api.py endpoints:
- build_symbol_data(): Build symbol data list from ScanOpportunity objects.
- get_ai_recommendations(): Call Claude for AI recommendations.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import ScanOpportunity, ScanResult
from src.services.earnings_service import EarningsInfo, get_cached_earnings
from src.services.ibkr_scanner import (
    SCANNER_PRESETS,
    IBKRScannerService,
    ScannerConfig,
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class AutoSelectResult:
    """Result of a full auto-select pipeline run."""

    success: bool
    error: str | None = None
    scan_id: int | None = None
    selected: list = field(default_factory=list)  # list[PortfolioCandidate]
    skipped: list = field(default_factory=list)  # list[PortfolioCandidate]
    warnings: list[str] = field(default_factory=list)
    opp_id_map: dict[str, int] = field(default_factory=dict)
    nlv: float | None = None
    available_budget: float = 0.0
    used_margin: float = 0.0
    config_snapshot: dict = field(default_factory=dict)
    earnings_map: dict = field(default_factory=dict)
    # Metrics
    symbols_scanned: int = 0
    chains_loaded: int = 0
    candidates_filtered: int = 0
    best_strikes_found: int = 0
    ai_scored: int = 0
    ai_cost_usd: float = 0.0
    elapsed_seconds: float = 0.0
    stale_data: bool = False


# ---------------------------------------------------------------------------
# Helper functions (shared with scanner_api.py)
# ---------------------------------------------------------------------------


def build_symbol_data(opps: list) -> list[dict]:
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
                notes = (
                    json.loads(opp.entry_notes)
                    if isinstance(opp.entry_notes, str)
                    else opp.entry_notes
                )
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


def get_ai_recommendations(
    symbol_data: list[dict],
    best_strike_data: list[dict] | None,
    scan_type: str,
    db_session: Session,
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
        "If earnings_date and earnings_in_dte fields are provided for a symbol, "
        "factor this heavily into your risk assessment. Stocks with earnings in "
        "the DTE window carry significant gap risk. Score them lower unless the "
        "OTM cushion is very large (>25%).\n\n"
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


# ---------------------------------------------------------------------------
# Pipeline functions
# ---------------------------------------------------------------------------


def run_scan_and_persist(
    preset: str,
    db: Session,
    scan_code: str | None = None,
) -> tuple[int, list]:
    """Run IBKR scanner with a preset and persist results to DB.

    Args:
        preset: Scanner preset name (e.g. "naked-put").
        db: SQLAlchemy session.
        scan_code: Override scan code (uses preset default if None).

    Returns:
        Tuple of (scan_id, list of ScanOpportunity objects).

    Raises:
        RuntimeError: If scanner fails or preset is unknown.
    """
    if preset not in SCANNER_PRESETS:
        raise RuntimeError(f"Unknown scanner preset: {preset}")

    preset_data = SCANNER_PRESETS[preset]
    config = ScannerConfig(
        scan_code=scan_code or preset_data.get("scan_code", "HIGH_OPT_IMP_VOLAT"),
        instrument=preset_data.get("instrument", "STK"),
        location=preset_data.get("location", "STK.US.MAJOR"),
        min_price=preset_data.get("min_price", 20.0),
        max_price=preset_data.get("max_price", 200.0),
        num_rows=preset_data.get("num_rows", 50),
        market_cap_above=preset_data.get("market_cap_above", 0),
        market_cap_below=preset_data.get("market_cap_below", 0),
        avg_volume_above=preset_data.get("avg_volume_above", 0),
        avg_opt_volume_above=preset_data.get("avg_opt_volume_above", 0),
        stock_type=preset_data.get("stock_type", ""),
    )

    service = IBKRScannerService()
    start_time = time.time()

    try:
        results = service.run_scan(config)
    except Exception as e:
        raise RuntimeError(f"IBKR scanner failed: {e}") from e

    elapsed = time.time() - start_time

    # Persist scan result + opportunities
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
            "preset": preset,
        },
        total_candidates=len(results),
        execution_time_seconds=round(elapsed, 2),
    )
    db.add(scan)
    db.flush()

    opportunities = []
    for r in results:
        opp = ScanOpportunity(
            scan_id=scan.id,
            symbol=r.symbol,
            strike=0,
            expiration=date.today(),
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

    logger.info(
        f"Scan persisted: scan_id={scan.id}, {len(opportunities)} opportunities, "
        f"{elapsed:.1f}s elapsed"
    )

    return scan.id, opportunities


def run_auto_select_pipeline(
    scan_id: int,
    db: Session,
    override_market_hours: bool = False,
) -> AutoSelectResult:
    """Run the full auto-select pipeline: chains → scores → AI → portfolio.

    Steps:
    1. Verify IBKR connection, get NLV + current margin
    2. Get VIX for position sizing
    3. Calculate available margin budget
    4. Fetch PENDING opportunities for the scan
    5. Load option chains batch from IBKR
    6. Filter candidates + batch margin queries
    7. Select best strike per symbol (3-weight)
    8. Call Claude for AI recommendations
    9. Build PortfolioCandidates with 4-weight composite scores
    10. Greedy portfolio selection within budget
    11. Build config snapshot

    Args:
        scan_id: ID of the ScanResult to process.
        db: SQLAlchemy session.
        override_market_hours: If True, allow pipeline to run with stale data.

    Returns:
        AutoSelectResult with selected/skipped candidates and metrics.
    """
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

    # Check cost cap
    cost_tracker = CostTracker(db, daily_cap_usd=10.0)
    if not cost_tracker.can_call():
        return AutoSelectResult(
            success=False,
            error="Daily Claude API cost cap exceeded.",
        )

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
        logger.warning(f"Auto-select pipeline: IBKR account summary failed — {e}")

    if not ibkr_connected and not override_market_hours:
        return AutoSelectResult(
            success=False,
            error="IBKR offline. Cannot auto-select without live account data.",
        )

    # Step 2: Get VIX for position sizing
    try:
        service = IBKRScannerService()
        vix = service.get_vix()
    except Exception as e:
        logger.warning(f"Auto-select pipeline: VIX fetch failed — {e}")

    # Step 3: Calculate available budget
    margin_budget_pct = settings.budget.margin_budget_pct

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
        logger.warning(f"Auto-select pipeline: staged margin query failed — {e}")

    ceiling = round(nlv * margin_budget_pct, 2) if nlv else 0
    available_budget = max(0, ceiling - current_margin - staged_margin)

    if available_budget <= 0 and not override_market_hours:
        return AutoSelectResult(
            success=False,
            error="No available margin budget.",
            nlv=nlv,
            available_budget=0,
        )

    # Step 4: Fetch PENDING opportunities for this scan
    opps = (
        db.query(ScanOpportunity)
        .filter(
            ScanOpportunity.scan_id == scan_id,
            ScanOpportunity.state == "PENDING",
        )
        .order_by(ScanOpportunity.id.asc())
        .all()
    )

    if not opps:
        return AutoSelectResult(
            success=False,
            error="No PENDING opportunities for this scan.",
            scan_id=scan_id,
        )

    symbols = [opp.symbol for opp in opps]
    opp_id_map = {opp.symbol: opp.id for opp in opps}

    logger.info(
        f"Auto-select pipeline: {len(symbols)} symbols, "
        f"budget=${available_budget:,.0f} "
        f"(NLV=${nlv or 0:,.0f}, ceiling=${ceiling:,.0f})"
    )

    # Step 5: Load chains batch
    max_dte = settings.filters.max_dte
    try:
        service = IBKRScannerService()
        all_chains = service.get_option_chains_batch(symbols, max_dte=max_dte)
    except Exception as e:
        logger.error(f"Auto-select pipeline: batch chain load failed — {e}")
        return AutoSelectResult(
            success=False,
            error=f"Chain loading failed: {e}",
            scan_id=scan_id,
            symbols_scanned=len(symbols),
        )

    chains_loaded = sum(
        1 for v in all_chains.values()
        if v.get("stock_price") and v.get("expirations")
    )

    # Step 5a: Fetch earnings dates for all symbols (always, regardless of
    # earnings.enabled — that toggle only controls filter adjustment)
    earnings_map: dict[str, EarningsInfo] = {}
    for symbol in symbols:
        try:
            chain = all_chains.get(symbol, {})
            exps = chain.get("expirations", [])
            earliest_exp = None
            if exps:
                exp_str = exps[0].get("date") if isinstance(exps[0], dict) else str(exps[0])
                earliest_exp = date.fromisoformat(exp_str)
            info = get_cached_earnings(symbol, option_expiration=earliest_exp)
            earnings_map[symbol] = info
        except Exception as e:
            logger.debug(f"Earnings fetch failed for {symbol}: {e}")

    # Step 6: Filter candidates + batch margin queries
    selector = AutoSelector(settings)
    all_candidates: dict[str, list] = {}
    margin_queries: list[dict] = []

    earnings_warnings: list[str] = []

    for symbol, chain_data in all_chains.items():
        # Check for earnings within DTE
        earnings_info = earnings_map.get(symbol)
        original_filters = None

        if earnings_info and earnings_info.earnings_in_dte:
            if settings.earnings.enabled:
                # Adjust filters: add additional_otm_pct to base min_otm_pct
                original_filters = selector.settings.filters.model_copy()
                base_otm = selector.settings.filters.min_otm_pct
                adjusted_otm = base_otm + settings.earnings.additional_otm_pct
                selector.settings.filters.min_otm_pct = adjusted_otm
                logger.info(
                    f"{symbol}: earnings {earnings_info.earnings_date} in DTE — "
                    f"OTM adjusted {base_otm:.0%} + "
                    f"{settings.earnings.additional_otm_pct:.0%} = "
                    f"{adjusted_otm:.0%}"
                )
            else:
                # Warn-only mode: log and collect warnings for toast
                logger.warning(
                    f"{symbol}: earnings {earnings_info.earnings_date} in DTE "
                    f"({earnings_info.days_to_earnings}d away) — "
                    f"no filter adjustment (earnings detection disabled)"
                )
                earnings_warnings.append(
                    f"{symbol}: earnings {earnings_info.earnings_date} "
                    f"({earnings_info.days_to_earnings}d) in DTE"
                )

        candidates = selector.filter_candidates(chain_data)
        all_candidates[symbol] = candidates

        # Restore original filters
        if original_filters is not None:
            selector.settings.filters = original_filters

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
            logger.warning(f"Auto-select pipeline: margin query failed — {e}")

    # Step 7: Select best strike per symbol (3-weight)
    best_results = selector.select_best_per_symbol(all_candidates, margins)

    # Enrich with sector and contracts
    for result in best_results:
        if result.status == "skipped":
            continue
        result.sector = get_sector(result.symbol)
        if nlv and nlv > 0:
            budget_cfg = settings.budget
            price_based_max = (
                budget_cfg.max_contracts_expensive
                if result.stock_price > budget_cfg.price_threshold
                else budget_cfg.max_contracts_cheap
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
    symbol_data = build_symbol_data(opps)
    active_best = [r for r in best_results if r.status != "skipped"]
    best_strike_dicts = []
    for r in active_best:
        entry = {
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
        }
        # Add earnings context if available
        ei = earnings_map.get(r.symbol)
        if ei and ei.earnings_date:
            entry["earnings_date"] = str(ei.earnings_date)
            entry["days_to_earnings"] = ei.days_to_earnings
            entry["earnings_in_dte"] = ei.earnings_in_dte
            entry["earnings_timing"] = ei.earnings_timing
        best_strike_dicts.append(entry)

    scan_type = (
        opps[0].scan.config_used.get("scan_code", "?")
        if opps[0].scan and opps[0].scan.config_used
        else "?"
    )

    ai_map: dict[str, dict] = {}
    ai_cost = 0.0
    ai_result = get_ai_recommendations(
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
        logger.warning(
            f"Auto-select pipeline: AI recommendations failed — "
            f"{ai_result.get('error')}"
        )

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

    # Append earnings warnings (warn-only mode)
    warnings.extend(earnings_warnings)

    # Step 11: Build config snapshot
    config_snapshot = {
        "auto_select_config": settings.model_dump(),
        "vix": vix,
        "nlv": nlv,
        "available_budget": available_budget,
        "scan_type": scan_type,
        "timestamp": datetime.utcnow().isoformat(),
    }

    elapsed = time.time() - t0
    used_by_selection = sum(s.total_margin for s in selected)

    logger.info(
        f"Auto-select pipeline complete: {len(selected)} selected, "
        f"{len(skipped)} skipped, ${used_by_selection:,.0f} margin used, "
        f"{elapsed:.1f}s elapsed, AI cost=${ai_cost:.4f}"
    )

    return AutoSelectResult(
        success=True,
        scan_id=scan_id,
        selected=selected,
        skipped=skipped,
        warnings=warnings,
        opp_id_map=opp_id_map,
        nlv=nlv,
        available_budget=available_budget,
        used_margin=used_by_selection,
        config_snapshot=config_snapshot,
        earnings_map=earnings_map,
        symbols_scanned=len(symbols),
        chains_loaded=chains_loaded,
        candidates_filtered=total_candidates,
        best_strikes_found=len(active_best),
        ai_scored=len(ai_map),
        ai_cost_usd=ai_cost,
        elapsed_seconds=round(elapsed, 1),
        stale_data=override_market_hours,
    )


def stage_selected_candidates(
    selected: list,
    opp_id_map: dict[str, int],
    config_snapshot: dict,
    db: Session,
    earnings_map: dict[str, EarningsInfo] | None = None,
) -> int:
    """Stage selected PortfolioCandidates as ScanOpportunity rows.

    Builds StageContractSelection-compatible data from each PortfolioCandidate
    and updates the corresponding ScanOpportunity with real contract details.

    Args:
        selected: List of PortfolioCandidate objects marked as selected.
        opp_id_map: Mapping of symbol → ScanOpportunity.id.
        config_snapshot: Config snapshot dict for enrichment_snapshot.
        db: SQLAlchemy session.
        earnings_map: Optional mapping of symbol → EarningsInfo for
            populating earnings fields on the ScanOpportunity.

    Returns:
        Number of candidates successfully staged.
    """
    staged_count = 0

    for pc in selected:
        opp_id = opp_id_map.get(pc.symbol)
        if not opp_id:
            logger.warning(f"Stage: no opportunity_id for {pc.symbol}")
            continue

        opp = db.query(ScanOpportunity).get(opp_id)
        if not opp:
            logger.warning(f"Stage: opportunity {opp_id} not found")
            continue

        # Parse expiration
        try:
            exp_parts = pc.expiration.split("-")
            exp_date = date(int(exp_parts[0]), int(exp_parts[1]), int(exp_parts[2]))
        except (ValueError, IndexError):
            logger.warning(f"Stage: invalid expiration '{pc.expiration}'")
            continue

        # Update with real contract data
        opp.strike = pc.strike
        opp.expiration = exp_date
        opp.bid = pc.bid
        opp.ask = pc.ask
        premium = round(pc.bid, 2) if pc.bid else 0.0
        opp.premium = premium
        opp.delta = pc.delta
        opp.iv = pc.iv
        opp.stock_price = pc.stock_price
        opp.otm_pct = pc.otm_pct
        opp.dte = (exp_date - date.today()).days
        opp.staged_contracts = pc.contracts
        opp.staged_limit_price = premium
        opp.state = "STAGED"
        opp.staged_at = datetime.utcnow()

        # Margin data
        if pc.margin and pc.margin > 0:
            opp.margin_required = round(pc.margin, 2)
            opp.staged_margin = round(pc.margin * pc.contracts, 2)
            opp.staged_margin_source = pc.margin_source or "ibkr_whatif"
        elif pc.stock_price and pc.stock_price > 0 and pc.strike > 0:
            otm_amount = max(0, pc.stock_price - pc.strike)
            margin_est = (0.20 * pc.stock_price - otm_amount + (premium or 0)) * 100
            min_margin = 0.10 * pc.stock_price * 100
            opp.margin_required = round(max(margin_est, min_margin), 2)
            opp.staged_margin = opp.margin_required
            opp.staged_margin_source = "estimated"

        if opp.margin_required and opp.margin_required > 0 and premium:
            opp.margin_efficiency = round((premium * 100) / opp.margin_required, 4)

        # Portfolio rank and config snapshot
        if pc.portfolio_rank:
            opp.portfolio_rank = pc.portfolio_rank
        opp.enrichment_snapshot = config_snapshot

        # Earnings data
        if earnings_map:
            ei = earnings_map.get(pc.symbol)
            if ei and ei.earnings_date:
                opp.earnings_date = ei.earnings_date
                opp.days_to_earnings = ei.days_to_earnings
                opp.earnings_in_dte = ei.earnings_in_dte
                opp.earnings_timing = ei.earnings_timing

        staged_count += 1

    db.commit()

    logger.info(f"Auto-select pipeline: staged {staged_count} candidates")
    return staged_count
