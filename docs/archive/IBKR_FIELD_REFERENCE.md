# IBKR Field Reference for Manual Trade Entry

This guide explains which IBKR fields to use when manually entering trades.

## Quick Reference Table

| Web Form Field | IBKR Field Name | Format | Example |
|----------------|-----------------|--------|---------|
| Symbol | Underlying | Text | AAPL |
| Strike | Strike | Number | 180.00 |
| Expiration | Expiration | Date | 2025-02-14 |
| Premium | Bid or Mark | Dollar | 0.45 |
| Bid | Bid | Dollar | 0.44 |
| Ask | Ask | Dollar | 0.46 |
| Delta | Delta | Decimal | -0.15 |
| OTM % | (Calculate) | Decimal | 0.12 (for 12%) |
| Stock Price | Underlying Last | Dollar | 204.50 |
| **IV** | **IV Last** | **Decimal** | **0.75 (for 75%)** |
| Volume | Volume | Integer | 450 |
| Open Interest | Open Int | Integer | 1200 |

## Detailed Field Explanations

### Implied Volatility (IV)

**✅ CORRECT FIELD: "IV Last"**
- This is the option's implied volatility percentage
- Shown in IBKR options chain as "IV Last"
- Typically ranges from 20% to 200%+ for most stocks

**❌ WRONG FIELD: "Implied Vol/Hist Vol"**
- This is a RATIO comparing implied volatility to historical volatility
- Used for identifying over/underpriced options
- NOT the same as IV

**How to Enter:**
- IBKR shows: 75% → Enter: **0.75**
- IBKR shows: 107% → Enter: **1.07**
- IBKR shows: 35.5% → Enter: **0.355**

**Example from IBKR:**
```
Option Chain for AAPL
Strike: 180.00
Expiration: Feb 14, 2025
Bid: 0.44
Ask: 0.46
IV Last: 75.2%        ← Use this (enter as 0.752)
Impl Vol/Hist Vol: 1.2 ← Don't use this
Delta: -0.15
```

### Delta

**Field:** Delta
**Format:** Decimal (negative for puts, positive for calls)

**Example:**
- IBKR shows: -0.15 → Enter: **-0.15**
- IBKR shows: 0.25 → Enter: **0.25**

**Note:** Delta changes constantly. Use the value at the time you're evaluating the trade.

### OTM Percentage

**Field:** (Must calculate manually)
**Format:** Decimal (0.12 = 12%)

**Calculation for PUTS:**
```
OTM % = (Stock Price - Strike) / Stock Price

Example:
Stock Price: $204.50
Strike: $180.00
OTM % = (204.50 - 180.00) / 204.50 = 0.1198 ≈ 0.12
```

**Calculation for CALLS:**
```
OTM % = (Strike - Stock Price) / Stock Price

Example:
Stock Price: $204.50
Strike: $220.00
OTM % = (220.00 - 204.50) / 204.50 = 0.0758 ≈ 0.08
```

### Premium

**Recommended:** Use the **Bid** price for naked puts (this is what you'll receive)

**Options:**
- **Bid Price**: What you'll get when selling (conservative)
- **Mark Price**: Midpoint between bid/ask (optimistic)
- **Ask Price**: Don't use this for selling

**Example:**
```
Bid: $0.44
Ask: $0.46
Mark: $0.45

For Premium, enter: 0.44 (use Bid for selling)
```

### Trend

**Field:** Not directly in IBKR
**Source:** Chart analysis or technical indicators

**Valid Values:**
- `uptrend` - Stock moving up consistently
- `downtrend` - Stock moving down
- `sideways` - Ranging/consolidating

**How to Determine:**
1. Look at IBKR chart (6-month timeframe recommended)
2. Check if price is above/below major moving averages
3. Look at recent higher highs/lower lows

## Common Mistakes to Avoid

❌ **Don't mix up IV Last with Impl Vol/Hist Vol**
- IV Last = Implied volatility percentage (use this)
- Impl Vol/Hist Vol = Ratio (don't use this)

❌ **Don't enter percentages as whole numbers**
- Wrong: Entering 75 for 75%
- Right: Entering 0.75 for 75%

❌ **Don't use Ask price as Premium when selling**
- Ask is what you'd pay to buy the option
- Bid is what you receive when selling
- Use Bid or Mark for selling

❌ **Don't forget Delta is negative for puts**
- Puts: Delta ranges from 0 to -1.0
- Calls: Delta ranges from 0 to +1.0

## Finding Fields in IBKR TWS

### Options Chain View
1. Right-click on underlying stock
2. Select "Option Chain" or "Option Trader"
3. Find your target expiration and strike
4. All fields are visible in the chain

### Field Locations in Option Chain
```
AAPL Put Options - Feb 14, 2025
Strike | Bid  | Ask  | Last | Volume | Open Int | IV Last | Delta  | Gamma | Theta
180.00 | 0.44 | 0.46 | 0.45 | 450    | 1200     | 75.2%   | -0.15  | 0.03  | -0.02
       ^Bid  ^Ask         ^Volume  ^Open Int  ^IV Last  ^Delta
```

## Examples

### Example 1: Conservative Naked Put
```
Web Form Entry:
- Symbol: AAPL
- Strike: 180.00
- Expiration: 2025-02-14
- Option Type: PUT
- Premium: 0.44 (using Bid)
- Bid: 0.44
- Ask: 0.46
- Delta: -0.15
- OTM %: 0.12 (calculated: (204.50-180)/204.50)
- Stock Price: 204.50
- IV: 0.752 (IBKR shows 75.2%)
- Volume: 450
- Open Interest: 1200
- Trend: uptrend (from chart analysis)
- Notes: "Strong support at $200, earnings beat expectations"
```

### Example 2: High IV Play
```
Web Form Entry:
- Symbol: NVDA
- Strike: 800.00
- Expiration: 2025-03-21
- Option Type: PUT
- Premium: 8.50
- Bid: 8.50
- Ask: 9.00
- Delta: -0.25
- OTM %: 0.18
- Stock Price: 975.00
- IV: 1.07 (IBKR shows 107% - high IV after earnings)
- Volume: 2500
- Open Interest: 8500
- Trend: sideways (consolidating after big move)
- Notes: "High IV after earnings, expecting IV crush over next 2 weeks"
```

## Summary

**Key Takeaways:**
1. ✅ Use "IV Last" from IBKR, enter as decimal (75% = 0.75)
2. ✅ Use Bid price for Premium when selling options
3. ✅ Delta is negative for puts (-0.15), positive for calls
4. ✅ Calculate OTM % manually: (Stock - Strike) / Stock for puts
5. ✅ All percentages entered as decimals (0.12 = 12%)

**If in doubt, leave optional fields blank!** The system will fetch real-time data during IBKR validation.
