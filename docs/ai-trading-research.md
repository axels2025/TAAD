# AI in Naked Put Option Selling Systems

## 1. AI for Entry Timing

- **ML classification models** (Random Forest, XGBoost, SVM) can predict probability of a put expiring OTM given features: underlying price relative to moving averages, RSI, put/call ratio, term structure slope, days to expiry, delta, and distance from support levels
- **Key insight**: Frame it as a classification problem — "will this strike remain OTM by expiry?" — not a price prediction problem
- **Strike/expiry selection features**: IV rank/percentile, IV skew steepness, historical probability of touching, earnings date proximity, sector correlation, and liquidity (bid-ask spread)
- **Practical approach**: Train on historical options chains (CBOE DataShop, OptionMetrics, or Polygon.io) to predict P(expire OTM) and expected return per unit of risk

## 2. Volatility Forecasting (The Core Edge)

The entire edge in selling puts comes from **implied volatility overpricing realized volatility** (the Volatility Risk Premium):

- **GARCH + ML hybrids**: Combine GARCH with LSTM/GRU neural networks — consistently outperforms pure econometric models
- **HAR-RV + XGBoost**: Strong baseline model
- **LSTM/GRU**: Best for capturing volatility clustering and regime changes
- **IV vs RV spread prediction**: When spread is wide → sell aggressively. When narrow → reduce exposure
- **Features**: Intraday RV, VIX term structure, put/call skew, volume-weighted IV changes, macro indicators

## 3. Risk Management

- **AI-driven position sizing**: Model on portfolio Greeks + market conditions to dynamically size
- **Anomaly detection** (Isolation Forest, autoencoders) to detect regime shifts before they hit positions
- **Portfolio heat**: Track margin utilization (<50%), worst-case loss at multiple sigmas, position correlation
- **Dynamic stop-loss**: ML model predicts P(assignment) in real-time, close when probability crosses threshold
- **Kelly Criterion enhanced with ML**: Use model confidence to scale Kelly fraction per trade

## 4. Sentiment Analysis

- **NLP for catalyst avoidance** — monitor SEC filings, earnings transcripts (FinBERT), social media
- **Pre-earnings filter**: Never sell naked puts through earnings
- **News event detection**: NER + sentiment scoring on real-time news feeds
- **Tools**: FinBERT, VADER, LLMs for nuanced analysis

## 5. Reinforcement Learning

- **Environment**: State (positions, Greeks, equity, IV rank, regime), Actions (open/close/roll/wait), Reward (Sharpe-weighted P&L with drawdown penalty)
- **Best algorithms**: PPO (most stable), SAC (good exploration), DQN (simpler)
- **Frameworks**: Stable Baselines3, RLlib, FinRL

## 6. Tools & Frameworks

### Open Source
- **QuantConnect (LEAN)** — full options backtesting + live trading
- **FinRL** — RL for financial trading
- **Stable Baselines3** — production RL algorithms
- **QuantLib** — options pricing and Greeks
- **VectorBT** — fast vectorized backtesting
- **TradingAgents** — multi-agent LLM trading (LangGraph-based)

### Commercial
- **Polygon.io** — affordable real-time + historical options data API
- **ORATS** — options analytics built for options sellers
- **Interactive Brokers API** — most complete broker API for options
- **Tastytrade API** — well-suited for options selling

### Data Sources
- CBOE DataShop, Polygon.io, OptionMetrics, Theta Data, FirstRate Data

## 7. Backtesting with AI

- Walk-forward optimization to prevent overfitting
- Synthetic data (GANs/VAEs) for tail event stress testing
- Bayesian optimization (Optuna) for parameter tuning
- **Pitfalls**: Survivorship bias, look-ahead bias, unrealistic fills, ignoring assignment risk

## 8. Real-World Examples

- **CBOE PutWrite Index (PUT)**: Matched S&P returns with lower volatility — proves systematic put selling works
- **Tastytrade research**: 30 delta, 45 DTE, manage at 50% profit — solid rule-based baseline
- **LJM Preservation Fund**: Blew up Feb 2018 (Volmageddon) — cautionary tale about tail risk
- **SSRN (Joshi et al., 2024)**: ML-enhanced options selling outperformed vanilla in Sharpe ratio

## 9. The Agentic Architecture

```
┌─────────────────────────────────────────────────┐
│              ORCHESTRATOR AGENT                  │
├─────────────┬──────────────┬────────────────────┤
│  ANALYST    │  RISK MGR    │   EXECUTION        │
│  • Vol model│  • Position  │   • Order routing  │
│  • Sentiment│    sizing    │   • Roll mgmt      │
│  • Entry    │  • Greeks    │   • Profit-take     │
│    signals  │  • Drawdown  │   • Stop execution  │
├─────────────┴──────────────┴────────────────────┤
│              EVOLUTION AGENT                     │
│  • Analyze results → Hypothesize → Backtest     │
│  • A/B test parameters → Deploy improvements    │
└─────────────────────────────────────────────────┘
```

### Self-Improving Loop
1. Trade → Monitor → Record → Analyze → Hypothesize → Backtest → Deploy → Repeat

### Implementation Path
1. **Rule-based**: 20-30 delta, 30-45 DTE, IV rank > 50%, manage at 50% profit
2. **Add ML**: Vol forecast model + sentiment screening
3. **Add RL**: Learn optimal delta/DTE/management via simulation
4. **Add agentic**: LLM agents analyze performance, propose improvements
5. **Human-in-the-loop**: Approval gates always maintained

### Critical Guardrails
- Max margin utilization: 50% hard cap
- Per-position: No single underlying > 5% notional
- Correlation: Max 3 positions same sector
- Kill switch: Human can halt instantly
- Drawdown breaker: Close all if >10% weekly loss
- No earnings plays: Auto-exclude within 7 days
- Gradual scaling: Start 10% capital, scale after 6 months live

### Tech Stack
- **Orchestration**: LangGraph or CrewAI
- **Broker**: Interactive Brokers (ib_insync) or Tastytrade API
- **Data**: Polygon.io
- **ML**: XGBoost + LSTM + Stable Baselines3
- **DB**: TimescaleDB for time-series
- **Monitoring**: Grafana dashboards
