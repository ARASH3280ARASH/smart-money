# Smart Money Analytics

Multi-chain **on-chain intelligence**: ingest wallet and token activity through [Moralis](https://moralis.io/), persist it in **SQLite** (or PostgreSQL), run **analytics and scoring**, apply **graph-style clustering** (DBSCAN) to surface coordinated flows, detect **signals**, expose a **FastAPI** service with OpenAPI docs, and optionally push **Telegram** alerts.

**Repository:** [github.com/ARASH3280ARASH/smart-money](https://github.com/ARASH3280ARASH/smart-money)

> **Disclaimer:** This software is for research and engineering education. It is not investment advice. On-chain metrics can be wrong, incomplete, or manipulated. Use at your own risk.

---

## Contents

- [Highlights](#highlights)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Environment variables](#environment-variables)
- [HTTP API](#http-api)
- [Background jobs](#background-jobs)
- [Signals](#signals)
- [Wallet scoring](#wallet-scoring)
- [Database](#database)
- [Moralis Streams (webhooks)](#moralis-streams-webhooks)
- [Tests](#tests)
- [Operations and cost](#operations-and-cost)
- [Security](#security)
- [Pinning this repo on your GitHub profile](#pinning-this-repo-on-your-github-profile)
- [License](#license)

---

## Highlights

| Area | What this project does |
|------|-------------------------|
| **Blockchain** | Tracks configurable EVM chains, trades, token events, and wallet graphs. |
| **Analytics / ML-style** | Wallet scores from multiple factors; **DBSCAN** clustering on relationships for “smart cluster” behaviour. |
| **Signals** | Rule-based detector for coordinated buys, whale moves, early entries, liquidity events, and more. |
| **Product surface** | REST JSON API, Swagger/ReDoc, static dashboard under `/`, optional Telegram delivery. |

---

## Architecture

```
                    ┌─────────────────┐
                    │   Moralis API   │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
       ┌────────────┐ ┌────────────┐ ┌──────────────┐
       │  Ingestion │ │  Streams   │ │  Rate limit   │
       │  workers   │ │  webhook   │ │  + cache      │
       └──────┬─────┘ └──────┬─────┘ └───────┬──────┘
              │              │               │
              └──────────────┼───────────────┘
                             ▼
                    ┌─────────────────┐
                    │  SQLAlchemy DB  │
                    │ SQLite / Postgres│
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
  ┌─────────────┐    ┌─────────────┐     ┌──────────────┐
  │  Analytics  │    │    Graph    │     │   Signals    │
  │ PnL, scores │    │ DBSCAN, etc │     │  detector    │
  └──────┬──────┘    └──────┬──────┘     └──────┬───────┘
         │                  │                    │
         └──────────────────┼────────────────────┘
                            ▼
                   ┌────────────────┐
                   │ FastAPI :8000  │
                   │ + static UI    │
                   └────────┬───────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
       ┌─────────────┐            ┌─────────────┐
       │  Telegram   │            │   Client    │
       │   alerts    │            │   / browser │
       └─────────────┘            └─────────────┘
```

---

## Tech stack

- **Python 3.11+**, asyncio  
- **FastAPI**, **Uvicorn**  
- **SQLAlchemy** (async), **aiosqlite** / optional **asyncpg**  
- **APScheduler** for periodic jobs  
- **Pydantic Settings** for configuration  
- **Moralis** REST (and optional Streams webhooks)  
- **Telegram** Bot API for notifications  
- **pytest** for unit tests  

---

## Repository layout

Application code lives under **`smart_money/`** (see that folder’s own README for a shorter duplicate of some sections).

```
smart_money/
├── config/           # Pydantic settings, chains, known wallets
├── db/               # Models, session, schema init
├── clients/          # Moralis, Telegram, Streams helper
├── ingestion/        # Wallet / token fetchers
├── analytics/        # Scoring, PnL, token + wallet analytics, backtester
├── graph/            # Relationships + DBSCAN clustering
├── signals/          # Detection + formatting
├── alerts/           # Telegram delivery
├── workers/          # Scheduler + ingestion loops
├── api/              # FastAPI app, routes, static dashboard
├── scripts/          # e.g. label seeding
├── tests/
├── main.py           # Entry point (DB init, workers, uvicorn)
├── requirements.txt
├── .env.example
└── README.md
```

---

## Requirements

- Python **3.11+**  
- A **Moralis** API key  
- Optional: **Telegram** bot token and chat ID for alerts  
- Optional: public **HTTPS** URL + Moralis Streams secret for webhook mode  

---

## Quick start

```bash
git clone https://github.com/ARASH3280ARASH/smart-money.git
cd smart-money/smart_money
pip install -r requirements.txt
copy .env.example .env   # Windows: use copy; Unix: cp
# Edit .env — set at least MORALIS_API_KEY
python main.py
```

Then open:

- **Dashboard:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)  
- **Swagger:** [http://127.0.0.1:8000/api/docs](http://127.0.0.1:8000/api/docs)  
- **ReDoc:** [http://127.0.0.1:8000/api/redoc](http://127.0.0.1:8000/api/redoc)  
- **Health:** [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)  

---

## Environment variables

| Variable | Purpose | Default / notes |
|----------|---------|-----------------|
| `MORALIS_API_KEY` | Moralis authentication | **Required** for live ingestion |
| `TELEGRAM_BOT_TOKEN` | Bot for alerts | Optional |
| `TELEGRAM_CHAT_ID` | Destination chat | Optional |
| `DB_PATH` | SQLite file path | `smart_money.db` |
| `USE_POSTGRES` / `POSTGRES_URL` | Use PostgreSQL instead of SQLite | See `config/settings.py` |
| `ENABLED_CHAINS` | Comma-separated chain keys | e.g. `eth,bsc,polygon,base` |
| `MORALIS_PLAN` | `starter` / `pro` / `business` | Drives CU budget assumption |
| `SCAN_INTERVAL_WALLETS` | Wallet poll interval (s) | `30` |
| `SCAN_INTERVAL_TOKENS` | Token scan interval (s) | `60` |
| `SCORE_UPDATE_INTERVAL` | Score recompute (s) | `300` |
| `TOP100_SYNC_INTERVAL` | Top wallet sync (s) | `600` |
| `GRAPH_UPDATE_INTERVAL` | Graph job (s) | `1800` |
| `SMART_WALLET_SCORE_THRESHOLD` | Minimum “smart” score | `70` |
| `COORDINATED_BUY_MIN_WALLETS` | Wallets in window | `3` |
| `COORDINATED_BUY_WINDOW_HOURS` | Time window | `4` |
| `WHALE_MOVE_MIN_USD` | Whale notional threshold | `50000` |
| `TOP_WALLET_COUNT` | Ranked wallets to keep hot | `100` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |
| `LOG_FILE` | Log file path | `logs/smart_money.log` |
| `SEED_TOKENS` | Discovery seed contracts | WETH on ETH in example |
| `API_PORT` / `API_HOST` | Uvicorn bind | `8000`, `0.0.0.0` |
| `WEBHOOK_BASE_URL` | Public base URL for Streams | Optional |
| `STREAMS_SECRET` | Moralis webhook HMAC secret | Optional |
| `MORALIS_STREAM_ID` | Persisted stream id | Optional |

Copy from **`.env.example`** and adjust for your environment.

---

## HTTP API

| Prefix / path | Role |
|----------------|------|
| `GET /health` | Liveness |
| `/api/wallets` | Wallet queries and rankings |
| `/api/signals` | Signal history / filters |
| `/api/stats` | Aggregate statistics |
| `/api/backtest` | Backtest-related endpoints |
| `POST /streams/webhook` | Moralis Streams delivery (signature optional via `STREAMS_SECRET`) |
| `/` | Static dashboard (HTML) |
| `/api/docs`, `/api/redoc` | OpenAPI UIs |

Exact query parameters are described in the interactive OpenAPI UI.

---

## Background jobs

| Job | Default cadence | Role |
|-----|-----------------|------|
| Wallet ingestion | 30s | Refresh top wallets |
| Token scan | 60s | Token events and coordination hints |
| Score update | 5 min | Recompute wallet scores |
| Top-100 sync | 10 min | Re-rank leaderboard |
| Graph clustering | 30 min | DBSCAN on wallet relationships |
| Wallet discovery | 20 min | Seed from trending tokens |

Intervals are driven by the environment variables above.

---

## Signals

| Signal | Idea |
|--------|------|
| `SMART_WALLET_BUY` | High-score wallet opens a position |
| `COORDINATED_BUY` | Several smart wallets buy same asset in a window |
| `EARLY_ENTRY` | Entry early in a price move |
| `PRE_PUMP_PATTERN` | Wallets with favourable history repeat behaviour |
| `NEW_LIQUIDITY` | Notable liquidity change |
| `CLUSTER_BUY` | Graph cluster acts together |
| `SMART_EXIT` | Strong wallet reduces exposure |
| `WHALE_MOVE` | Large notional transfer |

---

## Wallet scoring

Scores are **0–100** and combine win rate, realised PnL, ROI consistency, early entry, exit timing, coordination with other strong wallets, capital size, and recency. Weights are documented in `smart_money/README.md` and implemented under `analytics/scoring.py`.

---

## Database

Main tables include **`wallets`**, **`wallet_metrics`**, **`trades`**, **`tokens`**, **`token_events`**, **`signals`**, **`wallet_relationships`**, and **`api_usage`**. See `db/models.py` for the canonical schema.

---

## Moralis Streams (webhooks)

If **`WEBHOOK_BASE_URL`** is set to a URL that reaches this service (typically HTTPS in production), the app can register and receive **Moralis Streams** on `POST /streams/webhook`. Set **`STREAMS_SECRET`** so payloads can be HMAC-verified. Without a public webhook URL, the system stays on **polling** mode.

---

## Tests

```bash
cd smart_money
python -m pytest tests/ -v
```

---

## Operations and cost

Moralis usage is measured in **compute units (CU)**. Defaults assume a **Starter**-class budget (`MORALIS_PLAN=starter`). Tune scan intervals and `TOP_WALLET_COUNT` if you hit rate limits. See **`smart_money/README.md`** for a rough CU table.

---

## Security

- **Never commit** `.env`, API key text files, or database dumps with personal data.  
- This repository’s **`.gitignore`** excludes common secret paths; double-check before `git add`.  
- Rotate any API token that has been pasted into chat logs, CI logs, or screenshots.  
- Restrict who can reach **`/streams/webhook`** (firewall, reverse proxy, IP allow list).

---

## Pinning this repo on your GitHub profile

GitHub does **not** expose an official API to pin repositories to a personal profile. After the repository is **public**:

1. Open **[github.com/ARASH3280ARASH](https://github.com/ARASH3280ARASH)** while logged in as that user.  
2. Click **Customize your pins** on the pinned repositories row.  
3. Select **smart-money**, then **Save pins**.

Official help: [Pinning items to your profile](https://docs.github.com/en/account-and-profile/how-tos/profile-customization/pinning-items-to-your-profile).

---

## License

Specify your license in a `LICENSE` file when you choose one (for example MIT or Apache-2.0). Until then, all rights are reserved unless you state otherwise.

---

## Author

**[@ARASH3280ARASH](https://github.com/ARASH3280ARASH)** — smart money, on-chain analytics, and automation experiments.
