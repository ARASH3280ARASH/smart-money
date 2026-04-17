from __future__ import annotations

"""
FastAPI application.
Serves the REST API and static web dashboard on port 8000.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import backtest, signals, stats, streams, wallets

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Smart Money Analytics",
    description="Multi-chain smart money intelligence system",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ────────────────────────────────────────────────────────────
app.include_router(wallets.router)
app.include_router(signals.router)
app.include_router(stats.router)
app.include_router(streams.router)
app.include_router(backtest.router)

# ── Health check ──────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}

# ── Static files (dashboard) — must be last ───────────────────────────────
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
