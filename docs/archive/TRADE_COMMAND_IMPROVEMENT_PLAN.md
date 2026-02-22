# Trade Command Improvement Plan
## Enhancing the Autonomous Trading Workflow

**Document Version:** 1.0  
**Created:** January 27, 2026  
**Purpose:** Implementation plan for improving the `trade` CLI command based on business analyst, trader, and end-user review  
**Status:** Ready for implementation  
**Estimated Effort:** 3-4 weeks (can be parallelized with existing Phase 3 work)

---

## Executive Summary

This plan addresses critical gaps identified in the current `trade` command workflow:

| Gap Category | Impact | Priority |
|--------------|--------|----------|
| **Missing Opportunity Lifecycle** | Cannot learn from rejected/skipped trades | 🔴 Critical |
| **No Market Hours Awareness** | Orders may fail or execute at wrong prices | 🔴 Critical |
| **Margin Efficiency Not Calculated** | Core trading metric missing | 🔴 Critical |
| **Poor User Experience** | Tedious approval process, no batch operations | 🟡 High |
| **Hidden Risk Rejections** | User cannot see why opportunities were filtered | 🟡 High |
| **No Session Recovery** | Interrupted executions leave inconsistent state | 🟡 High |
| **Missing Trade Context Capture** | Learning engine lacks decision context | 🟠 Medium |

---

## Phase Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PHASE 2.5: TRADE COMMAND ENHANCEMENTS                                  │
│  (Insert between Phase 2 and Phase 3)                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌──────────┐ │
│  │  2.5A       │───▶│  2.5B       │───▶│  2.5C       │───▶│  2.5D    │ │
│  │  Data Model │    │  Market     │    │  User       │    │  Context │ │
│  │  & State    │    │  Awareness  │    │  Experience │    │  Capture │ │
│  │  (5 days)   │    │  (4 days)   │    │  (4 days)   │    │  (3 days)│ │
│  └─────────────┘    └─────────────┘    └─────────────┘    └──────────┘ │
│                                                                         │
│  Total: 16 days (3-4 weeks with testing and buffer)                    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 2.5A: Data Model & State Machine (5 days)

### Objective
Implement explicit opportunity lifecycle tracking with state transitions and full audit trail.

### Why This Matters
- Current system treats opportunities as disposable after execution
- No visibility into rejected opportunities or rejection reasons
- Learning engine cannot analyze "missed" opportunities
- No rollback capability for partial failures

### Deliverables

#### 2.5A.1: Opportunity State Machine

**New File:** `src/data/opportunity_state.py`

```python
from enum import Enum, auto
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

class OpportunityState(Enum):
    """Explicit states for opportunity lifecycle."""
    PENDING = auto()        # Created, awaiting enrichment
    ENRICHED = auto()       # Live data fetched from IBKR
    VALIDATED = auto()      # Passed strategy criteria
    RISK_BLOCKED = auto()   # Failed risk checks (with reasons)
    OFFERED = auto()        # Presented to user
    APPROVED = auto()       # User said yes
    REJECTED = auto()       # User said no
    SKIPPED = auto()        # User skipped (no decision)
    EXECUTING = auto()      # Order in progress
    EXECUTED = auto()       # Order filled
    FAILED = auto()         # Order rejected/error
    EXPIRED = auto()        # TTL exceeded without action

@dataclass
class StateTransition:
    """Record of a state change."""
    from_state: OpportunityState
    to_state: OpportunityState
    timestamp: datetime
    reason: str
    actor: str  # "system", "user", "risk_governor", "ibkr"
    metadata: dict = field(default_factory=dict)
```

#### 2.5A.2: Database Schema Updates

**Migration:** `migrations/003_opportunity_lifecycle.sql`

```sql
-- Add lifecycle tracking to opportunities table
ALTER TABLE opportunities ADD COLUMN state TEXT DEFAULT 'PENDING';
ALTER TABLE opportunities ADD COLUMN state_history JSON DEFAULT '[]';
ALTER TABLE opportunities ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE opportunities ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE opportunities ADD COLUMN expires_at TIMESTAMP;

-- Snapshot data at different stages
ALTER TABLE opportunities ADD COLUMN enrichment_snapshot JSON;
ALTER TABLE opportunities ADD COLUMN validation_snapshot JSON;
ALTER TABLE opportunities ADD COLUMN execution_snapshot JSON;

-- Rejection tracking (critical for learning)
ALTER TABLE opportunities ADD COLUMN rejection_reasons JSON DEFAULT '[]';
ALTER TABLE opportunities ADD COLUMN risk_check_results JSON;

-- User decision tracking
ALTER TABLE opportunities ADD COLUMN user_decision TEXT;  -- approved/rejected/skipped
ALTER TABLE opportunities ADD COLUMN user_decision_at TIMESTAMP;
ALTER TABLE opportunities ADD COLUMN user_notes TEXT;

-- Execution tracking
ALTER TABLE opportunities ADD COLUMN execution_attempts INTEGER DEFAULT 0;
ALTER TABLE opportunities ADD COLUMN last_error TEXT;

-- Idempotency key (prevent duplicates)
ALTER TABLE opportunities ADD COLUMN opportunity_hash TEXT UNIQUE;

-- Indexes for common queries
CREATE INDEX idx_opp_state ON opportunities(state);
CREATE INDEX idx_opp_created ON opportunities(created_at);
CREATE INDEX idx_opp_hash ON opportunities(opportunity_hash);
```

