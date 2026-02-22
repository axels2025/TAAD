# Future Features - Research

> Roadmap for the Naked Put Trading System
> Created: 2026-02-19
> Based on: codebase review, industry best practices, practitioner research (Tastytrade, WealthyOption, BigERN/EarlyRetirementNow), and professional options desk standards.

---

## How This Document Is Organized

Features are grouped into tiers by importance to a naked put seller, then sorted within each tier by implementation priority. Each feature uses **plain English first**, then the correct technical term in parentheses so you can learn the lingo as you go.

- **Tier 1 — Must-Have:** Things that protect your money or fix blind spots. Missing these creates real risk.
- **Tier 2 — Should-Have:** Features that make you meaningfully better at trading. Standard on professional platforms.
- **Tier 3 — Nice-to-Have:** Cool stuff that adds polish or edge. Differentiators, not necessities.
- **Tier 4 — Moonshot Ideas:** Ambitious/experimental features others have tried. May or may not work.

For each feature:
- **What it is** (plain English)
- **Why it matters** (the risk or opportunity)
- **Technical details** (the lingo + how to build it)
- **What we have today** (current state in our codebase)
- **Effort estimate** (S/M/L)

---

## Current System Snapshot

Before diving into what's missing, here's what we already have that's working well:

| Area | Status | Key Components |
|------|--------|----------------|
| Baseline strategy (stock screening + options selection) | Done | NakedPutStrategy, screener, options_finder |
| NakedTrader (daily SPX/XSP put selling) | Done | Full module: chain, strike_selector, order_manager, watcher |
| Order execution (place + fill + reconcile) | Done | Adaptive executor, bracket orders, fill manager, reconciliation |
| Risk governor (pre-trade checks) | Done | Daily loss, margin, position count, sector limits, earnings check |
| Learning engine (pattern detection) | Done | 35+ patterns, A/B testing, path analysis, pattern combiner |
| Trade data capture (snapshots) | Done | 66-field entry snapshot, position snapshots, exit snapshots |
| Kill switch | Done | Emergency shutdown |
| Assignment detection | Done (just wired in) | AssignmentDetector + reconcile integration |

---

# TIER 1 — MUST-HAVE (Protect Your Money)

These are things that, if missing, expose you to real financial risk or leave you flying blind. Highest priority.

---

## 1.1 VIX-Based Position Scaling

**What it is:** Automatically trade smaller when the market is scared, and normal-sized when calm. The VIX (fear index) tells you how volatile the market expects to be. When VIX is high, everything is riskier — your puts are more likely to be tested, correlations between stocks spike, and margin requirements expand.

**Why it matters:** This is the single highest-impact improvement you can make. During the COVID crash (March 2020), VIX hit 82. Traders who didn't scale down got wiped out. Traders who cut size survived and profited from the fat premiums on the recovery.

**Technical details (VIX regime framework):**

| VIX Level | Regime Name | Position Size | Max Positions | Delta Target Adjustment |
|-----------|-------------|---------------|---------------|------------------------|
| < 12 | Ultra-low vol | 75% of normal | Normal | Standard — but premiums are thin, skip marginal trades |
| 12-16 | Low vol | 100% | Normal | Standard |
| 16-20 | Normal | 100% | Normal | Standard |
| 20-25 | Elevated | 75% | Reduce by 25% | Widen to lower delta (further OTM) |
| 25-30 | High vol | 50% | Reduce by 50% | Target 4-5 delta instead of 6.5 |
| 30-40 | Very high vol | 25% | Max 3-5 total | Target 3-4 delta |
| > 40 | Crisis | No new entries | Close weakest | Switch to defined-risk spreads or cash |

Also monitor the **VIX term structure** (the relationship between near-term and far-term VIX futures):
- **Normal (contango):** Near-term VIX < far-term. Market calm. Proceed normally.
- **Flat:** Uncertainty rising. Reduce sizes 20%.
- **Inverted (backwardation):** Near-term VIX > far-term. Market pricing imminent fear. Reduce 40-50%.
- **Deeply inverted (>5 points):** Extreme fear. Close all naked positions.

**What we have today:** VIX is captured in snapshots and the learning engine analyzes VIX patterns. But the risk governor does NOT automatically scale position sizes based on VIX. The NakedTrader config has a static delta target.

**Effort:** Medium (2-3 days). Add VIX query to risk governor, create regime classification, wire into position sizer and strike selector.

---

## 1.2 Portfolio-Level Greeks Dashboard

**What it is:** Instead of looking at each position's risk individually, see the combined risk of your entire portfolio as a single set of numbers. Like a dashboard for your car — speed, fuel, temperature — but for your trading account.

**Why it matters:** You could have 8 positions that each look fine individually but together create massive directional risk. If all your puts are on tech stocks, a tech sell-off hits every position at once. Portfolio Greeks let you see this.

**Technical details (portfolio Greeks):**

| Metric | What It Means (Plain English) | Technical Name | Target |
|--------|-------------------------------|----------------|--------|
| How much you lose if the market drops 1% | Your directional bet size | Beta-weighted portfolio delta | Keep < 0.1-0.3% of account value |
| How much you earn per day from time passing | Daily income from holding positions | Portfolio theta | Target 0.1-0.5% of account per day |
| How much you lose if fear spikes 1 point | Sensitivity to volatility changes | Portfolio vega | Monitor; naked puts are short vega |
| How fast your risk accelerates | Rate of change of directional exposure | Portfolio gamma | Highest near expiration — watch closely |
| Your total "at stake" amount | Dollar value of all underlying positions | Notional exposure | Should not exceed 3-5x account value |
| Ratio of income to risk | Efficiency of your premium collection | Theta/delta ratio | Higher is better; means more income per unit of risk |

**What we have today:** Position-level Greeks tracked in position_monitor. Risk governor tracks margin and position count. But no aggregated portfolio-level Greeks computation.

**Effort:** Medium (2-3 days). Query all open positions, compute beta-weighted aggregate Greeks using SPY/SPX as reference, display in CLI dashboard.

---

