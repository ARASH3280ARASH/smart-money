# Smart Money Analytics System

**Full documentation** (quick start, environment reference, HTTP API table, security, and how to pin the repo on your GitHub profile) lives in the repository root: [`README.md`](../README.md).

---

A production-grade multi-chain smart money analytics system that continuously scans blockchain activity, identifies profitable wallets, detects coordinated behavior, and delivers trade-ready signals via Telegram.

---

## Architecture

```
Moralis API → MoralisClient (rate-limited, cached)
    ↓
Ingestion Workers (wallet history, token events)
    ↓
Database (SQLite / SQLAlchemy)
    ↓
Analytics Engine (PnL, ROI, win rate, early entry, scoring)
    ↓
Signal Detector (8 signal types)
    ↓
Telegram Alerts
```

**Background jobs (APScheduler):**
| Job | Cadence | Purpose |
|-----|---------|---------|
| Wallet Ingestion | 30s | Scan top-100 wallets for new trades |
| Token Event Scan | 60s | Detect coordinated buys, new liquidity |
| Score Update | 5 min | Recompute wallet scores |
| Top-100 Sync | 10 min | Re-rank all wallets |
| Graph Clustering | 30 min | DBSCAN community detection |
| Wallet Discovery | 20 min | Seed new wallets from trending tokens |

---

## Setup

### 1. Prerequisites
- Python 3.11+
- pip

### 2. Install dependencies
```bash
cd smart_money
pip install -r requirements.txt
```

### 3. Configure environment
Copy `.env.example` to `.env` and fill in:
```bash
MORALIS_API_KEY=your_key_here
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

**Getting your Telegram Chat ID:**
1. Message your bot: `/start`
2. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Find the `chat.id` field in the response

### 4. Run
```bash
cd smart_money
python main.py
```

---

## Signal Types

| Signal | Trigger | Use Case |
|--------|---------|----------|
| `SMART_WALLET_BUY` | Score>70 wallet buys token | Copy trade |
| `COORDINATED_BUY` | 3+ smart wallets buy same token in 4h | High confidence entry |
| `EARLY_ENTRY` | Smart wallet enters first 10% of move | Early momentum |
| `PRE_PUMP_PATTERN` | Historically accurate wallets buying again | Pattern repeat |
| `NEW_LIQUIDITY` | Significant new DEX liquidity | New token watch |
| `CLUSTER_BUY` | Wallet cluster buys together | Network signal |
| `SMART_EXIT` | Top wallet selling near peak | Exit warning |
| `WHALE_MOVE` | Large capital movement (>$50k) | Whale tracking |

---

## Wallet Scoring (0-100)

| Factor | Weight | Logic |
|--------|--------|-------|
| Win rate | 20% | >65% win rate = full score |
| Realized PnL | 20% | Log-scaled $1k → $500k |
| ROI consistency | 15% | >20% avg ROI per trade |
| Early entry | 20% | Buys in first 10% of move |
| Smart exits | 10% | Sells within 20% of peak |
| Coordination | 5% | Trades with other high-scorers |
| Capital size | 5% | Trade sizes $10k → $100k |
| Recency | 5% | Active in last 30 days |

---

## Sample Telegram Alert

```
🤝 SMART MONEY SIGNAL
Type: COORDINATED_BUY  |  Score: 87/100
Token: $PEPE
Chain: ETH
Wallets: 0x1a2b...3c4d, 0x5e6f...7g8h (+2 more)
What: 4 smart wallets (avg score 82) bought within 3h, total $234,000
Why: Historical pattern: 3/4 wallets previously bought before 2x+ moves
Evidence: 0x1a2b... score=91 pnl=$67,000 | 0x5e6f... score=85 pnl=$43,000
🔥 Confidence: HIGH
2026-04-13 14:32 UTC
```

---

## Database Schema

| Table | Purpose |
|-------|---------|
| `wallets` | All tracked wallets with scores |
| `wallet_metrics` | PnL, ROI, win rate per wallet |
| `trades` | Individual swap/trade history |
| `tokens` | Token metadata and prices |
| `token_events` | Detected token-level events |
| `signals` | All generated signals |
| `wallet_relationships` | Co-trade graph edges |
| `api_usage` | CU cost tracking |

---

## Project Structure

```
smart_money/
├── config/          # Settings (Pydantic) + chain configs
├── db/              # SQLAlchemy models + session
├── clients/         # Moralis API + Telegram clients
├── ingestion/       # Wallet/token data fetchers
├── analytics/       # Scoring, PnL, token analysis
├── graph/           # Relationship graph + DBSCAN clustering
├── signals/         # Signal detection + formatting
├── alerts/          # Telegram alert delivery
├── workers/         # APScheduler jobs
├── utils/           # Cache, rate limiter, logger
├── tests/           # Unit tests
└── main.py          # Entry point
```

---

## Running Tests

```bash
cd smart_money
python -m pytest tests/ -v
```

---

## Tuning & Next Steps

1. **Add more seed tokens** in `.env` → `SEED_TOKENS=0xabc...,0xdef...`
2. **Adjust score threshold** → `SMART_WALLET_SCORE_THRESHOLD=75` for stricter filtering
3. **Upgrade to PostgreSQL** → change `DB_PATH` to `postgresql+asyncpg://...` in `.env`
4. **Add Solana** → set `ENABLED_CHAINS=eth,bsc,polygon,base,solana`
5. **Backtesting** → query historical signals from `signals` table and cross-reference price data
6. **Custom wallet labels** → update `wallet.label` in DB for known VCs/funds/whales
7. **Dashboard** → serve `/metrics` endpoint with FastAPI for a minimal web view
8. **Streams API** → replace polling with Moralis Streams for real-time push events

---

## API Cost Guide (CU estimates)

| Operation | CU cost | Frequency |
|-----------|---------|-----------|
| Wallet history | 5 CU | 30s per top wallet |
| PnL summary | 10 CU | 10 min per wallet |
| Token price | 2 CU | 60s per active token |
| Token top traders | 10 CU | 20 min per discovery |
| Discovery wallets | 10 CU | 20 min per chain |

On Starter plan (1000 CU/s), scanning 100 wallets every 30s = ~17 CU/s. Well within limits.

---

## Assumptions

- Moralis plan: Starter (1000 CU/s). Adjust `MORALIS_PLAN` if different.
- SQLite is used for local deployment. Zero-server setup.
- Telegram `CHAT_ID` must be set manually after first `/start` message to the bot.
- Initial wallet seeds come from Moralis' top profitable wallets per trending token.
- Solana support is included but uses a separate Moralis gateway endpoint.