#### 2.5A.3: Opportunity Lifecycle Manager

**New File:** `src/execution/opportunity_lifecycle.py`

```python
class OpportunityLifecycleManager:
    """Manages opportunity state transitions with full audit trail."""
    
    def transition(
        self, 
        opportunity_id: int, 
        new_state: OpportunityState,
        reason: str,
        actor: str = "system",
        metadata: dict = None
    ) -> bool:
        """
        Transition opportunity to new state with validation.
        
        - Validates transition is allowed
        - Records state history
        - Updates timestamps
        - Logs for audit
        """
        pass
    
    def capture_snapshot(
        self,
        opportunity_id: int,
        snapshot_type: str,  # "enrichment", "validation", "execution"
        data: dict
    ) -> None:
        """Capture point-in-time data for later analysis."""
        pass
    
    def record_rejection(
        self,
        opportunity_id: int,
        check_name: str,
        current_value: float,
        limit_value: float,
        message: str
    ) -> None:
        """Record why an opportunity was rejected (for learning)."""
        pass
    
    def get_lifecycle_report(
        self,
        opportunity_id: int
    ) -> dict:
        """Get complete lifecycle history for an opportunity."""
        pass
```

#### 2.5A.4: Idempotency & Duplicate Detection

**Update:** `src/data/opportunity_repository.py`

```python
def calculate_opportunity_hash(
    symbol: str,
    strike: float,
    expiration: str,
    option_type: str,
    date_created: str
) -> str:
    """
    Generate unique hash for opportunity deduplication.
    
    Hash includes date_created (not just date) to allow
    re-entry of same option on different days.
    """
    import hashlib
    key = f"{symbol}:{strike}:{expiration}:{option_type}:{date_created}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]

def find_duplicate(self, opportunity: TradeOpportunity) -> Optional[int]:
    """Check if opportunity already exists (any state)."""
    pass

def merge_duplicate(
    self, 
    existing_id: int, 
    new_data: dict,
    source: str
) -> None:
    """
    Merge new data into existing opportunity.
    
    Use case: Manual trade also found by Barchart scan.
    Keep manual notes, update with fresher pricing.
    """
    pass
```

### Success Criteria

| Criterion | Validation Method |
|-----------|-------------------|
| All opportunities have explicit state | Query: `SELECT COUNT(*) WHERE state IS NULL` = 0 |
| State transitions logged | Verify `state_history` JSON populated |
| Duplicates detected | Create same opportunity twice, verify hash collision |
| Snapshots captured | Check `enrichment_snapshot` after enrichment |
| Rejections recorded | Trigger risk rejection, verify `rejection_reasons` populated |

### Test Coverage Requirements

- Unit tests for state machine transitions (valid and invalid)
- Unit tests for hash generation consistency
- Integration test for full lifecycle: PENDING → EXECUTED
- Integration test for rejection flow: PENDING → RISK_BLOCKED
- Edge case: Opportunity expires mid-workflow

---

## Phase 2.5B: Market Hours Awareness (4 days)

### Objective
Implement market calendar awareness to handle pre-market, market hours, and after-hours correctly.

### Why This Matters
- Orders placed outside market hours may use stale prices
- Pre-market underlying movement can invalidate manual entries
- Order types should differ based on timing (GTC vs DAY)
- User needs visibility into when orders will execute

### Deliverables

#### 2.5B.1: Market Calendar Service

**New File:** `src/services/market_calendar.py`

```python
from enum import Enum
from datetime import datetime, time
from zoneinfo import ZoneInfo

class MarketSession(Enum):
    PRE_MARKET = "pre_market"      # 4:00 AM - 9:30 AM ET
    REGULAR = "regular"            # 9:30 AM - 4:00 PM ET
    AFTER_HOURS = "after_hours"    # 4:00 PM - 8:00 PM ET
    CLOSED = "closed"              # 8:00 PM - 4:00 AM ET
    HOLIDAY = "holiday"            # Market closed all day
    WEEKEND = "weekend"            # Saturday/Sunday

class MarketCalendar:
    """US equity market hours and holiday awareness."""
    
    TZ = ZoneInfo("America/New_York")
    
    # 2026 US market holidays (update annually)
    HOLIDAYS_2026 = [
        "2026-01-01",  # New Year's Day
        "2026-01-19",  # MLK Day
        "2026-02-16",  # Presidents Day
        "2026-04-03",  # Good Friday
        "2026-05-25",  # Memorial Day
        "2026-07-03",  # Independence Day (observed)
        "2026-09-07",  # Labor Day
        "2026-11-26",  # Thanksgiving
        "2026-12-25",  # Christmas
    ]
    
    def get_current_session(self) -> MarketSession:
        """Determine current market session."""
        pass
    
    def is_market_open(self) -> bool:
        """Check if regular session is active."""
        pass
    
    def next_market_open(self) -> datetime:
        """Get datetime of next regular session open."""
        pass
    
    def time_until_open(self) -> timedelta:
        """Get time remaining until market opens."""
        pass
    
    def is_trading_day(self, date: datetime = None) -> bool:
        """Check if given date is a trading day."""
        pass
```

