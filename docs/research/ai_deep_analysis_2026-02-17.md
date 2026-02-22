# AI Deep Analysis — 718 Trades, 180 Days

**Date:** 2026-02-17

---

## The Big Picture

Over the last 6 months, the system placed 718 trades on paper and made $39,255 in simulated profit. It won 91.6% of the time, which is strong. The last 30 days have been even better — 158 trades with higher returns — but that's likely because the market has been calm and trending upward rather than the system getting smarter.

The system has some very specific habits: almost all trades (94.4%) are very short-term, expiring within a week ("0-7 DTE" — days to expiration). It overwhelmingly enters trades on Mondays (72.4%), and it mostly trades when the market is calm ("low volatility"). This works well when conditions are right, but it means the system is operating in a narrow lane — and if market conditions shift, results could deteriorate quickly.

The data paints a clear picture of what works and what doesn't. The system does best when:
- The market is calm (low "VIX" — the fear index)
- Stocks are trending upward ("uptrend")
- It picks options that are a moderate distance from the current stock price (the "10-20% delta" range — how sensitive the option price is to the stock moving)

When all three line up, win rates approach 100%. But the system also wanders into danger zones — selling options too close to the stock price ("high delta"), trading when the market is wild ("extreme volatility"), or trading when stocks are falling ("downtrend"). These trades drag down the overall results badly.

---

## Findings and Recommendations

### 1. Stop selling options too close to the stock price

