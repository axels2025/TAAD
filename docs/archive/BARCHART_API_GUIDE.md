# Barchart API Guide for Trading Agent

## Overview

This document helps you select the right Barchart OnDemand API tier and explains how the trading agent uses it.

## What is Barchart OnDemand?

Barchart OnDemand is a market data API service that provides:
- Real-time and delayed market data
- **Options screener** (what we need)
- Historical data
- Futures, stocks, ETFs, and options data

**Website:** https://www.barchart.com/ondemand

## API Tiers Comparison

### Free Trial
- **Cost:** Free for 30 days
- **Rate Limits:** 400 queries/day
- **Data Delay:** 15-minute delayed
- **Best For:** Testing and development

**Recommendation:** Start here to test the integration

### GetHistory Plan
- **Cost:** $29/month
- **Rate Limits:** 5,000 queries/day
- **Data Delay:** 15-minute delayed
- **Best For:** Basic historical analysis

**Recommendation:** Not suitable - we need real-time options data

### onDemand Basic
- **Cost:** $99/month
- **Rate Limits:** 10,000 queries/day
- **Data Delay:** Real-time for US equities
- **Options Data:** Yes, includes options screener
- **Best For:** Small-scale automated trading

**Recommendation:** ⭐ **START HERE** for live trading

### onDemand Professional
- **Cost:** $299/month
- **Rate Limits:** 50,000 queries/day
- **Data Delay:** Real-time
- **Options Data:** Yes, full access
- **Best For:** Professional traders, higher frequency

**Recommendation:** Upgrade to this if you scan multiple times per day

### Enterprise
- **Cost:** Custom pricing
- **Rate Limits:** Unlimited (custom)
- **Data Delay:** Real-time
- **Best For:** Institutional use

**Recommendation:** Not needed for this use case

## What Tier Do You Need?

### For Testing & Development
**Free Trial** (30 days free)
- Perfect for testing the integration
- 400 queries/day is enough for ~20-30 scans per day
- No cost risk

### For Live Trading
**onDemand Basic ($99/month)**
- 10,000 queries/day
- Real-time data
- Sufficient for 1-2 scans per day
- Cost-effective for a single trader

### Why onDemand Basic is Recommended

**Daily Usage Estimate:**
- 1 scan per day = ~5-10 API queries
- Market data for validation = ~10-20 queries per scan
- **Total: ~30-50 queries per day**

With a 10,000 query limit, you have **plenty of headroom** for:
- Multiple scans per day
- Re-scans during market hours
- Historical lookbacks
- Error retries

## API Endpoint We Use

### `getOptionsScreener` Endpoint

**What it does:**
Scans the entire US options market server-side and returns contracts matching your criteria.

**Example Request:**
```
GET https://ondemand.websol.barchart.com/getOptionsScreener.json
?apikey=YOUR_API_KEY
&optionType=put
&minDTE=0
&maxDTE=30
&minVolume=250
&minOpenInterest=250
&minDelta=-0.50
&maxDelta=-0.10
&minPrice=0.20
&fields=delta,gamma,theta,vega,bid,ask,volume,openInterest,volatility
&limit=100
```

**Example Response:**
```json
{
  "status": {
    "code": 200,
    "message": "Success"
  },
  "results": [
    {
      "underlyingSymbol": "AAPL",
      "symbol": "AAPL250214P00170000",
      "strike": 170.00,
      "expirationDate": "2025-02-14",
      "type": "put",
      "lastPrice": 175.50,
      "optionPrice": 0.45,
      "bid": 0.44,
      "ask": 0.46,
      "delta": -0.15,
      "volume": 450,
      "openInterest": 1200,
      "volatility": 0.35
    },
    ...
  ]
}
```

## How Our Trading Agent Uses Barchart

### Two-Step Workflow

**Step 1: Barchart Scan (Fast)**
```
┌─────────────────────────────┐
│  Barchart API               │
│  Single API call            │
│  Scans ENTIRE US market     │
│  Server-side filtering      │
│  Returns ~50-100 candidates │
└─────────────────────────────┘
              ↓
    ~50-100 candidates
```

**Step 2: IBKR Validation (Accurate)**
```
┌─────────────────────────────┐
│  IBKR API                   │
│  Real-time bid/ask          │
│  Margin requirements        │
│  Trend validation           │
│  Returns ~10-20 trades      │
└─────────────────────────────┘
              ↓
    ~10-20 validated trades
```

### Why This is Fast

**Old System (IBKR only):**
- Check ~70 symbols (LIQUID_UNIVERSE)
- Fetch chain for each = 70 API calls
- Qualify each option = 100s of API calls
- Get premiums = 100s more calls
- **Total: ~500-1000 API calls, 5-10 minutes**

**New System (Barchart + IBKR):**
- Barchart scan = 1 API call (2-3 seconds)
- IBKR validation of 50 candidates = ~50 API calls (30-60 seconds)
- **Total: ~51 API calls, under 1 minute**

**Speed improvement: 10-20x faster**

## Getting Started

### Step 1: Sign Up for Free Trial
1. Go to https://www.barchart.com/ondemand
2. Click "Get Started Free"
3. Fill out registration form
4. Confirm email

### Step 2: Get Your API Key
1. Log in to your account
2. Go to "My Account" → "API Access"
3. Copy your API key (looks like: `a17fab99f94d0442d7ef4a559ab7a0e4`)

### Step 3: Add to .env File
```bash
BARCHART_API_KEY=your_api_key_here
```

### Step 4: Test the Integration
```bash
python -m src.cli.main scan
```

### Step 5: Upgrade to Paid Plan (After Testing)
Once you've tested and confirmed it works:
1. Go to Billing in your Barchart account
2. Subscribe to **onDemand Basic ($99/month)**
3. Your API key remains the same

## Important Notes

### Rate Limiting
- Barchart enforces rate limits per plan
- Free trial: 400/day
- onDemand Basic: 10,000/day
- Our scanner caches results, so re-running won't use extra queries

### Data Delay
- Free trial: 15-minute delay (not suitable for live trading)
- onDemand Basic: Real-time (required for live trading)

### Fair Usage
- Don't hammer the API (we don't - we scan once per session)
- Cache results when possible (we do this automatically)
- Respect rate limits (built into error handling)

## Support & Documentation

### Barchart Resources
- **Main Docs:** https://www.barchart.com/ondemand/api
- **Options Screener Docs:** https://www.barchart.com/ondemand/api/getOptionsScreener
- **Support Email:** ondemand@barchart.com
- **Support Hours:** Mon-Fri, 9am-5pm CT

### Common Questions

**Q: Can I use the free trial for paper trading?**
A: Yes, but data is 15-minute delayed. For realistic paper trading, upgrade to onDemand Basic.

**Q: What if I hit my rate limit?**
A: The scanner will show an error. You can either wait until the next day (limits reset daily) or upgrade to a higher tier.

**Q: Can I downgrade later?**
A: Yes, Barchart allows plan changes. You can start with Professional and downgrade to Basic if you don't need the extra queries.

**Q: Is there a commitment?**
A: No long-term contracts. Month-to-month billing, cancel anytime.

## Next Steps

1. **Now:** Sign up for free trial
2. **This week:** Test integration with paper trading
3. **Before going live:** Upgrade to onDemand Basic ($99/month)
4. **After 1 month:** Evaluate if you need Professional tier (more scans/day)

---

**Recommendation Summary:**
- **Testing:** Free Trial (30 days)
- **Live Trading:** onDemand Basic ($99/month) ⭐
- **High Frequency:** onDemand Professional ($299/month)

**Questions?** Contact Barchart support at ondemand@barchart.com