#### 2.5B.2: Order Timing Configuration

**Update:** `.env`

```bash
# Order Timing Configuration
ORDER_TIMING_MODE=MARKET_OPEN  # IMMEDIATE | MARKET_OPEN | MANUAL_TRIGGER

# Pre-market deviation threshold
# If underlying moved more than X% since opportunity creation,
# require user re-confirmation before execution
MAX_PREMARKET_DEVIATION_PCT=0.03  # 3%

# Staleness threshold for manual trades
MANUAL_TRADE_STALENESS_HOURS=24  # Flag manual trades older than this

# Order validity
DEFAULT_ORDER_TIF=DAY  # DAY | GTC | GTD
GTD_DAYS_AHEAD=1  # For GTD orders, how many days ahead
```

#### 2.5B.3: Pre-Market Price Deviation Check

**New File:** `src/validation/price_deviation.py`

```python
@dataclass
class PriceDeviationCheck:
    """Result of checking price movement since opportunity creation."""
    symbol: str
    original_price: float
    current_price: float
    deviation_pct: float
    threshold_pct: float
    requires_confirmation: bool
    message: str

class PriceDeviationValidator:
    """Check if underlying has moved significantly since opportunity creation."""
    
    def check_deviation(
        self,
        opportunity: TradeOpportunity,
        current_price: float,
        threshold_pct: float = 0.03
    ) -> PriceDeviationCheck:
        """
        Compare current price to price at opportunity creation.
        
        Returns:
            PriceDeviationCheck with confirmation requirement if exceeded.
        """
        pass
    
    def check_staleness(
        self,
        opportunity: TradeOpportunity,
        max_age_hours: int = 24
    ) -> bool:
        """
        Check if manual opportunity is too old.
        
        Returns True if opportunity should be flagged as stale.
        """
        pass
```

#### 2.5B.4: Order Timing Handler

**New File:** `src/execution/order_timing.py`

```python
class OrderTimingHandler:
    """Handle order placement based on market session."""
    
    def __init__(self, calendar: MarketCalendar, config: dict):
        self.calendar = calendar
        self.timing_mode = config.get("ORDER_TIMING_MODE", "IMMEDIATE")
    
    def prepare_order(
        self,
        opportunity: TradeOpportunity,
        order_type: str = "LIMIT"
    ) -> PreparedOrder:
        """
        Prepare order with appropriate timing settings.
        
        - IMMEDIATE: Place now (may be outside-hours order)
        - MARKET_OPEN: Queue for market open
        - MANUAL_TRIGGER: Wait for user to trigger
        
        Returns PreparedOrder with:
        - time_in_force (DAY/GTC/GTD)
        - scheduled_time (if queued)
        - price_adjustment (bid under mid for fast fill)
        """
        pass
    
    def adjust_limit_price(
        self,
        bid: float,
        ask: float,
        session: MarketSession
    ) -> float:
        """
        Calculate optimal limit price based on session.
        
        - Regular hours: Slightly below mid
        - Pre-market/after-hours: More aggressive
        """
        mid = (bid + ask) / 2
        
        if session == MarketSession.REGULAR:
            # Just under mid for reasonable fill speed
            return round(mid - 0.01, 2)
        else:
            # More aggressive when liquidity is low
            return round(bid + 0.01, 2)
```

#### 2.5B.5: Trade Command Integration

**Update:** `src/cli/main.py` (trade command)

Add market hours display and handling:

```python
# At start of trade command
calendar = MarketCalendar()
session = calendar.get_current_session()

console.print(f"\n[bold]Market Status:[/bold] {session.value}")

if session == MarketSession.CLOSED:
    console.print("[yellow]⚠ Market is closed. Orders will queue for next open.[/yellow]")
    console.print(f"   Next open: {calendar.next_market_open().strftime('%Y-%m-%d %H:%M ET')}")
elif session == MarketSession.PRE_MARKET:
    console.print("[yellow]⚠ Pre-market session. Checking for price deviations...[/yellow]")
```

### Success Criteria

| Criterion | Validation Method |
|-----------|-------------------|
| Correct session detection | Unit test across all session types |
| Holiday detection | Unit test with 2026 holiday list |
| Price deviation flagged | Create stale opportunity, verify warning shown |
| Order timing respected | Mock clock, verify order TIF changes |

### Test Coverage Requirements

- Unit tests for all market session boundaries
- Unit tests for holiday detection
- Integration test for pre-market deviation flow
- Integration test for queued order at market open
- Edge case: DST transition handling

---

## Phase 2.5C: User Experience Improvements (4 days)

### Objective
Improve the trade approval workflow with batch operations, better visibility, and session recovery.

