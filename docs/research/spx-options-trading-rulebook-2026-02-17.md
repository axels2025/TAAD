# The SPX Options Trading System — Complete Rulebook

*A plain-English guide to selling short-term SPX put options for income*

*Compiled 17 February 2026 — Based on the WealthyOption & BigERN systems*

---

## Table of Contents

1. [What Is This Strategy?](#1-what-is-this-strategy)
2. [Why SPX?](#2-why-spx)
3. [Core Concepts in Plain English](#3-core-concepts-in-plain-english)
4. [The Complete Rules](#4-the-complete-rules)
5. [Position Sizing — How Many Contracts?](#5-position-sizing--how-many-contracts)
6. [Step-by-Step Trade Walkthrough](#6-step-by-step-trade-walkthrough)
7. [Managing Winners](#7-managing-winners)
8. [Managing Losers](#8-managing-losers)
9. [The Call Side (Optional Bonus Income)](#9-the-call-side-optional-bonus-income)
10. [Risk Protection](#10-risk-protection)
11. [What Can Go Wrong — Honest Risk Assessment](#11-what-can-go-wrong--honest-risk-assessment)
12. [Account Requirements](#12-account-requirements)
13. [Daily Routine & Time Commitment](#13-daily-routine--time-commitment)
14. [Performance Expectations](#14-performance-expectations)
15. [Glossary — The Jargon Decoded](#15-glossary--the-jargon-decoded)
16. [Sources](#16-sources)

---

## 1. What Is This Strategy?

In one sentence: **You sell insurance on the S&P 500 index crashing, collect the premium, and keep the money when the crash doesn't happen (which is most of the time).**

More specifically:
- You sell SPX **put options** that expire in 1-4 days
- You pick strikes far below the current market price (so SPX would need to drop significantly for you to lose)
- You collect a premium (cash) upfront for selling each option
- You buy the option back when 70% of the premium has decayed (your profit target)
- You repeat this 5-8 times per week
- When the market does drop sharply, you take a loss — but because your strikes are so far away, this happens rarely

**The edge:** People consistently overpay for crash protection. The fear premium built into SPX puts is historically higher than the actual risk of a crash occurring. You're the insurance company collecting premiums that, on average, exceed the payouts.

**Track record:** Both WealthyOption (15 years trading) and BigERN (since 2011) have documented this strategy publicly with real results. BigERN made ~$90K from options in 2025 alone. Neither lost money during the COVID crash of 2020.

---

## 2. Why SPX?

SPX (the S&P 500 Index) is the instrument of choice for this strategy. Here's why in plain terms:

| Feature | What It Means For You |
|---------|----------------------|
| **Daily expirations (Mon-Fri)** | You can enter a new trade every single day — maximum flexibility |
| **Extremely liquid** | Tight spreads between buy and sell prices — you don't lose much to the middleman |
| **Cash-settled** | If you lose, you just pay cash. You never get assigned 500 stocks to deal with |
| **Tax advantaged** | In the US, SPX options get 60/40 tax treatment (60% taxed as long-term gains even if held for 1 day). Note: Australian tax treatment may differ — consult your accountant |
| **Diversified** | It's 500 companies. No single company can blow you up with surprise earnings or fraud |
| **No dividend risk** | Index dividends are spread across 500 stocks — no single ex-dividend date to worry about |
| **Circuit breakers** | The market literally halts if it drops 7%, 13%, or 20% in a day — your maximum loss per day is capped by market structure |

**Alternatives if SPX doesn't work for you:**
- **XSP** — Mini-SPX (1/10th the size of SPX, so ~$600 per point instead of $6,000). Same cash settlement and tax treatment. Good for smaller accounts.
- **SPY** — S&P 500 ETF. More accessible but: American-style (can be assigned shares), no 60/40 tax treatment, dividends to watch. Use if you can't access index options.

---

## 3. Core Concepts in Plain English

### What is a Put Option?

A put option gives the **buyer** the right to sell something at a specific price (the "strike price") by a specific date. If you **sell** a put option, you're making a promise: "I'll buy SPX at that strike price if it drops that low."

**Real-world analogy:** You're selling home insurance. The homeowner (put buyer) pays you a premium. If their house burns down (SPX crashes below the strike), you pay the claim. If it doesn't, you keep the premium.

### What Does "Selling a Put" Actually Do?

When you sell an SPX put:
1. You immediately receive cash (the premium) in your account
2. You now have an obligation: if SPX is below your strike price at expiration, you owe the difference × 100
3. If SPX stays above your strike price, the option expires worthless and you keep all the premium

**Example:** SPX is at 6,100. You sell a 5,950 put expiring tomorrow for $1.50.
- You receive $150 (=$1.50 × 100) immediately
- If SPX closes above 5,950 tomorrow: you keep $150. Done.
- If SPX closes at 5,900 tomorrow: you owe $5,000 (= (5,950 - 5,900) × 100). Net loss: $4,850.

### Delta — Your Probability Gauge

**Delta** tells you roughly how likely the market thinks it is that your option will end up "in the money" (ITM = losing for you).

- **Delta of 6.5** means: the market estimates a ~6.5% chance SPX will drop to your strike by expiration
- **Delta of 30** means: ~30% chance of reaching your strike
- **Delta of 50** means: the strike is right at the current price (coin flip)

**For this strategy, you're targeting delta 5.5-7.5 for puts.** That means you're picking strikes with roughly a 93-95% chance of expiring worthless (in your favour).

**How delta changes matters too:**
- When the market moves toward your strike, your delta increases (the option becomes more dangerous for you)
- When the market moves away from your strike, delta decreases (the option becomes safer)
- The closer to expiration, the more violently delta changes with each market move (this is gamma — see below)

### Gamma — The Acceleration Factor

**Gamma** measures how fast delta changes when the market moves. Think of it as the "acceleration" versus delta's "speed."

- **High gamma** (near expiration, near the strike): a small market move causes a big change in how dangerous your position is
- **Low gamma** (far from expiration, far from the strike): market moves don't change your risk much

**Why it matters for you:** With 1-4 day options, gamma is potentially extreme if SPX moves close to your strike. This is why you pick strikes FAR away (low delta). At 6.5 delta with 1-2 days left, your strike is typically 2-3% below the market — gamma only becomes a problem if SPX drops that much in a day or two.

**Plain English:** Gamma is the reason a "safe-looking" trade can turn ugly fast if the market drops sharply. It's also why you pick such distant strikes — to stay well outside the gamma danger zone.

### Theta — Time Decay (Your Best Friend)

**Theta** measures how much value an option loses each day just from the passage of time. For option sellers, theta is how you make money.

Key facts:
- Options lose value every day, even if the market doesn't move
- This decay **accelerates dramatically** in the final days before expiration
- The last 7 days of an option's life account for ~50-60% of its total time decay
- The last 1-2 days are where decay is fastest

**Plain English:** You're selling ice cream on a hot day. Every hour that passes, your ice cream is worth less. By selling options with only 1-4 days of life, you're selling the ice cream when it's melting fastest. That's the whole point.

### Implied Volatility (IV) — The Fear Gauge

**IV** reflects how much the market expects prices to move. High IV = market is scared = option premiums are expensive.

- When IV is high (VIX above 25-30), the premiums you collect are fatter AND your strike is pushed further from the current price (because delta 6.5 at high IV = a much more distant strike)
- When IV is low (VIX below 15), premiums are thinner but the market is calm
- **You don't need to time your entry based on IV.** This system trades continuously regardless. But it's useful to understand why your premiums and strike distances change day to day.

### Vega — Not Important Here

Vega measures sensitivity to changes in implied volatility. For 1-4 day options, vega is nearly irrelevant — there's too little time left for IV changes to matter much. You can safely ignore it.

---

## 4. The Complete Rules

### Rule 1: Instrument
**Trade only SPX options.** (Or XSP for smaller accounts, SPY as last resort.)

### Rule 2: Direction
**Sell put options** (and optionally call options — see Section 9).

### Rule 3: Days to Expiration (DTE)
**Always enter positions with 1-4 DTE.** Never open 0 DTE (expiring today). When your current trade closes or expires, your new position targets the next available expiration that is 1-4 days away.

*Rationale:* 0 DTE requires you to watch the screen all day. 1-4 DTE lets you set your order and walk away.

### Rule 4: Strike Selection (Delta)
**Sell puts at 5.5-7.5 delta, targeting 6.5 delta.** Look at the option chain, find the put with delta closest to 0.065 (or 6.5 if your platform shows it as a whole number).

What this looks like in practice:
- **Low IV (VIX ~13-15):** Your strike will be roughly 2-3% below the current SPX price
- **Normal IV (VIX ~18-22):** Your strike will be roughly 4-7% below current price
- **High IV (VIX ~30+):** Your strike will be 10-20% below current price — extremely far away

*Rationale:* Extensive backtesting by WealthyOption across all deltas shows 5.5-7.5 delta is the sweet spot for return vs drawdown. Higher deltas earn more per trade but blow up worse. Lower deltas are too small to be worth the commissions.

### Rule 5: Profit Target
**Set a Good-Till-Cancelled (GTC) limit order to buy back the option at 70% profit (i.e., 30% of the premium you received).**

Example: You sell a put for $2.00 ($200 per contract). Immediately place a GTC buy-to-close limit order at $0.60. When the option decays from $2.00 to $0.60, your order fills automatically and you've made $1.40 ($140) per contract.

*Rationale:* Backtesting shows 69-74% profit targets are optimal. Going higher (80-90%) means holding through unnecessary risk for diminishing returns.

### Rule 6: When Your Profit Target Hits → Enter New Trade
As soon as your buy-to-close order fills, **immediately open a new position** using the same rules (6.5 delta, 1-4 DTE, 70% profit target).

### Rule 7: No Stop-Loss on Individual Trades
**If the trade goes against you, hold it to expiration.** Do not panic-close.

*Rationale:* WealthyOption's backtesting shows that stop-losses on short-term SPX puts frequently trigger on intraday spikes that recover. The strategy accepts occasional losses as the cost of doing business. The frequent small wins overwhelm the rare larger losses over time.

**BigERN's alternative:** If you prefer a stop-loss for peace of mind, use 10-15× the premium received. (Sell for $1.00, stop at $10-$15.) This protects against truly catastrophic scenarios while avoiding getting stopped out on normal volatility.

### Rule 8: If the Trade Expires (Profit Target Doesn't Hit)
Two scenarios:
- **Option expires worthless (OTM):** You keep 100% of the premium. Enter a new trade.
- **Option expires in-the-money (ITM):** You take a loss (cash-settled — the difference between strike and SPX close × 100 is debited from your account). Enter a new trade.

### Rule 9: Always Have a Position On
**This is a continuous strategy.** You should always have a put position open. There is no "wait for the right conditions" — you sell systematically, every day.

*Rationale:* Trying to time entries adds decision-making, emotional bias, and reduces your exposure to the variance risk premium. The system works because you are always collecting premium.

### Rule 10: No Timing, No Indicators, No Opinions
**You do not care about:**
- What the market "looks like" today
- Whether economic data is coming out
- Whether you "feel" bearish or bullish
- Technical analysis, support/resistance levels
- Upcoming Fed meetings or earnings

The system is purely mechanical. Delta determines your strike. Profit target determines your exit. DTE determines your expiration. That's it.

**The one exception:** BigERN pauses 0DTE puts if the market opens down significantly and his overnight puts are already under pressure. This is a reasonable risk management adjustment (don't add fuel to a fire).

---

## 5. Position Sizing — How Many Contracts?

This is the most important section. **More accounts blow up from incorrect sizing than from bad trades.**

### Step 1: Determine Your Assumed Maximum Loss Per Contract

WealthyOption assumes that the worst-case scenario for any single trade is SPX falling 20% below your **strike price** (not the current market price — below your strike).

**Example:**
- SPX is at 6,100
- Your short put strike is 5,950 (about 2.5% below the market)
- Assumed max loss = 20% of 5,950 = 1,190 points
- In dollars: 1,190 × $100 = **$119,000 per contract**

*Is this realistic?* In 6+ years of backtesting across 3,000+ trades, the deepest any WealthyOption trade expired ITM was 3.2% below the strike. The 20% assumption is extremely conservative — it would require consecutive worst-days-ever.

### Step 2: Decide Your Allocation

How much of your portfolio do you want to allocate to this strategy?

| Allocation | Leverage | Risk Level | Notes |
|-----------|----------|------------|-------|
| 20% | ~1x | Conservative | Very safe, very modest returns |
| 40% | ~2x | Moderate | Good starting point for cautious traders |
| 60% | ~3x | Aggressive | Requires comfort with drawdowns |
| 80% | ~4x | Very Aggressive | WealthyOption's actual allocation |
| 100% | ~5x | Maximum | WealthyOption says "safe" but personally runs 80% |

**For beginners: start at 40% (2x leverage) or less.** You can always increase later once you've seen the strategy through a drawdown and know how you handle it emotionally.

### Step 3: Calculate Number of Contracts

**Formula:** Number of contracts = (Portfolio value × Allocation %) ÷ Assumed max loss per contract

**Example — $150,000 portfolio at 40% allocation:**
- Allocation: $150,000 × 0.40 = $60,000
- Assumed max loss per contract: $119,000 (from Step 1)
- Contracts: $60,000 ÷ $119,000 = **0.5 → Round down to 0 contracts**

Wait — that doesn't work! This is the honest truth about SPX: **it's big.** Each contract controls ~$600,000 of notional value at current levels.

**For a $150K account at 2x leverage, you'd sell 1 SPX put.** The maths:
- 1 contract with strike at 5,950 = $119K max loss (your whole account plus some)
- At 2x leverage: you need ~$60K of portfolio per contract

**For smaller accounts, use XSP** (1/10th the size):
- XSP max loss assumption: ~$11,900 per contract
- $150K portfolio at 40%: $60,000 ÷ $11,900 = **5 XSP contracts**

### Quick Reference — SPX Contract Count by Account Size (2x Leverage)

| Account Size | SPX Contracts | Notes |
|-------------|---------------|-------|
| $25,000 | 0 SPX (use 2 XSP) | Minimum viable account |
| $50,000 | 0-1 SPX (use 4 XSP) | 1 SPX is aggressive here |
| $100,000 | 1 SPX | Conservative |
| $150,000 | 1 SPX | Comfortable |
| $200,000 | 1-2 SPX | Standard |
| $500,000 | 3-4 SPX | Scaling up |

### The Doubling-Up Consideration

When your current trade is ITM (losing) at expiration, you need to enter a new trade simultaneously. For a brief period, you'll have two positions — the expiring one and the new one. **Your account needs enough buying power to handle this.** This is why WealthyOption caps allocation at ~60-70% for the doubling scenario — you need reserves.

---

## 6. Step-by-Step Trade Walkthrough

### Example Trade — Monday Entry

**Situation:** It's Monday morning. Your previous trade hit its 70% profit target overnight. Time to enter a new one.

**Step 1: Open the SPX option chain**
- Look at the expirations available: Tuesday (1 DTE), Wednesday (2 DTE), Thursday (3 DTE), Friday (4 DTE)
- You choose **Wednesday (2 DTE)** — it's the next closest expiration in the 1-4 DTE range

**Step 2: Find the 6.5 delta put**
- Scroll down the put side of the option chain
- Find the strike where delta is closest to 0.065 (or 6.5)
- Let's say SPX is at 6,100 and the 5,950 put shows delta 0.066
- That's your strike

**Step 3: Sell to open**
- Sell 1 (or however many your sizing allows) SPX 5,950 Put expiring Wednesday
- You receive $1.80 per contract ($180 total)

**Step 4: Immediately place profit target**
- Calculate 30% of $1.80 = $0.54
- Place a GTC limit order: Buy to close SPX 5,950 Put at $0.54

**Step 5: Walk away**
- Don't watch the screen. Don't check every hour. The GTC order handles everything.
- Two things will happen:
  - ✅ **Profit target hits:** Your order fills, you pocket $1.26 ($126) per contract. Go to Step 1.
  - ❌ **Expiration arrives, target hasn't hit:** The option either expires worthless (you keep $180) or expires ITM (you take a loss). Either way, go to Step 1.

### The Rhythm

In practice, most trades hit the 70% profit target within 1-2 days. This means you're typically entering 5-8 new trades per week. Each trade takes about 2-3 minutes to execute.

---

## 7. Managing Winners

**This is the easy part.** Winners manage themselves.

- Your GTC buy-to-close order at 70% profit handles everything automatically
- When it fills, you enter a new trade
- There is no second-guessing, no "should I hold for more?"
- **The 70% profit target is the rule.** Don't override it.

**Why not hold for 100% (let it expire worthless)?**

Because the last 30% of premium comes with disproportionate risk:
- You've already captured 70% of the possible profit
- Holding for the last 30% exposes you to the full remaining theta + gamma risk
- A sharp market move in the last day can turn a winner into a loser
- Backtesting confirms: taking profit at 70% produces better long-term results than holding to expiration

---

## 8. Managing Losers

**Rule: Hold losing trades to expiration.** Do not close early.

This is counterintuitive, but the rationale is solid:

1. **Short-term SPX options frequently spike and recover intraday.** A stop-loss triggers on the spike; holding through means you often still win.

2. **The 70% profit target already limits your time exposure.** Most trades close quickly. Losers are the exception.

3. **Every loss is bounded by time.** Unlike a stock position that can drift lower for weeks, your option expires in 1-4 days maximum. The bleeding stops automatically.

4. **The strategy is profitable in aggregate.** You will have losing trades. They are expected and priced into the system. The key is that winning trades happen ~90%+ of the time, and the cumulative wins exceed the occasional losses.

### What Happens When You Take a Loss?

It's cash-settled. If SPX closes at 5,900 and your strike was 5,950:
- Loss = (5,950 - 5,900) × 100 = $5,000 per contract
- This is debited from your account
- You immediately enter a new trade

**Emotionally:** This is the hardest part. You'll have weeks of steady $100-$200 wins, then one trade loses $3,000-$5,000. The system works because you don't let the losses stop you from re-entering. **The next trade has the same ~93% win probability as every other.**

### When WealthyOption's "No Stop" Doesn't Apply

If you're running **higher leverage** (4-5x) and a significant market decline threatens your ability to stay solvent, you may need to act:

1. **Reduce position size** (close some contracts to free buying power)
2. **Convert to spreads** — buy a cheaper put further OTM to cap your downside
3. **If you're truly at risk of a margin call**, close the position. Surviving to trade tomorrow matters more than any single trade.

BigERN's approach: **10-15× premium stop-loss.** If you sold for $1.00, close the position if it reaches $10-$15. This provides a defined maximum loss per trade while still being wide enough to avoid false triggers.

---

## 9. The Call Side (Optional Bonus Income)

In addition to selling puts, you can also sell **call options** on SPX. This is essentially betting the market won't spike dramatically upward.

### Call Rules

| Parameter | Value |
|-----------|-------|
| Delta | 2-3 (even further OTM than puts) |
| DTE | 1-4 (same as puts) |
| Profit target | 78-83% (slightly higher than puts) |
| Stop-loss | None (hold to expiration, same as puts) |

### Why Lower Delta for Calls?

The market tends to go up over time and can gap up overnight on good news. Selling calls is inherently more dangerous than selling puts because:
- Market crashes are sudden but markets grind upward — your put protection from volatility doesn't work the same on the call side
- Overnight gaps tend to be positive more often than negative

The 2-3 delta target means your call strike is typically 1-2% above the current price with very short DTE — a very wide margin of safety.

### Timing Restriction for Calls

**Do not enter new call positions after 3:00 PM ET.** Backtesting shows that selling calls late in the day, then holding overnight, exposes you to the market's tendency to gap up on positive overnight news. Wait until the next morning.

### Portfolio Margin Requirement

Calls only make sense if you're on **portfolio margin.** On portfolio margin, your naked puts and naked calls offset each other (they're opposite bets), requiring little additional buying power. On regular (Reg-T) margin, each naked call requires full margin — making it capital-inefficient.

### BigERN's Call Strategy (2025)

BigERN sells 20-24 0DTE calls per day. His 0DTE call PCR (premium capture rate) was 57.3% in 2025. Lower than puts (94% on 1DTE) because the market's upward bias eats into call profits more often. But still profitable.

---

## 10. Risk Protection

### Level 1: Position Sizing (Essential)

Already covered in Section 5. This is your primary risk control. Lower leverage = lower risk. Start conservative.

### Level 2: Spreads Instead of Naked (For Defined Risk)

Instead of selling a naked put, you can buy a cheaper put further OTM to cap your maximum loss:

**Example — Put Credit Spread:**
- Sell SPX 5,950 put for $1.80
- Buy SPX 5,900 put for $0.60
- Net credit: $1.20 ($120 per spread)
- **Maximum loss: $50 per point × 50 points = $5,000 minus $120 credit = $4,880 max loss**

**Pros:** Your loss is absolutely capped. No matter what happens — market crash, nuclear war — you can't lose more than $4,880 per spread.

**Cons:** Lower premium collected ($120 vs $180). Over time, this significantly reduces returns. Also, spreads provide great single-day protection but if a crash spans multiple days, you need to keep buying new protection at increasingly expensive prices.

**WealthyOption's warning:** Don't increase your number of contracts just because you now have "defined risk." People who switch from 1 naked put to 5 spreads (thinking "I can afford more risk now!") often end up with MORE total risk than before.

### Level 3: VIX Call Hedge (For Black Swan Protection)

Buy cheap VIX call options as portfolio insurance:

- **Purchase:** Weekly, ~23 DTE, around 5 delta
- **Cost:** Typically $0.20 per call ($20) — very cheap
- **Budget:** ~1.5% of portfolio per year (about $0.03% per week)
- **What it does:** VIX calls explode in value during market panics. They won't fully offset your losses, but they significantly cushion the blow.

**When they pay off:** Think extreme events — flash crashes, geopolitical shocks, pandemic announcements. The kind of events that send VIX from 15 to 60+ in a day.

**When they don't pay off:** Slow, grinding market declines. Normal corrections. They're designed for tail events only.

---

## 11. What Can Go Wrong — Honest Risk Assessment

### Scenario 1: Normal Market Correction (5-10% over weeks)

**Impact:** Mostly fine. Each individual trade adjusts automatically because as IV rises, your 6.5 delta strike moves further from the market. You may take 1-2 losses during the sharpest drop days, but the recovery trades earn fatter premiums.

**Historical example:** WealthyOption was profitable through the March 2020 crash (the third worst day in market history) because his strikes were pushed so far OTM by elevated IV.

### Scenario 2: Flash Crash (5%+ intraday drop)

**Impact:** Could cause a significant single-trade loss if SPX blows through your strike. However, circuit breakers halt trading at -7%, -13%, and -20%. Cash-settled means you don't get stuck with shares.

**Mitigation:** Proper position sizing means even a full loss on one trade doesn't blow up your account. Spreads or VIX calls provide additional protection.

### Scenario 3: Overnight Gap (Market opens significantly lower)

**Impact:** This is the biggest single risk. If the market gaps down past your strike overnight, your option opens deeply ITM and your loss is immediate. You can't close during the gap.

**Mitigation:** 1-4 DTE means maximum 1-2 overnight gaps per trade. At 6.5 delta, your strike is 2-3% below the market (in low IV) or 10-20% below (in high IV). The market would need to gap down 2-3%+ overnight — which has happened but is uncommon.

### Scenario 4: Multi-Day Sustained Crash

**Impact:** The only scenario that can seriously damage this strategy. If SPX drops 10%+ over 2-3 days, multiple consecutive trades take losses. With leverage, this compounds.

**Mitigation:** Reduced leverage (start at 2x, not 4x), VIX call hedges, and the mathematical reality that such events are extremely rare (once every 5-10+ years).

### Scenario 5: Strategy Edge Erodes

**Impact:** The 0DTE boom (57-61% of SPX volume is now 0DTE) means more sellers are competing. If too many people sell short-term premium, the edge may shrink.

**Reality check:** BigERN has been doing this since 2011 with consistent results. The edge hasn't disappeared yet because the fundamental driver (people overpaying for insurance) is behavioral, not structural. But it's worth monitoring.

### Worst-Case Numbers

From backtesting:
- **Worst single trade loss:** 3.2% below strike (on a massive -12% SPX day)
- **Worst drawdown:** ~25-35% of account at 4x leverage (during 2022 bear market)
- **Recovery time:** Drawdowns of 20-30% typically recovered within 2-4 months

---

## 12. Account Requirements

### Minimum Account Size
- **SPX:** $50,000 (practical minimum for 1 contract)
- **XSP:** $25,000 (practical minimum)
- **SPY:** $25,000 (pattern day trader minimum applies)

### Options Permission Level
You need **the highest tier of options approval** from your broker:
- Usually called "Level 4" or "Level 5" — naked/uncovered options
- Requires experience and a significant account balance
- Not all brokers grant this easily

### Margin Type
- **Portfolio margin** is strongly preferred (more efficient buying power, especially if adding calls)
- **Reg-T margin** works for puts only, but is less capital-efficient
- **Cash-secured** is too capital-intensive for SPX (you'd need ~$600K cash per contract)

### Recommended Brokers
- **Interactive Brokers (IBKR):** Most commonly used by SPX options traders. Low commissions, portfolio margin available, good execution. You already have an IBKR account.
- **tastytrade/tastylive:** Popular for options, though their platform is more focused on 45 DTE strategies
- **Schwab/TD Ameritrade (thinkorswim):** Good platform, acceptable commissions

### IBKR Specifics for SPX
- SPX options available under the "Index Options" section
- Commissions: typically $0.65 per contract (negotiable with volume)
- Portfolio margin requires $110K minimum and approval
- XSP available as smaller alternative

---

## 13. Daily Routine & Time Commitment

### Morning Check (~2-3 minutes)

1. **Did yesterday's profit target fill?** Check your positions.
   - If yes → Enter new trade (Rule 6)
   - If no → Do nothing. Your GTC order is working.

2. **Is any position expiring today?**
   - If yes and it's safely OTM (delta < 10): Let it expire, enter new trade near close or tomorrow morning
   - If yes and it's ITM: Handle per Section 8 (hold to expiry, manage buying power for new position)

3. **Enter new trade if needed.** Takes 2 minutes: find 6.5 delta, sell, place GTC.

### During the Day

**Do nothing.** Seriously. The system is designed to be set-and-forget. Watching the screen doesn't help and often hurts (emotional decisions).

### End of Day (~2 minutes, only when a position expires)

1. If an ITM position is expiring, you need to enter your new trade before close (3:30-3:45 PM ET)
2. Make sure you have buying power for the new position
3. If necessary, convert expiring ITM position to a spread to free up buying power

### Total Weekly Time: 20-30 minutes

This is one of the strategy's best features. It's systematic and mechanical. No chart-staring, no news-watching, no agonising over entries.

---

## 14. Performance Expectations

### What the Data Shows

**WealthyOption backtests (2016-2022, ~3,000+ trades):**
- CAGR: ~20-30% at 4x leverage (varies by exact parameters)
- Win rate: ~90-95%
- Worst drawdown: ~25-35%
- Average trade duration: 1-2 days

**BigERN actual results (2025):**
- Total options income: ~$90K
- ~10,000 contracts traded
- Premium capture rate: 74.2% overall
- 1DTE puts: 94% premium capture rate
- 0DTE puts: lower (~60-65%) due to some intraday losses
- 0DTE calls: 57.3% premium capture rate
- No calendar-month losses since 2022 (including through April 2025 volatility spike)

### Realistic Expectations for a New Trader

**Starting with $150K at 2x leverage (1 SPX contract):**
- Expected annual income: ~$15,000-$25,000 (10-17% return)
- Average trade profit: ~$100-$200
- Expected losing trades: ~1-2 per month
- Average loss per losing trade: $1,000-$5,000
- Worst single loss (rare): $5,000-$15,000
- Time to feel comfortable: 2-3 months of consistent trading

**Important:** Past performance ≠ future results. The strategy has a structural edge (variance risk premium), but edges can narrow and black swans can hurt.

---

## 15. Glossary — The Jargon Decoded

| Term | Plain English |
|------|--------------|
| **ATM (At The Money)** | Strike price equals current market price |
| **OTM (Out of The Money)** | Strike is below market for puts (above for calls) — where you want to be |
| **ITM (In The Money)** | Strike is above market for puts — means you're losing |
| **DTE** | Days to expiration — how many days until the option expires |
| **0DTE** | Expires today |
| **Delta** | Rough probability the option expires ITM. Lower = safer for sellers |
| **Gamma** | How fast delta changes. High gamma = position risk changes rapidly |
| **Theta** | Daily time decay. How much money you earn per day from time passing |
| **Vega** | Sensitivity to IV changes. Nearly irrelevant for 1-4 DTE |
| **IV (Implied Volatility)** | Market's expectation of future price movement. Higher = bigger premiums |
| **VIX** | The "fear index" — measures expected S&P 500 volatility over next 30 days |
| **GTC** | Good-Till-Cancelled — your order stays active until it fills or you cancel it |
| **STO** | Sell To Open — opening a new short position |
| **BTC** | Buy To Close — closing an existing short position |
| **Naked/Uncovered** | Selling an option without a hedge — unlimited theoretical risk |
| **Spread** | Combining a short option with a long option to cap risk |
| **Premium** | The cash you receive when selling an option |
| **Strike** | The price level where the option obligation kicks in |
| **Cash-settled** | Settlement is in cash, not shares. SPX is cash-settled |
| **American-style** | Can be exercised any time before expiry (SPY, stocks) |
| **European-style** | Can only be exercised at expiry (SPX) — safer for sellers |
| **Circuit breaker** | Market-wide trading halt triggered at -7%, -13%, -20% drops |
| **Variance risk premium** | The structural tendency for implied volatility to exceed realised volatility — your edge |
| **PCR (Premium Capture Rate)** | % of premium collected that you keep as profit |
| **Portfolio margin** | Advanced margin system using real portfolio risk (more efficient than Reg-T) |
| **Reg-T margin** | Standard margin (less capital-efficient for this strategy) |
| **Notional value** | The total underlying value controlled by the option (SPX × 100 per contract) |
| **Section 1256** | US tax code: 60% of gains taxed as long-term regardless of holding period |

---

## 16. Sources

1. **WealthyOption.com** — Primary source for the 6.5 delta / 1-4 DTE / 70% profit target system. Full trade history, backtests, sizing calculator, and FAQ.
   - https://wealthyoption.com/

2. **EarlyRetirementNow.com — Options Trading Series (Parts 1-14)** — BigERN's multi-year documented strategy. PhD economist, trading SPX puts since 2011. Annual reviews with real P/L.
   - https://earlyretirementnow.com/options/

3. **BigERN's 2025 Review (Part 14)** — Most recent annual review. $90K income from options, 10,000 contracts, strategy details for 0DTE and 1DTE puts and calls.
   - https://earlyretirementnow.com/2026/01/30/options-trading-series-part-14-year-2025-review/

4. **Whisper Trades** — Backtesting platform used by WealthyOption. Allows custom delta/DTE/profit target backtests.
   - https://whispertrades.com/

5. **Option Alpha — WealthyOption Bot Template** — Automated implementation of the strategy.
   - https://optionalpha.com/bots/wealthyoption

6. **Spintwig.com** — Independent backtests of SPX 0DTE and 7DTE short puts at various deltas.
   - https://spintwig.com/all-backtests/

7. **CBOE** — Market data on 0DTE volume, retail participation, and market structure.
   - https://go.cboe.com/0DTE

---

## Quick Reference Card

```
┌─────────────────────────────────────────────┐
│          SPX PUT SELLING CHEAT SHEET         │
├─────────────────────────────────────────────┤
│  Instrument:  SPX (or XSP for small accts)  │
│  Direction:   SELL puts                      │
│  Delta:       6.5 (range 5.5-7.5)           │
│  DTE:         1-4 days (never 0 DTE)        │
│  Profit:      70% (BTC at 30% of premium)   │
│  Stop-loss:   NONE (hold to expiry)          │
│  Frequency:   Continuous (always have a pos) │
│  Sizing:      20% of strike × 100 = max loss│
│  Leverage:    Start at 2x, max 4-5x         │
│  Time/week:   ~20-30 minutes total           │
│                                              │
│  WHEN PROFIT TARGET FILLS → NEW TRADE        │
│  WHEN OPTION EXPIRES → NEW TRADE             │
│  WHEN IN DOUBT → DO NOTHING                  │
│                                              │
│  DON'T: Time entries, use indicators,        │
│         override rules, chase the last 30%   │
└─────────────────────────────────────────────┘
```

---

*This document synthesises the publicly documented strategies of WealthyOption.com and EarlyRetirementNow.com (BigERN). Neither the author of this document nor these sources provide financial advice. Options trading involves significant risk of loss. Paper trade first. Start small. Understand the risks before committing real capital.*