## 1.3 Tighter Margin Safety Limits

**What it is:** Our current margin limits are set aggressively — we allow using 80% of our buying power and only require 10% excess liquidity (cash cushion). During a market crash, margin requirements can jump 50-200% in hours. If you're at 80% usage and margins double, you get a margin call.

**Why it matters:** A margin call forces your broker to liquidate your positions at the worst possible prices. This turns a bad day into a catastrophic one. The Tastytrade golden rule: never use more than 50% of buying power.

**Technical details:**

| Setting | Current Value | Recommended | Why |
|---------|--------------|-------------|-----|
| Max margin utilization (buying power usage) | 80% | 50-60% | Leaves room for margin expansion during vol spikes |
| Min excess liquidity | 10% of NLV | 20% of NLV | Bigger cushion before margin call |
| Per-trade margin cap | 10% of NLV | Keep 10% | This is fine |

Add **margin stress projection**: before entering any trade, simulate what margin usage would look like if the market dropped 10% AND VIX doubled. If projected usage exceeds 80%, block the trade.

**What we have today:** Risk governor enforces max_margin_utilization and MIN_EXCESS_LIQUIDITY_PCT. The whatIfOrder check works. Just need to tighten the numbers and add stress projection.

**Effort:** Small (half day). Config changes + stress projection calculation.

---

## 1.4 Data Staleness / Connectivity Watchdog

**What it is:** If the connection to IBKR drops or price data stops updating, the system should immediately halt all trading. Right now, if the connection goes stale, we might continue making decisions based on old prices.

**Why it matters:** Trading on stale data is like driving with a foggy windshield. You might place an order at what you think is a good price, but the market has already moved against you.