### Why This Matters
- Individual approval for 8+ opportunities is tedious
- Users cannot see risk-filtered opportunities (erodes trust)
- No recovery if connection drops mid-execution
- No visibility into "what-if" scenarios

### Deliverables

#### 2.5C.1: Batch Approval Interface

**Update:** `src/cli/trade_presenter.py` (new file)

```python
class TradePresenter:
    """Enhanced presentation and approval of trade opportunities."""
    
    def present_opportunities(
        self,
        qualified: list[TradeOpportunity],
        risk_blocked: list[tuple[TradeOpportunity, str]],  # (opp, reason)
        console: Console
    ) -> list[int]:
        """
        Present opportunities with batch approval options.
        
        Shows:
        1. Qualified opportunities (numbered, with details)
        2. Risk-blocked opportunities (with reasons)
        3. Approval options
        
        Returns list of approved opportunity indices.
        """
        pass
    
    def _show_qualified_table(
        self,
        opportunities: list[TradeOpportunity],
        console: Console
    ) -> None:
        """Display qualified opportunities in rich table."""
        table = Table(title="Qualified Opportunities")
        table.add_column("#", style="cyan", width=3)
        table.add_column("Symbol", style="green")
        table.add_column("Strike", justify="right")
        table.add_column("Expiry")
        table.add_column("Premium", justify="right")
        table.add_column("OTM %", justify="right")
        table.add_column("Margin Eff.", justify="right")  # NEW
        table.add_column("Source")
        table.add_column("Rank", justify="right")
        # ... populate rows
    
    def _show_risk_blocked_table(
        self,
        blocked: list[tuple[TradeOpportunity, str]],
        console: Console
    ) -> None:
        """
        Display risk-blocked opportunities with reasons.
        
        Critical: Users NEED to see what was filtered and why.
        """
        console.print("\n[yellow]Risk-Blocked Opportunities:[/yellow]")
        table = Table(show_header=True)
        table.add_column("Symbol")
        table.add_column("Strike")
        table.add_column("Blocked Reason", style="red")
        
        for opp, reason in blocked:
            table.add_row(opp.symbol, str(opp.strike), reason)
        
        console.print(table)
    
    def _get_batch_approval(self, count: int, console: Console) -> list[int]:
        """
        Get user approval with batch options.
        
        Options:
        - 'a' or 'all': Approve all
        - 'n' or 'none': Reject all
        - '1,3,5': Approve specific numbers
        - '1-5': Approve range
        - 'q': Quit
        
        Returns list of approved indices (0-based).
        """
        console.print("\n[bold]Approval Options:[/bold]")
        console.print("  [cyan]a[/cyan] or [cyan]all[/cyan]  - Approve all qualified")
        console.print("  [cyan]n[/cyan] or [cyan]none[/cyan] - Reject all")
        console.print("  [cyan]1,3,5[/cyan]    - Approve specific (comma-separated)")
        console.print("  [cyan]1-5[/cyan]      - Approve range")
        console.print("  [cyan]q[/cyan]        - Quit without executing")
        
        choice = console.input("\n[bold]Your choice:[/bold] ").strip().lower()
        
        # Parse choice and return indices
        pass
```

#### 2.5C.2: Margin Efficiency Display

**Update:** `src/strategies/base.py` (TradeOpportunity)

```python
@dataclass
class TradeOpportunity:
    # ... existing fields ...
    
    # NEW: Margin efficiency (your 1:10 to 1:20 ratio)
    margin_requirement: float = 0.0
    margin_efficiency_pct: float = 0.0  # premium / margin * 100
    margin_efficiency_ratio: str = ""   # "1:12" format for familiarity
    
    def calculate_margin_efficiency(self) -> None:
        """
        Calculate margin efficiency ratio.
        
        Your manual check: $400 premium with $4000-$8000 margin = 5-10%
        This is equivalent to 1:10 to 1:20 ratio.
        """
        if self.margin_requirement > 0:
            self.margin_efficiency_pct = (self.premium * 100 / self.margin_requirement) * 100
            ratio = self.margin_requirement / (self.premium * 100)
            self.margin_efficiency_ratio = f"1:{ratio:.0f}"
```

**Update:** `.env`

```bash
# Margin Efficiency Filter
MIN_MARGIN_EFFICIENCY_PCT=5.0   # 5% minimum (equivalent to 1:20 ratio)
MAX_MARGIN_EFFICIENCY_PCT=15.0  # 15% maximum (equivalent to ~1:7 ratio)
# Your comfort zone: 5-10% (1:10 to 1:20)
```

#### 2.5C.3: Session State & Recovery

**New File:** `src/execution/session_state.py`