**The problem:** When the system sells options that are close to the current stock price ("25%+ delta" — meaning there's roughly a 1-in-4 chance the option gets exercised), it only wins 70% of the time and barely makes money (5.34% return). The losses on the 30% of losers nearly wipe out all the income collected ("premium") from winners.

**Compare that to the sweet spot:** Options at a moderate distance from the stock price ("10-20% delta") win 93-100% of the time with returns near 99%. The further away from the stock price you sell, the less you get paid per trade, but you win far more often.

**Why this happens:** Selling closer to the stock price means less safety margin. If the stock drops even a little, your option is in trouble. And since almost all trades expire within a week ("0-7 DTE"), there's no time for the stock to recover.

**Recommendation:** Cap the system at 20% delta. If it does sell closer to the stock price, require a longer time to expiration ("DTE") to give the trade room to breathe.

### 2. Don't trade when the market is panicking

**The problem:** When market fear is extreme ("extreme vol regime"), the system essentially flips a coin — 56% win rate, 0% return. The 16 trades in these conditions made nothing. Even mildly elevated fear ("elevated VIX") drops performance significantly compared to calm markets.

**The numbers:** Calm market ("low VIX") = 100% win rate, 95.65% return. Fearful market ("elevated VIX") = 88% win, 38.80% return. Panicking market ("extreme vol") = 56% win, 0% return.

**Why this is tempting but wrong:** When the market is scared, option buyers pay more ("higher premium"), so it looks like a good time to sell. But the risk of the stock crashing through your option's price ("strike price") goes way up, and the data shows the extra income doesn't compensate for the extra losses.

**Recommendation:** Set a hard ceiling — when the fear index ("VIX") goes above 25, stop opening new trades entirely.

### 3. Use a simple checklist before entering trades

**The idea:** The three best conditions for this strategy are: (1) the stock is trending up ("uptrend"), (2) the market is calm ("low VIX"), and (3) the option is at a moderate distance from the stock price ("10-20% delta"). When all three are true, the system should trade more aggressively. When none are true, it should sit on its hands.

**A simple scoring system:** Before each trade, count how many of these three conditions are met (0 to 3). Scale the number of trades accordingly. Even a rough version of this would avoid the worst trades — the ones in the 2-5% return danger zones that drag down the portfolio.

### 4. Aim for the 10-20% delta sweet spot

**The problem with going too far from the stock price:** You'd think that selling options very far from the stock price ("0-10% delta" — less than a 10% chance of being exercised) would be the safest. Surprisingly, these only win 80% of the time with 81% returns. That's because the income per trade is so tiny (near the $0.30 minimum) that even a small loss wipes out many wins. The gap between what buyers pay and sellers receive ("bid-ask spread") also eats into these small trades.

**The sweet spot:** The 10-15% delta range (93% win, 98.74% return) and 15-20% range (100% win, 99.80% return) give the best balance of safety and income. The system should deliberately target this range.

### 5. The worst losing streak ate 20% of all profits

**The reality check:** The biggest peak-to-trough loss ("max drawdown") was $8,125 — that's 20.7% of the total $39,256 profit. One bad stretch wiped out a fifth of everything earned.

**Why this matters for real money:** The 91.6% win rate hides an important truth: each win is small (the option income collected), but each loss is large (having to buy back the option at a much higher price, or being forced to buy the stock at a loss — "assignment"). With up to 100 positions open at once, a broad market crash could trigger many losses simultaneously.

**Recommendation:** Stress-test the portfolio: what happens if the market drops 5% in a day and 10% of all open positions go wrong at the same time?

### 6. Consider holding trades a bit longer — 1-2 weeks instead of under 1 week

**The finding:** Trades held for 1-2 weeks ("7-14 DTE") have a 100% win rate and 82% return across 36 trades. The dominant under-1-week trades ("0-7 DTE") win 92% of the time with only 52% return. The slightly longer holding period collects more time-based income ("time premium") and gives the trade more room.

**The cliff:** Going beyond 2 weeks ("14-21 DTE") drops to a 25% win rate on 4 trades — possibly because longer trades run into company earnings announcements and other events. So cap any expansion at 2 weeks.

**Recommendation:** Shift 20-30% of trades into the 1-2 week range to diversify and potentially improve returns. Don't go beyond 2 weeks.

### 7. Why do sharp sell-offs perform better than gentle ones?

**The puzzle:** When stocks are in a strong sell-off ("strong downtrend"), the system wins 92% of the time with 64% returns (179 trades). But during a gentle decline ("downtrend"), it only wins 88% with a 2% return (189 trades). This is backwards — sharper drops should be worse for selling options, not better.

**Possible explanations:**
- Sharp sell-offs may trigger higher option prices ("implied volatility"), meaning richer income that more than covers the extra risk
- Sharp sell-offs may coincide with market bottoms where stocks bounce back quickly ("V-recovery")
- The system's trend labels might be wrong — "downtrend" might catch the dangerous early stage of a fall, while "strong downtrend" catches the exhaustion phase where the worst is over

**Recommendation:** Investigate before acting. Trading into sharp sell-offs based on this pattern could be catastrophic if the reason turns out to be temporary.

### 8. Stocks that are rising in momentum are better entry points

**The pattern:** A common technical indicator called RSI measures whether a stock has been going up or down recently (scale of 0-100). The data shows a clear gradient:
- RSI above 70 (stock has strong upward momentum — "overbought"): 94% win, 85% return
- RSI 50-70 (stock is moving up normally — "neutral"): 94% win, 70% return
- RSI 30-50 (stock is drifting down — "low RSI"): 88% win, 26% return
- RSI below 30 (stock has been hammered — "oversold"): 92% win, 51% return

**Why this makes sense:** Selling a put is a bet that the stock won't fall much further. When a stock already has upward momentum (high RSI), you have a natural tailwind. The worst zone is 30-50 — the stock is falling but hasn't hit bottom yet.

**Recommendation:** Avoid entering trades when RSI is between 30-50 (the "falling but not yet bottomed" zone). This has the worst results by far.

### 9. Almost all trades happen on Monday — that's a risk

**The concentration:** 520 of 718 trades (72.4%) are entered on Monday. This probably works because the system captures the passage of time over the weekend ("theta decay" — options lose value as time passes, even over weekends when markets are closed). By selling on Monday, the system collects that weekend time decay as profit.

**The risk:** If something bad happens over a weekend (a geopolitical crisis, a company scandal, a market crash), the system would be entering new trades into a falling market at the worst possible time. All 520 Monday positions would be affected.

**An interesting detail:** Tuesday entries (105 trades) actually have better returns — 90.22% vs Monday's 44.30% — with a similar win rate. Some Monday entries could be deferred by a day for better results.

### 10. Sector data is mostly missing — fix this before drawing conclusions

**The gap:** Only 23 of 718 trades have a sector label (like "Technology" or "Consumer"). The other 695 trades are unclassified. This means any sector-based conclusions are unreliable.

**What we can see:** The 20 Technology trades look great (95% win, 98% return), but that's only 2.8% of all trades. The missing 695 trades might be broad market options (like SPY or QQQ) that don't have sector tags, which would itself be a concentration risk worth understanding.

**Recommendation:** Fix the sector classification to cover at least 80% of trades before making any sector-based decisions.