**Technical details:**
- **Heartbeat check:** Every 30-60 seconds, verify IBKR connection is alive and recent price data is fresh (last update < 120 seconds ago).
- **On staleness:** Cancel all open orders, halt new entries, log critical alert.
- **On recovery:** Require manual restart (don't auto-resume — the operator should verify market conditions).

**What we have today:** Kill switch exists but is manually triggered. No automatic staleness detection.

**Effort:** Small (1 day). Timer loop checking connection state + last price timestamps.

---

## 1.5 Approaching-Limit Soft Brakes

**What it is:** Instead of going from "everything is fine" to "HALT!" when a limit is hit, start slowing down as you approach limits. Like how a car warns you about low fuel before the tank is empty.

**Why it matters:** Hard limits are binary — either you're under or you're halted. Soft limits give the system time to de-risk gradually, which produces better outcomes than emergency stops.

**Technical details:**

| Hard Limit | 50% Warning | 75% Caution | 100% Halt |
|------------|-------------|-------------|-----------|
| Daily loss -2% | At -1%: log warning | At -1.5%: reduce sizes 50%, no new positions | At -2%: full halt |
| Weekly loss -5% | At -2.5%: log warning | At -3.75%: reduce sizes 50% | At -5%: full halt |
| Margin usage 60% | At 30%: normal | At 45%: log warning, skip marginal trades | At 60%: halt new entries |
| Drawdown -10% | At -5%: log warning | At -7.5%: reduce sizes 50% | At -10%: full halt + human review |

**What we have today:** Hard limits only. No graduated response.

**Effort:** Medium (1-2 days). Add scaling factors to risk governor based on proximity to limits.

---

## 1.6 Stress Testing / Scenario Analysis

**What it is:** Every night (or before each trade), calculate what would happen to your portfolio under various bad scenarios. "What if the market drops 5% tomorrow and fear doubles?" This tells you whether you can survive the worst before it happens.

**Why it matters:** You want to know your worst case BEFORE it happens, not during. If a stress test shows you'd lose 30% in a crash, you're over-leveraged and should reduce.

**Technical details — run these scenarios nightly:**

| Scenario | Market Move | VIX Change | What You're Testing |
|----------|-------------|------------|---------------------|
| Bad day | SPX -3% | VIX +5 | Can you handle a normal sell-off? |
| Sharp sell-off | SPX -5% | VIX +10 | Can you survive without margin call? |
| Correction starts | SPX -10% | VIX +20 | Are you still solvent? |
| Crash | SPX -20% | VIX +40 | Would you blow up? |
| Vol-only shock | SPX flat | VIX doubles | What's your pure vol exposure? |
| Sector crash | Worst sector -15% | VIX +10 | Are you too concentrated? |

**For each scenario, compute:**
```
Stress P&L per position = (New_Option_Price - Current_Option_Price) × contracts × 100
```
Where `New_Option_Price` = Black-Scholes price with shocked underlying and shocked IV.

**Rule:** If any single scenario shows portfolio loss > 15-20% of account value, you're over-leveraged. Reduce positions.

**What we have today:** Nothing. No stress testing at all. The risk governor checks limits in real-time but doesn't project forward.

**Effort:** Large (3-5 days). Need Black-Scholes pricer, scenario engine, nightly scheduler, and reporting.

---

# TIER 2 — SHOULD-HAVE (Be Meaningfully Better)

These features are standard on professional platforms. They won't prevent a blowup (Tier 1 does that), but they make you more efficient, more informed, and more disciplined.

---

## 2.1 Rolling Strategies (Adjusting Losing Positions)

**What it is:** When a put you sold is moving against you (the stock is falling toward your strike), instead of taking the full loss, you "roll" — close the current position and open a new one at a lower strike and/or further out in time. It's like moving the goalposts when you're about to lose.

**Why it matters:** Rolling can convert losing trades into eventual winners by giving the position more time and a better strike. Tastytrade research shows rolling down and out (lower strike, more time) recovers ~70% of challenged positions.

**Technical details (rolling mechanics):**

| Roll Type | What You Do | When to Use | Plain English |
|-----------|-------------|-------------|---------------|
| Roll down | Close current put, sell new put at lower strike, same expiration | Stock dropping but not crashing | Move your line in the sand lower |
| Roll out | Close current put, sell new put at same strike, later expiration | Need more time for recovery | Buy yourself more time |
| Roll down and out | Close current, sell new at lower strike AND later expiration | Stock dropping AND time running out | Move the line AND buy time |

**Decision rules for automated rolling:**
1. **When to roll:** Position is at 2x loss (200% of premium received) AND there's still time value left
2. **Where to roll to:** Same delta as original entry (e.g., 6.5 delta) on the new expiration
3. **When NOT to roll:** If rolling would exceed your risk limits, or if the credit received for rolling is less than $0.10
4. **Max rolls per position:** 2 (after that, take the loss — don't throw good money after bad)

**What we have today:** `roll_manager.py` exists in services but appears to be a stub/skeleton. No active rolling logic.

**Effort:** Medium-Large (3-4 days). Need roll detection logic, roll order execution, roll tracking in DB, and integration with exit manager.

---

## 2.2 Expiration Concentration Limits

**What it is:** Don't let too many of your positions expire on the same date. If 80% of your positions expire on Friday and the market crashes Thursday night, ALL of them get tested at once.

**Why it matters:** Near expiration, options have the highest gamma (their risk changes fastest). Concentrating expirations concentrates your gamma risk on specific dates. Spreading expirations across multiple dates is like not putting all your eggs in one basket.

**Technical details:**
- **Rule:** No more than 30-40% of positions expiring on the same date
- **DTE bucket limits:** Distribute across 0-3 DTE, 4-7 DTE, 8-14 DTE, 14+ DTE — max 40% in any bucket
- **For NakedTrader (daily SPX):** This is less relevant since you're always in 1-4 DTE. But if you scale to multiple contracts per day, stagger entry times.

**What we have today:** No expiration concentration tracking.

**Effort:** Small (half day). Query open positions, group by expiration, add check to risk governor.

---

## 2.3 Drawdown Recovery Protocol

**What it is:** After a big loss triggers a trading halt, don't jump back in at full speed. Ease back in gradually, like a runner returning from injury.

**Why it matters:** The #1 mistake after a drawdown is trying to "make it back" by trading bigger. This almost always makes things worse. A systematic recovery protocol removes emotion from the equation.

**Technical details:**

| Week After Halt | Position Size | Max Positions | Criteria to Advance |
|-----------------|---------------|---------------|---------------------|
| Week 1-2 | No trading | 0 | Analyze what happened |
| Week 3-4 | 50% of normal | Half of normal | Must be profitable |
| Week 5-8 | 75% of normal | 75% of normal | Must be profitable |
| Week 9+ | 100% | Normal | Only if drawdown recovered 50%+ |

**Formula for dynamic sizing:**
```python
recovery_factor = min(1.0, max(0.25, 1.0 - (current_drawdown / max_drawdown_limit)))
adjusted_size = normal_size * recovery_factor
```

**What we have today:** Daily/weekly/drawdown halt limits exist. But after a halt, the system just resumes at full size when the limit resets. No gradual recovery.

**Effort:** Small-Medium (1-2 days). Track drawdown state, apply scaling factor to position sizer.

---

## 2.4 Correlation Monitoring

**What it is:** Track how much your positions move together. In normal markets, AAPL and JPM don't move the same way. But in a crash, everything drops together — correlations spike from 0.3 to 0.8+. Your "diversified" 10 positions suddenly behave like 3.

**Why it matters:** High correlation means your diversification is an illusion. You think you have 10 independent bets, but you really have 3-4.

**Technical details:**
- Compute 60-day rolling pairwise correlation between all position underlyings
- Track average pairwise correlation as a single number
- **Effective number of independent bets:** `N_effective = N² / sum(all_pairwise_correlations)`

| Avg Correlation | Regime | Action |
|-----------------|--------|--------|
| < 0.30 | Low (good diversification) | Full sizes |
| 0.30-0.50 | Normal | Standard sizes |
| 0.50-0.70 | High | Reduce new entries by 20-30% |
| > 0.70 | Crisis (everything moves together) | Halt new entries |

**What we have today:** Config has `max_correlation: 0.70` but it's not actively computed or enforced.

**Effort:** Medium (2-3 days). Need price history for each underlying, correlation matrix computation, integration with risk governor.

---

## 2.5 The Wheel Strategy Integration

**What it is:** When a naked put gets assigned (you're forced to buy the stock), instead of just eating the loss, sell covered calls on the stock you now own. This creates income while you wait for the stock to recover. The cycle of selling puts → getting assigned → selling calls → getting called away → selling puts again is called "The Wheel."

**Why it matters:** Assignment doesn't have to be a pure loss event. The Wheel turns assignment into a structured recovery process. Many naked put sellers already do this manually.

**Technical details:**
1. **Assignment detected** (our system now handles this): Record stock position
2. **Auto-sell covered call:** Select a call at the same strike as the put assignment (or slightly above), 14-30 DTE
3. **If called away:** Stock sold at strike, position closed. Sell new put.
4. **If call expires worthless:** Sell another covered call. Repeat until called away.
5. **Track entire wheel cycle:** Put entry → assignment → call entries → call away. Full P&L across the cycle.

**What we have today:** Assignment detection works (just built). But no covered call logic, no wheel cycle tracking.

**Effort:** Large (4-6 days). Need call selling strategy, covered call order management, wheel cycle tracking in DB.

---

## 2.6 Defined-Risk Mode (Put Spreads)

**What it is:** Instead of selling a naked put (unlimited risk), sell a put spread — sell a put AND buy a cheaper put further out-of-the-money. This caps your maximum loss at the width of the spread.

**Why it matters:** Naked puts have theoretically unlimited risk (the stock could go to zero). Spreads cap your max loss. This is especially valuable during high-VIX periods, or if you want to trade larger size with a defined worst case. Also useful for smaller accounts that can't handle naked margin.

**Technical details:**
- **Entry:** Sell 6.5 delta put (same as now), buy a put 50-100 points lower (for SPX) or 5-10% lower strike (for stocks)
- **Cost:** Reduces premium income by 20-40%
- **Benefit:** Max loss = spread width - credit received. For example: sell $5950 put, buy $5900 put = max loss is $5,000 per spread minus credit
- **When to use:** VIX > 25, or total notional exceeds 3x account value, or portfolio margin approaching limits

**What we have today:** Nothing. Only naked put execution.

**Effort:** Medium (2-3 days). Add spread order logic to order executor, modify strike selector to find second leg, track spread P&L.

---

## 2.7 Notifications and Alerts

**What it is:** Get notified on your phone/desktop when important events happen — fills, approaching limits, assignments, drawdowns, system errors. Don't have to watch the terminal.

**Why it matters:** You can't stare at the screen all day. Push notifications let you live your life while the system watches the market.

**Technical details:**

| Event | Urgency | Channel |
|-------|---------|---------|
| Trade filled (entry or exit) | Low | Email or Telegram |
| Profit target hit | Low | Email or Telegram |
| Position approaching stop loss (50% of way there) | Medium | Push notification |
| Daily loss > 1% | Medium | Push notification |
| Margin utilization > 50% | Medium | Push notification |
| Assignment detected | High | Push notification + email |
| Circuit breaker triggered | Critical | Push + email + SMS |
| System error / disconnection | Critical | Push + email + SMS |

**Popular implementations:**
- **Telegram bot** (most common in trading community — free, easy API, group support)
- **Pushover** (push notifications, simple API)
- **Email via SendGrid/SES** (for detailed reports)
- **Discord webhook** (if you prefer Discord)

**What we have today:** `notifier.py` exists in services as a stub. No actual notification delivery.

**Effort:** Small-Medium (1-2 days). Implement Telegram bot integration (most bang for buck).

---

## 2.8 Backtesting Framework

**What it is:** Test your strategy on historical data before risking real money. "If I had traded this strategy for the last 5 years, what would have happened?"

**Why it matters:** Paper trading gives you live data going forward, but backtesting lets you see how your strategy would have performed through crashes, corrections, and various market conditions. It's the difference between testing a bridge by driving one car across it vs. simulating 10,000 cars and an earthquake.

**Technical details:**
- **Data source:** Historical option chain data (CBOE DataShop, OptionMetrics, or your IBKR historical data via Flex Queries)
- **Key features needed:**
  - Replay historical option chains day by day
  - Apply your strike selection logic to historical chains
  - Simulate fills at bid/ask (not mid — that's unrealistic)
  - Track P&L with realistic commissions and slippage
  - Generate performance reports: CAGR, max drawdown, Sharpe ratio, win rate
- **Option-specific challenges:**
  - Options data is expensive and large (thousands of strikes per day)
  - Greeks need to be recomputed or sourced historically
  - Bid-ask spreads matter enormously for short premium strategies

**Existing platforms:**
- **Whisper Trades** (used by WealthyOption — specifically for SPX put selling backtests)
- **OptionStack** (visual backtesting for options)
- **QuantConnect** (free, Python-based, has options data)
- **ORATS** (professional-grade options analytics and backtesting)

**What we have today:** TAAD (Trade Archaeology & Alpha Discovery) for importing and analyzing historical trades. No replay/simulation backtesting engine.

**Effort:** Large (1-2 weeks). Historical data acquisition + replay engine + metrics computation.

---

## 2.9 Continuous Agentic Loop (Phase 5)

**What it is:** Instead of running individual commands, the system runs continuously as a background service — perceiving market state, reasoning about what to do, acting, and learning. Like hiring a trader who works 24/7.

**Why it matters:** Currently you have to run commands manually (`nakedtrader sell XSP`, `nakedtrader reconcile`, `nakedtrader sell-watch`). A continuous loop automates the entire daily workflow: open positions, monitor, close, reconcile, learn.

**Technical details (from the spec):**
```
while True:
    state = perceive()        # Market data, positions, account state
    decision = reason(state)  # AI decides: trade, exit, hold, learn?
    execute(decision)         # Place orders, close positions
    learn()                   # Update patterns from new outcomes
    sleep(interval)           # Wait for next check
```

Components needed:
- **AgenticOrchestrator** — main loop coordinator
- **EventDetector** — watches for market events, portfolio events, system events
- **WorkingMemory** — remembers what it was doing, active experiments, recent decisions
- **Scheduler** — market hours awareness, pre-market prep, post-market reconciliation
- **Health monitoring** — self-check, auto-restart on failure

**What we have today:** All the pieces exist (execution, monitoring, learning, reconciliation). They just need to be wired into a continuous loop.

**Effort:** Large (7-10 days). Orchestrator design, event system, daemon process, health monitoring.

---

## 2.10 Commission Tracking and True P&L

**What it is:** Track actual commissions paid on every trade and subtract them from P&L. Your real profit is premium collected minus premium paid to close minus commissions.

**Why it matters:** Commissions add up fast with frequent trading. At $0.65/contract on IBKR, trading 2 contracts per day = ~$1.30/day x 252 trading days = $327.60/year. For small accounts, this is a meaningful drag. You need to see true net P&L.

**Technical details:**
- Capture commission from IBKR fill data (already partially done in reconciliation)
- Store per-trade commission in the Trade model (field exists: `commission`)
- Deduct from P&L calculations
- Track cumulative commissions in performance reports
- Compute "breakeven premium" — minimum premium that covers commission

**What we have today:** Commission field exists on Trade model. Reconciliation captures commissions from IBKR fills. But P&L calculations don't subtract commissions, and no cumulative reporting.

**Effort:** Small (half day). Wire commission into calc_pnl, add to performance reports.

---

# TIER 3 — NICE-TO-HAVE (Polish and Edge)

These features differentiate great systems from good ones. They add edge, convenience, or insight, but aren't critical.

---

## 3.1 Market Regime Detection

**What it is:** Automatically classify the market as trending up, trending down, or going sideways. Different regimes call for different strategies — aggressive put selling in uptrends, conservative or paused in downtrends.

**Why it matters:** Selling puts in an uptrend is easy money (the stock goes up, your put expires worthless). Selling puts in a downtrend is catching a falling knife. Knowing the difference automatically is a huge edge.

**Technical details:**

| Regime | How to Detect | Action |
|--------|--------------|--------|
| Strong uptrend | SPX > 20-day SMA > 50-day SMA, breadth positive | Full size, standard delta |
| Mild uptrend | SPX > 50-day SMA but below 20-day SMA | Standard size |
| Sideways/choppy | SPX oscillating around 50-day SMA, low ADX | Standard size, slightly wider delta |
| Mild downtrend | SPX < 50-day SMA but VIX < 25 | 75% size, wider delta |
| Strong downtrend | SPX < 50-day SMA AND VIX > 25 | 50% size or pause |
| Crash/panic | SPX < 200-day SMA AND VIX > 35 | No new naked positions |

Advanced detection uses:
- **Moving average alignment** (the relationship between short, medium, and long-term averages)
- **ADX** (Average Directional Index — measures trend strength regardless of direction)
- **Market breadth** (how many stocks are going up vs. down — tells you if the index is being carried by a few stocks)
- **Put/call ratio** (how much fear is in the options market)
- **Credit spreads** (high-yield bond spreads as a fear gauge)

**What we have today:** The learning engine detects some market context patterns (VIX level, SPY trend). The baseline strategy checks price > 20 EMA > 50 EMA. No formal regime classification system that feeds into position sizing.

**Effort:** Medium (2-3 days). Build regime classifier, wire into risk governor and position sizer.

---

## 3.2 Earnings and Event Calendar

**What it is:** Automatically track upcoming events that could cause big stock moves — earnings announcements (when a company reports quarterly results), Fed meetings (FOMC — when interest rate decisions happen), ex-dividend dates, economic data releases (CPI, jobs report, GDP). Block or adjust trades around these events.

**Why it matters:** Earnings can cause 5-20% overnight moves in individual stocks. FOMC meetings can move the entire market 1-3%. If you sell a put the day before earnings and the stock drops 15%, you lose big. Event awareness is table stakes for serious options traders.

**Technical details:**

| Event Type | Look-Ahead Window | Action |
|------------|-------------------|--------|
| Earnings | Within DTE of position | Block entry for that stock (you already have this!) |
| FOMC meeting | Day before + day of | Reduce all new entries by 50%, widen delta |
| CPI release | Day before + day of | Reduce all new entries by 25% |
| Jobs report (NFP) | Day before + day of | Reduce all new entries by 25% |
| Ex-dividend date | Day before (for American-style options) | Check for early assignment risk |
| Triple/quad witching | Expiration Friday | Be aware of increased volatility and pin risk |
| Market holidays | Day before + day after | Wider delta (overnight gap risk over holiday) |

**Data sources:**
- Earnings dates: Yahoo Finance API, Alpha Vantage, or IBKR fundamentals
- FOMC dates: Published annually by the Fed (static calendar)
- Economic calendar: Investing.com API, Trading Economics
- Ex-div dates: IBKR fundamentals or Yahoo Finance

**What we have today:** Earnings-within-DTE check exists in risk governor (blocks trades on stocks with upcoming earnings). No FOMC, CPI, or other economic event awareness.

**Effort:** Medium (2-3 days). Build event calendar service, integrate FOMC/CPI/NFP dates, wire into risk governor.

---

## 3.3 Tax Optimization

**What it is:** Track tax implications of your trades — especially wash sale rules (which disallow deducting a loss if you re-enter a similar position within 30 days) and Section 1256 contracts (SPX options get favorable 60/40 tax treatment — 60% taxed as long-term capital gains regardless of holding period).

**Why it matters:** Tax efficiency can be worth thousands of dollars per year. SPX options have a huge tax advantage over SPY/stock options due to Section 1256. Wash sale awareness prevents accidentally losing tax deductions.

**Technical details:**

| Tax Topic | Plain English | Technical Detail |
|-----------|---------------|------------------|
| Section 1256 contracts | SPX options taxed better than stock options | 60% long-term / 40% short-term rate regardless of hold time. SPX, XSP qualify. SPY does NOT. |
| Wash sale rule | Can't deduct a loss if you buy back the same thing within 30 days | If you close a SPY put at a loss and sell a new SPY put within 30 days, the loss gets added to the new position's cost basis |
| Tax lot tracking | Track the cost basis of each individual trade | FIFO (first in, first out) vs. specific lot identification |
| Year-end tax report | Generate a summary for your tax preparer | Net short-term gains, net long-term gains (1256), total commissions, wash sale adjustments |

**What we have today:** Nothing. No tax tracking at all.

**Effort:** Medium (3-4 days). Wash sale detection logic, Section 1256 flagging, year-end report generator.

---

## 3.4 Web Dashboard

**What it is:** A browser-based interface for viewing positions, P&L, risk metrics, and trade history. Instead of the terminal-only CLI, see your portfolio on a nice visual dashboard.

**Why it matters:** The CLI is great for operations, but charts, graphs, and visual dashboards make it much easier to spot trends, compare performance over time, and share results with others.

**Technical details:**
- **Backend:** FastAPI or Flask serving a REST API
- **Frontend:** React, Vue, or even a simple Streamlit/Gradio app
- **Key views:**
  - Position dashboard (live P&L, Greeks, days to expiry)
  - Risk dashboard (margin usage, VIX regime, stress test results)
  - Performance charts (cumulative P&L over time, win rate trends, drawdown chart)
  - Trade log (filterable, sortable)
  - Learning insights (detected patterns, experiment results)

**What we have today:** CLI-only with Rich tables. No web interface.

**Effort:** Large (1-2 weeks for MVP with Streamlit, 3-4 weeks for full React app).

---

## 3.5 Intraday Position Adjustment

**What it is:** During the trading day, if a position moves significantly against you, automatically take action — either close early, roll, or hedge — rather than waiting for end-of-day checks.

**Why it matters:** For 0-1 DTE options, things can change fast. A position that was fine at 10am can be deep in trouble by 2pm. Intraday monitoring with automatic responses catches problems before they become crises.

**Technical details:**
- Monitor positions every 1-5 minutes during market hours
- **Trigger conditions:**
  - Delta crosses above 50 (option is now more likely to lose than win)
  - Unrealized loss exceeds 150% of premium received
  - Underlying drops more than 1.5x the implied move for that DTE
- **Auto-response:**
  - At 150% loss: Send alert
  - At 200% loss: Auto-close if stop-loss enabled, or roll
  - At delta > 60: Mandatory close or roll

**What we have today:** nt-watch monitors positions but is manual. Exit manager checks profit targets and stop losses. No automatic intraday adjustment logic.

**Effort:** Medium (2-3 days). Enhanced position monitor with trigger engine and auto-response.

---

## 3.6 Performance Attribution

**What it is:** Break down your P&L into WHY you made or lost money. Was it because the market went up (directional/delta), because time passed (theta decay), because volatility dropped (vega), or because you picked good strikes? Attribution separates skill from luck.

**Why it matters:** If all your profits come from the market going up (delta) and not from theta decay, you're not really selling premium — you're just long the market. Attribution helps you understand your actual edge.

**Technical details:**

| P&L Component | What It Measures | How to Compute |
|---------------|-----------------|----------------|
| Delta P&L | Profit from market direction | delta × change_in_underlying × 100 × contracts |
| Theta P&L | Profit from time passing | theta × days_held × 100 × contracts |
| Vega P&L | Profit/loss from vol changes | vega × change_in_IV × 100 × contracts |
| Gamma P&L | Adjustment for non-linear price moves | 0.5 × gamma × (change_in_underlying)² × 100 × contracts |
| Residual | Everything else (execution quality, model error) | Total P&L - sum of above components |

**What we have today:** P&L is computed as (entry_premium - exit_premium) × contracts × 100. No attribution breakdown.

**Effort:** Medium (2-3 days). Need daily Greeks snapshots (already captured!) and attribution calculation.

---

## 3.7 Liquidity Scoring

**What it is:** Before selling a put, score how "tradeable" it is based on bid-ask spread, open interest, and volume. Skip illiquid options where you'd give up too much on the trade.

**Why it matters:** Wide bid-ask spreads are a hidden cost. If the bid is $0.40 and the ask is $0.80, you're getting $0.40 but it would cost you $0.80 to close — that's 50% slippage before you start.

**Technical details:**
- **Spread score:** (ask - bid) / mid. Under 15% is good, 15-25% acceptable, over 25% skip.
- **Open interest score:** > 500 contracts = liquid, 100-500 = acceptable, < 100 = skip
- **Composite liquidity score:** weighted average of spread, OI, and volume
- For SPX/XSP: nearly always liquid. More relevant for single-stock naked puts.

**What we have today:** Options finder checks for minimum premium but doesn't score liquidity. NakedTrader on SPX has no need (always liquid).

**Effort:** Small (1 day). Add bid-ask spread and OI checks to options_finder and strike_selector.

---

## 3.8 Multi-Account Support

**What it is:** Run the same strategy across multiple IBKR accounts (e.g., personal account + IRA + entity account) with independent position tracking but shared learning.

**Why it matters:** Many traders have multiple accounts. Managing them independently is tedious and error-prone. Shared learning means insights from one account benefit all.

**What we have today:** Account ID field exists on Trade model. TAAD supports account filtering. But execution, reconciliation, and risk management are single-account.

**Effort:** Large (1-2 weeks). Per-account risk limits, multi-account execution, shared learning DB.

---

# TIER 4 — MOONSHOT IDEAS (Experimental/Ambitious)

These are forward-looking ideas that push the boundaries. Some are being tried by others; results are mixed. High reward potential, high effort, and uncertain ROI.

---

## 4.1 ML-Based Volatility Forecasting

**What it is:** Use machine learning to predict whether actual future price movement (realized volatility) will be higher or lower than what the options market is pricing in (implied volatility). The difference between implied and realized vol is called the **variance risk premium** — it's the core edge in selling options.

**Why it matters:** If you can predict when implied vol is overpriced (great time to sell) vs. fairly priced (maybe skip), you can time your entries better. Instead of selling mechanically every day, you sell more when the edge is fattest.

**Technical details:**
- **Target variable:** Realized vol over next N days minus current implied vol (positive = implied overpriced = good to sell)
- **Features:**
  - Current VIX and VIX term structure
  - Historical realized vol (5, 10, 21, 63 day)
  - VIX/realized vol ratio
  - Put/call ratio
  - Market breadth indicators
  - Day of week, time of month effects
  - Recent earnings/events calendar
- **Models:** Gradient boosting (XGBoost/LightGBM) tends to work best for tabular financial data
- **Validation:** Walk-forward cross-validation (train on past, test on future, never peek)
- **Output:** Probability that selling premium today has above-average edge

**What others have found:**
- The variance risk premium is real and persistent (academic consensus)
- It's strongest after VIX spikes (mean-reversion)
- ML can modestly improve timing vs. mechanical daily entry
- But transaction costs and overfitting are real dangers

**What we have today:** Learning engine detects patterns in trade outcomes. No forward-looking volatility prediction.

**Effort:** Very Large (2-4 weeks). Historical data collection, feature engineering, model training, walk-forward validation, integration with entry decision.

---

## 4.2 Reinforcement Learning for Position Management

**What it is:** Train an AI agent that learns optimal actions (hold, close, roll, hedge) through trial and error on historical or simulated data. Unlike rule-based exits, the RL agent discovers the best action for each specific market state.

**Why it matters:** Fixed rules (close at 50% profit, stop at 200% loss) are good starting points but may not be optimal in all conditions. RL can learn nuanced policies like "hold longer when VIX is falling" or "close early when volume spikes."

**Technical details:**
- **State:** Current option Greeks, unrealized P&L, VIX level, DTE remaining, market trend
- **Actions:** Hold, close, roll down, roll out, add hedge
- **Reward:** Risk-adjusted P&L (Sharpe ratio or similar)
- **Algorithm:** PPO (Proximal Policy Optimization) or DQN (Deep Q-Network)
- **Training:** Replay historical option chains, simulate thousands of trade lifecycles
- **Challenge:** Options are path-dependent and have fat tails — standard RL struggles with rare catastrophic events

**What others have found:**
- Academic papers show promise but live results are mixed
- Biggest issue: training on historical data doesn't prepare for unprecedented events
- Works better as a "suggestion engine" alongside human rules, not a replacement
- The OpenAI approach of using RL for game-playing doesn't directly transfer to finance

**What we have today:** Learning engine uses statistical analysis, not RL. No simulation environment for training.

**Effort:** Very Large (4-8 weeks). Simulation environment, RL framework, training pipeline, safety constraints.

---

## 4.3 Sentiment Analysis for Risk Gating

**What it is:** Use natural language processing (NLP) to scan news headlines, social media, and earnings transcripts for signals that might affect your positions. If sentiment turns very negative on a stock you have a put on, close early or add protection.

**Why it matters:** News moves markets faster than price patterns. A CEO resignation, FDA rejection, or fraud allegation can tank a stock 20% before any technical indicator reacts. Early detection of negative sentiment gives you a head start.

**Technical details:**
- **Sources:** Financial news APIs (Benzinga, Alpha Vantage news), Reddit (r/wallstreetbets for retail sentiment), Twitter/X financial accounts, SEC filings (8-K for material events)
- **Processing:** LLM-based sentiment scoring (Claude API — you already have it!)
- **Signal:** Score from -1 (very bearish) to +1 (very bullish) per symbol
- **Rules:**
  - Sentiment < -0.5 on a position you hold: Alert
  - Sentiment < -0.8: Auto-close or add hedge
  - Sentiment spike detection: flag sudden shift from neutral to negative

**What others have tried:**
- Tastytrade found that news-based signals are too noisy for short-term options
- Works better for earnings/event-driven strategies than daily mechanical selling
- LLM-based analysis (like what you're building) shows promise for qualitative assessment

**What we have today:** Performance analyzer uses Claude API for trade analysis. No real-time news/sentiment monitoring.

**Effort:** Large (1-2 weeks). News API integration, sentiment scoring pipeline, alert system.

---

## 4.4 Naked Call Selling (Short Strangles)

**What it is:** In addition to selling puts (betting the market won't go down), also sell calls (betting the market won't go up too much). Selling both together is called a **short strangle**. This collects premium from both sides.

**Why it matters:** BigERN's 2025 results show adding calls to his put-selling strategy. His call PCR (premium capture rate — how much of the collected premium you keep as profit) was 57.3% in 2025. Not as high as puts (94% on 1DTE) because the market's natural upward bias eats into call profits. But still profitable and diversifies your premium income.

**Technical details:**
- **Delta target for calls:** Much lower than puts — 2-3 delta (very far OTM)
- **Why lower delta:** Market gaps UP overnight more often than DOWN. You need a wider margin.
- **Timing:** Do NOT enter calls after 3:00 PM ET (overnight gap-up risk)
- **Account requirement:** Portfolio margin strongly recommended. On portfolio margin, naked puts and calls offset each other (opposite directional bets), requiring little additional capital. On Reg-T, each naked call needs full margin.
- **Risk:** Unlimited on the upside. A surprise rate cut or takeover bid can cause a 5-10% spike.

**What we have today:** Everything is put-only. No call selling capability.

**Effort:** Medium-Large (3-5 days). Add call chain retrieval, call strike selector, strangle tracking, P&L for strangles.

---

## 4.5 Options Flow / Dark Pool Intelligence

**What it is:** Monitor what big institutional traders are doing in the options market — large block trades (unusual options activity), dark pool prints, and put/call ratio changes. If institutions are buying massive amounts of puts on a stock, they might know something you don't.

**Why it matters:** Institutional flow can signal upcoming moves before they happen. If a hedge fund buys $50M in protective puts on a stock, and you're selling naked puts on that same stock, you might want to reconsider.

**Technical details:**
- **Data sources:** Unusual Whales, FlowAlgo, Cheddar Flow (paid services, $30-100/month)
- **Free alternatives:** IBKR has options volume data, CBOE has daily put/call ratios
- **What to watch:**
  - Unusual volume (> 2x average) on puts of your underlying
  - Large block trades (> 1000 contracts at a single strike)
  - Put/call ratio spikes on individual names
  - Dark pool short volume percentage

**What we have today:** Nothing. No options flow monitoring.

**Effort:** Medium-Large (depends on data source). API integration + signal processing + alert system.

---

## 4.6 Auto-Journaling with AI

**What it is:** Use Claude to automatically write a daily trading journal — summarizing what happened, why it happened, what went well, what went wrong, and what to watch for tomorrow. Like having a trading coach who reviews every day.

**Why it matters:** The best traders keep journals. But it's tedious. An AI journal that's automatically populated with real data (your actual trades, market conditions, and P&L) and enhanced with AI analysis would be incredibly valuable for learning.

**Technical details:**
- After each trading day, gather: positions opened/closed, P&L, VIX change, SPX change, any notable events
- Send to Claude API with prompt: "You are a trading coach reviewing today's session..."
- Generate structured journal entry: what happened, why, lessons learned, tomorrow's plan
- Store in database, make searchable
- Weekly/monthly AI summaries that identify recurring patterns in your behavior

**What we have today:** Performance analyzer generates weekly AI reports. No daily journaling.

**Effort:** Small-Medium (1-2 days). Prompt engineering + daily scheduler + storage.

---

## 4.7 Paper → Live Confidence Score

**What it is:** Track a "readiness score" that measures how well the system has performed in paper trading before you risk real money. The score considers win rate, consistency, drawdown history, number of trades, edge stability, and system reliability.

**Why it matters:** Everyone wonders "when is it safe to go live?" A quantitative confidence score replaces gut feeling with data. It answers: "Based on 200 paper trades, your strategy has a 78% win rate with 3.2% max drawdown and 99.7% system uptime — confidence score: 82/100."

**Technical details:**

| Component | Weight | Measurement |
|-----------|--------|-------------|
| Win rate vs. target | 25% | Actual win rate / target win rate |
| Drawdown discipline | 20% | Max drawdown stayed within limits? |
| Trade count sufficiency | 15% | At least 200 trades |
| Edge consistency | 15% | Sharpe ratio > 1.0 over rolling 3-month windows |
| System reliability | 15% | Uptime %, order fill rate, error rate |
| Risk limit compliance | 10% | Zero risk limit violations |

**Score thresholds:**
- < 60: Not ready. Keep paper trading.
- 60-75: Getting close. Maybe start with 10% of intended capital.
- 75-90: Good. Start with 25-50% of capital.
- > 90: Strong. Ready for full size.

**What we have today:** Nothing formal. Win rate and P&L tracked but no composite readiness score.

**Effort:** Small (1 day). Aggregate existing metrics into a score.

---

# SUMMARY: PRIORITIZED IMPLEMENTATION ROADMAP

## Phase A — Safety First (Weeks 1-2)
| # | Feature | Tier | Effort | Impact |
|---|---------|------|--------|--------|
| 1 | VIX-based position scaling | 1 | M | Highest single-feature risk reduction |
| 2 | Tighter margin limits | 1 | S | Config change + stress projection |
| 3 | Approaching-limit soft brakes | 1 | M | Graduated risk response |
| 4 | Data staleness watchdog | 1 | S | Prevents blind trading |
| 5 | Commission tracking | 2 | S | True P&L accuracy |

## Phase B — Risk Visibility (Weeks 3-4)
| # | Feature | Tier | Effort | Impact |
|---|---------|------|--------|--------|
| 6 | Portfolio-level Greeks dashboard | 1 | M | See aggregate risk at a glance |
| 7 | Stress testing / scenarios | 1 | L | Know your worst case before it happens |
| 8 | Expiration concentration limits | 2 | S | Prevent gamma bunching |
| 9 | Drawdown recovery protocol | 2 | S-M | Disciplined comeback after losses |

## Phase C — Strategy Enhancement (Weeks 5-8)
| # | Feature | Tier | Effort | Impact |
|---|---------|------|--------|--------|
| 10 | Continuous agentic loop (Phase 5) | 2 | L | Full automation |
| 11 | Rolling strategies | 2 | M-L | Recover challenged positions |
| 12 | Notifications (Telegram) | 2 | S-M | Don't miss critical events |
| 13 | Market regime detection | 3 | M | Adapt to market conditions |
| 14 | Earnings/event calendar | 3 | M | Avoid event land mines |

## Phase D — Depth & Polish (Weeks 9-12)
| # | Feature | Tier | Effort | Impact |
|---|---------|------|--------|--------|
| 15 | Correlation monitoring | 2 | M | Real diversification measurement |
| 16 | Defined-risk mode (spreads) | 2 | M | Capped downside when needed |
| 17 | Performance attribution | 3 | M | Understand your edge |
| 18 | Liquidity scoring | 3 | S | Avoid hidden costs |
| 19 | Tax optimization | 3 | M | Save on taxes |
| 20 | Paper → Live confidence score | 4 | S | Data-driven go-live decision |

## Phase E — Advanced / Experimental (Months 4+)
| # | Feature | Tier | Effort | Impact |
|---|---------|------|--------|--------|
| 21 | Wheel strategy integration | 2 | L | Turn assignments into income |
| 22 | Backtesting framework | 2 | L | Historical validation |
| 23 | Web dashboard | 3 | L | Visual monitoring |
| 24 | Intraday adjustment | 3 | M | Real-time defense |
| 25 | Naked call selling (strangles) | 4 | M-L | Second income stream |
| 26 | Auto-journaling with AI | 4 | S-M | Automated trade reflection |
| 27 | ML volatility forecasting | 4 | XL | Optimize entry timing |
| 28 | Sentiment analysis | 4 | L | News-driven risk gating |
| 29 | Options flow intelligence | 4 | M-L | Institutional signal |
| 30 | RL position management | 4 | XL | AI-optimized exits |
| 31 | Multi-account support | 3 | L | Scale across accounts |

---

## Key Numbers to Remember

These thresholds come from Tastytrade research, BigERN's documented results, CBOE options indices, and professional desk standards:

| Rule of Thumb | Number | Source |
|---------------|--------|--------|
| Max buying power usage | 50% | Tastytrade #1 rule |
| Min excess liquidity | 20% of NLV | Professional standard |
| Max portfolio notional | 3-5x NLV | Risk management standard |
| Hedge budget | 10-20% of premium income | Tastytrade recommendation |
| Max single-name exposure | 5-10% of NLV | Professional standard |
| VIX > 30: reduce size to | 50% | Practitioner consensus |
| VIX > 40: action | No new naked entries | Practitioner consensus |
| Max drawdown before halt | 10% | Common target |
| Min trades before going live | 200 | Statistical significance |
| Daily theta income target | 0.1-0.5% of NLV | Tastytrade research |
| Section 1256 tax advantage | 60% long-term rate | US tax code (SPX/XSP only) |

---

## Sources & Further Reading

1. **WealthyOption.com** — Primary source for 6.5 delta / 1-4 DTE / 70% profit target system
2. **EarlyRetirementNow.com (BigERN)** — Multi-year documented SPX put selling (Parts 1-14)
3. **Tastytrade/Tastylive research** — Extensive options selling studies (portfolio management, position sizing, rolling)
4. **CBOE S&P 500 PutWrite Index (PUT)** — Benchmark for systematic put selling
5. **Whisper Trades** — Backtesting platform for SPX options strategies
6. **Option Alpha** — Automated options trading platform with bot templates
7. **Spintwig.com** — Independent SPX options strategy backtests
8. **ORATS** — Professional options analytics and volatility data
9. **QuantConnect** — Open-source algorithmic trading platform with options support
10. **r/thetagang (Reddit)** — Community of premium sellers sharing strategies and results

---

*This document is a living roadmap. Features should be re-prioritized based on actual trading experience, market conditions, and account size. Safety features (Tier 1) should always be implemented before optimization features (Tier 3-4).*