```python
import json
from pathlib import Path
from datetime import datetime

class TradeSessionState:
    """
    Persist session state for recovery after interruption.
    
    Saves to: data/sessions/session_{timestamp}.json
    """
    
    STATE_DIR = Path("data/sessions")
    
    def __init__(self):
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.state_file = self.STATE_DIR / f"session_{self.session_id}.json"
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)
    
    def save_state(
        self,
        phase: str,
        opportunities: list[dict],
        approved: list[int],
        executed: list[int],
        failed: list[int]
    ) -> None:
        """Save current session state for recovery."""
        state = {
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            "phase": phase,
            "opportunities": opportunities,
            "approved": approved,
            "executed": executed,
            "failed": failed,
        }
        self.state_file.write_text(json.dumps(state, indent=2))
    
    @classmethod
    def find_incomplete_sessions(cls) -> list[Path]:
        """Find sessions that didn't complete."""
        pass
    
    @classmethod
    def resume_session(cls, session_file: Path) -> "TradeSessionState":
        """Load and resume an incomplete session."""
        pass
    
    def mark_complete(self) -> None:
        """Mark session as complete (rename file)."""
        complete_file = self.state_file.with_suffix(".complete.json")
        self.state_file.rename(complete_file)
```

#### 2.5C.4: What-If Risk Analysis

**New File:** `src/analysis/what_if.py`

```python
class WhatIfAnalyzer:
    """
    Analyze impact of approving combinations of opportunities.
    
    Use case: "If I approve #1 and #3, will I hit max positions?"
    """
    
    def analyze_selections(
        self,
        opportunities: list[TradeOpportunity],
        selected_indices: list[int],
        current_positions: int,
        risk_limits: dict
    ) -> WhatIfResult:
        """
        Analyze what happens if user approves selected opportunities.
        
        Returns WhatIfResult with:
        - Would exceed position limit?
        - Would exceed sector concentration?
        - Would exceed margin utilization?
        - Estimated total premium
        - Estimated total margin used
        """
        pass
```

#### 2.5C.5: Trade Command Integration

**Update:** `src/cli/main.py` (trade command)

```python
# After Phase 4 (Risk Checks) - SHOW filtered opportunities
presenter = TradePresenter()

# Separate qualified from risk-blocked
qualified = [opp for opp, check in all_opportunities if check.approved]
risk_blocked = [(opp, check.reason) for opp, check in all_opportunities if not check.approved]

# Present both (critical for trust)
approved_indices = presenter.present_opportunities(qualified, risk_blocked, console)

if not approved_indices:
    console.print("[yellow]No opportunities approved. Exiting.[/yellow]")
    return

# What-if analysis before execution
analyzer = WhatIfAnalyzer()
what_if = analyzer.analyze_selections(
    qualified, 
    approved_indices, 
    current_positions,
    risk_limits
)

if what_if.warnings:
    console.print("\n[yellow]⚠ Warnings:[/yellow]")
    for warning in what_if.warnings:
        console.print(f"  • {warning}")
    
    if not Confirm.ask("Proceed anyway?"):
        return
```

### Success Criteria

| Criterion | Validation Method |
|-----------|-------------------|
| Batch approval works | Test "all", "1,3,5", "1-3" inputs |
| Risk-blocked visible | Trigger risk rejection, verify shown |
| Margin efficiency displayed | Check table output for column |
| Session recovery works | Kill mid-execution, resume, verify completion |
| What-if analysis accurate | Select 3 opportunities, verify calculation |

### Test Coverage Requirements

- Unit tests for approval parsing ("all", "1,3,5", "1-3", invalid input)
- Unit tests for margin efficiency calculation
- Integration test for interrupted session recovery
- Integration test for what-if analysis
- Edge case: Empty qualified list, only risk-blocked

---

## Phase 2.5D: Trade Context Capture (3 days)

### Objective
Capture complete market and decision context at trade time for the learning engine.

### Why This Matters
- Learning engine needs context to find patterns
- "Why did this trade succeed/fail?" requires knowing the conditions
- Post-hoc reconstruction is error-prone and incomplete
- Enables future analysis like "trades in high VIX environments"

### Deliverables

#### 2.5D.1: Market Context Snapshot

**New File:** `src/data/context_snapshot.py`

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class MarketContext:
    """Market-wide context at time of trade."""
    timestamp: datetime
    
    # Broad market
    spy_price: float
    spy_change_pct: float  # Daily change
    qqq_price: float
    qqq_change_pct: float
    
    # Volatility
    vix: float
    vix_change_pct: float
    
    # Market internals (if available)
    advance_decline_ratio: Optional[float] = None
    new_highs: Optional[int] = None
    new_lows: Optional[int] = None
    
    # Sector performance (top/bottom)
    sector_leaders: list[tuple[str, float]] = field(default_factory=list)
    sector_laggards: list[tuple[str, float]] = field(default_factory=list)

@dataclass
class UnderlyingContext:
    """Context for the specific underlying at trade time."""
    symbol: str
    timestamp: datetime
    
    # Price action
    current_price: float
    open_price: float
    high_price: float
    low_price: float
    previous_close: float
    
    # Trend indicators
    sma_20: float
    sma_50: float
    trend_direction: str  # "uptrend", "downtrend", "sideways"
    trend_strength: float  # 0-1 scale
    
    # Volatility
    iv_rank: float  # 0-100 percentile
    iv_percentile: float
    historical_vol_20d: float
    
    # Volume
    volume: int
    avg_volume_20d: int
    relative_volume: float  # volume / avg_volume
    
    # Technical levels
    support_levels: list[float] = field(default_factory=list)
    resistance_levels: list[float] = field(default_factory=list)

@dataclass 
class DecisionContext:
    """Complete context captured at trade decision time."""
    decision_id: str  # Unique identifier
    timestamp: datetime
    
    market: MarketContext
    underlying: UnderlyingContext
    
    # Strategy parameters at decision time
    strategy_params: dict
    
    # AI confidence (if using AI scoring)
    ai_confidence_score: Optional[float] = None
    ai_reasoning: Optional[str] = None
    
    # Source and ranking
    source: str  # "manual", "barchart", "manual+barchart"
    rank_position: int
    rank_score: float
    rank_factors: dict  # What contributed to ranking
```

#### 2.5D.2: Context Capture Service

**New File:** `src/services/context_capture.py`

```python
class ContextCaptureService:
    """Capture and store decision context for learning."""
    
    def __init__(self, ibkr_client: IBKRClient):
        self.ibkr = ibkr_client
    
    def capture_market_context(self) -> MarketContext:
        """Capture current market-wide context."""
        # Get SPY, QQQ, VIX data
        pass
    
    def capture_underlying_context(self, symbol: str) -> UnderlyingContext:
        """Capture context for specific underlying."""
        # Get price, indicators, IV, volume
        pass
    
    def capture_full_context(
        self,
        opportunity: TradeOpportunity,
        strategy_params: dict,
        rank_info: dict
    ) -> DecisionContext:
        """Capture complete decision context."""
        return DecisionContext(
            decision_id=self._generate_decision_id(),
            timestamp=datetime.now(),
            market=self.capture_market_context(),
            underlying=self.capture_underlying_context(opportunity.symbol),
            strategy_params=strategy_params,
            source=opportunity.source,
            rank_position=rank_info.get("position", 0),
            rank_score=rank_info.get("score", 0.0),
            rank_factors=rank_info.get("factors", {}),
        )
    
    def store_context(
        self,
        context: DecisionContext,
        opportunity_id: int
    ) -> None:
        """Store context linked to opportunity."""
        # Save to decision_contexts table
        pass
```

#### 2.5D.3: Database Schema for Context

**Migration:** `migrations/004_decision_context.sql`

```sql
-- Decision context storage
CREATE TABLE decision_contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT UNIQUE NOT NULL,
    opportunity_id INTEGER REFERENCES opportunities(id),
    trade_id INTEGER REFERENCES trades(id),
    
    timestamp TIMESTAMP NOT NULL,
    
    -- Market context (JSON for flexibility)
    market_context JSON NOT NULL,
    underlying_context JSON NOT NULL,
    strategy_params JSON NOT NULL,
    
    -- Ranking info
    source TEXT,
    rank_position INTEGER,
    rank_score REAL,
    rank_factors JSON,
    
    -- AI scoring (optional)
    ai_confidence_score REAL,
    ai_reasoning TEXT,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_context_opportunity ON decision_contexts(opportunity_id);
CREATE INDEX idx_context_trade ON decision_contexts(trade_id);
CREATE INDEX idx_context_timestamp ON decision_contexts(timestamp);
```

#### 2.5D.4: Trade Command Integration

**Update:** `src/cli/main.py` (trade command)

```python
# During Phase 6 (Execution) - before placing order
context_service = ContextCaptureService(client)

for idx in approved_indices:
    opportunity = qualified[idx]
    
    # Capture context BEFORE execution
    context = context_service.capture_full_context(
        opportunity=opportunity,
        strategy_params=strategy_config.model_dump(),
        rank_info={
            "position": idx + 1,
            "score": opportunity.rank_score,
            "factors": opportunity.rank_factors
        }
    )
    
    # Execute trade
    result = order_executor.execute_trade(opportunity)
    
    if result.success:
        # Link context to trade
        context_service.store_context(context, opportunity.id)
        
        # Update trade record with context_id
        trade_repo.link_context(result.trade_id, context.decision_id)
```

### Success Criteria

| Criterion | Validation Method |
|-----------|-------------------|
| Market context captured | Execute trade, query `market_context` JSON |
| Underlying context captured | Verify SMA, IV, volume in `underlying_context` |
| Context linked to trade | JOIN trades ↔ decision_contexts succeeds |
| Context queryable | `SELECT * WHERE market_context->>'vix' > 20` |

### Test Coverage Requirements

- Unit tests for context capture (mock IBKR data)
- Integration test for full capture → store → retrieve flow
- Test context survives trade failure (still captured)
- Edge case: IBKR timeout during context capture

---

## Configuration Updates Summary

### New `.env` Parameters

```bash
# ═══════════════════════════════════════════════════════════════
# PHASE 2.5: TRADE COMMAND ENHANCEMENTS
# ═══════════════════════════════════════════════════════════════

# --- 2.5A: Opportunity Lifecycle ---
OPPORTUNITY_TTL_HOURS=48              # Auto-expire opportunities after N hours
ENABLE_DUPLICATE_DETECTION=true       # Check for duplicate opportunities

# --- 2.5B: Market Hours Awareness ---
ORDER_TIMING_MODE=MARKET_OPEN         # IMMEDIATE | MARKET_OPEN | MANUAL_TRIGGER
MAX_PREMARKET_DEVIATION_PCT=0.03      # Flag if underlying moved >3%
MANUAL_TRADE_STALENESS_HOURS=24       # Flag manual trades older than 24h
DEFAULT_ORDER_TIF=DAY                 # DAY | GTC | GTD
GTD_DAYS_AHEAD=1                      # For GTD orders

# --- 2.5C: User Experience ---
SHOW_RISK_BLOCKED=true                # Show opportunities blocked by risk
ENABLE_BATCH_APPROVAL=true            # Allow "approve all" type commands
ENABLE_SESSION_RECOVERY=true          # Save session state for recovery
MIN_MARGIN_EFFICIENCY_PCT=5.0         # 5% min (1:20 ratio)
MAX_MARGIN_EFFICIENCY_PCT=15.0        # 15% max (~1:7 ratio)

# --- 2.5D: Context Capture ---
CAPTURE_MARKET_CONTEXT=true           # Capture SPY, VIX, etc.
CAPTURE_UNDERLYING_CONTEXT=true       # Capture per-symbol context
CONTEXT_CAPTURE_TIMEOUT_SEC=10        # Timeout for context capture
```

---

## File Changes Summary

### New Files (12 files)

| File | Phase | Purpose |
|------|-------|---------|
| `src/data/opportunity_state.py` | 2.5A | State machine enums and transitions |
| `src/execution/opportunity_lifecycle.py` | 2.5A | Lifecycle management with audit |
| `src/services/market_calendar.py` | 2.5B | Market hours and holiday awareness |
| `src/validation/price_deviation.py` | 2.5B | Pre-market deviation checking |
| `src/execution/order_timing.py` | 2.5B | Order timing handler |
| `src/cli/trade_presenter.py` | 2.5C | Enhanced trade presentation |
| `src/execution/session_state.py` | 2.5C | Session recovery |
| `src/analysis/what_if.py` | 2.5C | What-if risk analysis |
| `src/data/context_snapshot.py` | 2.5D | Context dataclasses |
| `src/services/context_capture.py` | 2.5D | Context capture service |
| `migrations/003_opportunity_lifecycle.sql` | 2.5A | DB schema update |
| `migrations/004_decision_context.sql` | 2.5D | Context storage schema |

### Modified Files (6 files)

| File | Changes |
|------|---------|
| `.env` | Add 15+ new configuration parameters |
| `src/cli/main.py` | Integrate all Phase 2.5 components |
| `src/strategies/base.py` | Add margin efficiency to TradeOpportunity |
| `src/data/opportunity_repository.py` | Add duplicate detection, state updates |
| `src/execution/risk_governor.py` | Return blocked opportunities with reasons |
| `src/config/base.py` | Add new configuration sections |

---

## Testing Requirements

### Unit Test Files (8 files)

```
tests/unit/
├── test_opportunity_state.py       # State machine transitions
├── test_opportunity_lifecycle.py   # Lifecycle manager
├── test_market_calendar.py         # Market hours
├── test_price_deviation.py         # Deviation checking
├── test_order_timing.py            # Order timing
├── test_trade_presenter.py         # Batch approval parsing
├── test_session_state.py           # Session recovery
├── test_context_capture.py         # Context capture
```

### Integration Test Files (4 files)

```
tests/integration/
├── test_opportunity_lifecycle_flow.py  # Full lifecycle: PENDING → EXECUTED
├── test_market_hours_workflow.py       # Pre-market, regular, after-hours
├── test_session_recovery.py            # Interrupt and resume
├── test_context_capture_flow.py        # Capture, store, retrieve
```

### Coverage Targets

| Component | Target |
|-----------|--------|
| Opportunity State Machine | >95% |
| Market Calendar | >95% |
| Trade Presenter | >90% |
| Context Capture | >90% |
| **Overall Phase 2.5** | **>90%** |

---

## Rollout Plan

### Week 1: Phase 2.5A (Data Model & State)

| Day | Tasks |
|-----|-------|
| Mon | Create state machine, write unit tests |
| Tue | Implement lifecycle manager |
| Wed | Database migrations, repository updates |
| Thu | Integration testing, bug fixes |
| Fri | Code review, documentation, checkpoint |

### Week 2: Phase 2.5B (Market Hours)

| Day | Tasks |
|-----|-------|
| Mon | Market calendar service, unit tests |
| Tue | Price deviation validator |
| Wed | Order timing handler |
| Thu | Integration with trade command |
| Fri | Testing, documentation, checkpoint |

### Week 3: Phase 2.5C (User Experience)

| Day | Tasks |
|-----|-------|
| Mon | Trade presenter with batch approval |
| Tue | Margin efficiency calculation |
| Wed | Session state and recovery |
| Thu | What-if analyzer |
| Fri | Full integration testing, checkpoint |

### Week 4: Phase 2.5D (Context Capture) + Polish

| Day | Tasks |
|-----|-------|
| Mon | Context dataclasses and schemas |
| Tue | Context capture service |
| Wed | Integration with trade command |
| Thu | End-to-end testing, bug fixes |
| Fri | Final documentation, release checkpoint |

---

## Success Metrics

### Quantitative

| Metric | Current | Target |
|--------|---------|--------|
| Test coverage | ~90% | >92% |
| States tracked | 2 (pending/executed) | 12 (full lifecycle) |
| Context fields captured | 0 | 30+ |
| Batch approval options | 0 | 5 (all, none, list, range, quit) |
| Recovery capability | None | Full session resume |

### Qualitative

| Improvement | Validation |
|-------------|------------|
| User can see why opportunities were filtered | UI shows risk-blocked table |
| User can approve multiple trades efficiently | Batch commands work |
| System handles pre-market correctly | Orders queue appropriately |
| Learning engine has context | Context queries return data |

---

## Dependencies

### Prerequisite: Phase 2 Complete
- OrderExecutor working
- PositionMonitor working  
- RiskGovernor working
- ExitManager working

### External Dependencies
- IBKR API for market data (already connected)
- No new external services required

### Follow-on Work
- Phase 3 (Learning Engine) will consume context data
- Phase 4 (Performance Analysis) will use lifecycle data
- Phase 5 (Continuous Loop) will use market calendar

---

## Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| IBKR rate limits during context capture | Medium | Medium | Add caching, throttling |
| Migration breaks existing data | Low | High | Backup before migration, rollback plan |
| Session recovery edge cases | Medium | Low | Extensive testing, manual recovery option |
| Market calendar holidays incorrect | Low | Medium | Verify against official NYSE calendar |

---

## Appendix A: State Transition Diagram

```
                                    ┌─────────────┐
                                    │   PENDING   │
                                    │  (created)  │
                                    └──────┬──────┘
                                           │
                                    enrichment
                                           │
                                    ┌──────▼──────┐
                                    │  ENRICHED   │
                                    │(live data)  │
                                    └──────┬──────┘
                                           │
                                    validation
                                           │
                         ┌─────────────────┴─────────────────┐
                         │                                   │
                  ┌──────▼──────┐                     ┌──────▼──────┐
                  │  VALIDATED  │                     │RISK_BLOCKED │
                  │(criteria ok)│                     │(with reason)│
                  └──────┬──────┘                     └─────────────┘
                         │
                  presented to user
                         │
                  ┌──────▼──────┐
                  │   OFFERED   │
                  │(awaiting)   │
                  └──────┬──────┘
                         │
            ┌────────────┼────────────┬──────────────┐
            │            │            │              │
     ┌──────▼──────┐ ┌───▼───┐ ┌─────▼─────┐ ┌─────▼─────┐
     │  APPROVED   │ │REJECTED│ │  SKIPPED  │ │  EXPIRED  │
     │(user yes)   │ │(user no)│ │(no action)│ │(TTL hit)  │
     └──────┬──────┘ └────────┘ └───────────┘ └───────────┘
            │
     order placed
            │
     ┌──────▼──────┐
     │  EXECUTING  │
     │(order sent) │
     └──────┬──────┘
            │
     ┌──────┴──────┐
     │             │
┌────▼────┐ ┌─────▼─────┐
│EXECUTED │ │  FAILED   │
│(filled) │ │(rejected) │
└─────────┘ └───────────┘
```

---

## Appendix B: Sample Configuration File

**File:** `config/trade_command.yaml` (alternative to .env for complex config)

```yaml
# Trade Command Configuration
# Override defaults from .env with structured config

opportunity_lifecycle:
  ttl_hours: 48
  enable_duplicate_detection: true
  states:
    - PENDING
    - ENRICHED
    - VALIDATED
    - RISK_BLOCKED
    - OFFERED
    - APPROVED
    - REJECTED
    - SKIPPED
    - EXECUTING
    - EXECUTED
    - FAILED
    - EXPIRED

market_hours:
  timezone: America/New_York
  pre_market_start: "04:00"
  regular_open: "09:30"
  regular_close: "16:00"
  after_hours_end: "20:00"
  order_timing_mode: MARKET_OPEN  # IMMEDIATE | MARKET_OPEN | MANUAL_TRIGGER
  
price_deviation:
  max_premarket_deviation_pct: 0.03
  manual_trade_staleness_hours: 24

user_experience:
  show_risk_blocked: true
  enable_batch_approval: true
  enable_session_recovery: true
  
margin_efficiency:
  min_pct: 5.0   # 1:20 ratio
  max_pct: 15.0  # 1:7 ratio
  display_format: "ratio"  # "ratio" (1:12) or "pct" (8.3%)

context_capture:
  enabled: true
  market_fields:
    - spy_price
    - vix
    - advance_decline
  underlying_fields:
    - price
    - sma_20
    - sma_50
    - iv_rank
    - volume
  timeout_seconds: 10
```

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-27 | Claude | Initial document |

---

**End of Implementation Plan**
