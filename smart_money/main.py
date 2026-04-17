from __future__ import annotations

"""
Smart Money Analytics System — Entry Point
Run with: python main.py

Starts:
  1. Database schema init
  2. Wallet seeding (if needed)
  3. Known wallet label seeding
  4. Initial enrichment pass
  5. Moralis Stream registration
  6. APScheduler background workers
  7. FastAPI / uvicorn web server on :8000
"""

import asyncio
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


async def startup() -> tuple:
    from utils.logger import init_logging
    init_logging()

    from utils.logger import get_logger
    logger = get_logger("main")
    logger.info("=" * 60)
    logger.info("Smart Money Analytics System starting...")
    logger.info("=" * 60)

    from config.settings import get_settings
    settings = get_settings()

    if not settings.moralis_api_key:
        logger.error("MORALIS_API_KEY not set in .env — aborting")
        sys.exit(1)
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram alerts disabled")
    if not settings.telegram_chat_id:
        logger.warning("TELEGRAM_CHAT_ID not set — alerts cannot be delivered")

    logger.info("Database: %s", "PostgreSQL" if settings.is_postgres else "SQLite")
    logger.info("Chains: %s", settings.chains)
    logger.info("CU budget: %d/s | API port: %d", settings.cu_per_second, settings.api_port)

    # Init database schema
    from db.init_db import init_db
    await init_db()

    # Seed known wallet labels
    try:
        from scripts.seed_labels import seed as seed_labels
        await seed_labels()
    except Exception as e:
        logger.warning("Label seeding partial: %s", e)

    # Seed wallets from discovery if DB is empty
    from workers.top100_sync import maybe_seed_wallets, run_seed_from_moralis_discovery
    await maybe_seed_wallets()
    await run_seed_from_moralis_discovery(settings.chains)

    # Initial enrichment
    from workers.top100_sync import run_top100_sync
    logger.info("Running initial wallet enrichment (may take a moment)...")
    try:
        await run_top100_sync()
    except Exception as e:
        logger.warning("Initial enrichment partial: %s", e)

    # Register Moralis Stream
    if settings.webhook_base_url:
        try:
            from clients.streams_client import get_streams_client
            sc = get_streams_client()
            stream_id = await sc.ensure_stream(chains=settings.chains)
            if stream_id:
                logger.info("Moralis Stream active: %s", stream_id)
            else:
                logger.warning("Moralis Stream not registered (no stream_id)")
        except Exception as e:
            logger.warning("Stream registration failed: %s", e)
    else:
        logger.info(
            "WEBHOOK_BASE_URL not set — Moralis Streams disabled. "
            "Polling mode active."
        )

    # Startup Telegram notification
    if settings.telegram_bot_token and settings.telegram_chat_id:
        from alerts.telegram_alert import send_startup_notification
        try:
            await send_startup_notification()
        except Exception as e:
            logger.warning("Startup notification failed: %s", e)

    # Start scheduler
    from workers.scheduler import setup_and_start_scheduler
    scheduler = await setup_and_start_scheduler()

    logger.info("All systems live on port %d", settings.api_port)
    return scheduler, settings


async def main() -> None:
    scheduler, settings = await startup()

    from utils.logger import get_logger
    logger = get_logger("main")

    # Start uvicorn server
    import uvicorn
    from api.app import app

    uv_config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        loop="none",       # use existing asyncio loop
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(uv_config)

    # Graceful shutdown
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _handle_signal():
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except (NotImplementedError, OSError):
            pass  # Windows may not support all signal types

    async def _scheduler_keepalive():
        while not stop_event.is_set():
            await asyncio.sleep(1)

    async def _uvicorn_serve():
        await server.serve()
        stop_event.set()

    try:
        await asyncio.gather(
            _uvicorn_serve(),
            _scheduler_keepalive(),
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        logger.info("Shutting down...")
        server.should_exit = True

        from workers.scheduler import stop_scheduler
        await stop_scheduler()

        from clients.moralis import get_moralis_client
        await get_moralis_client().close()

        from clients.telegram_client import get_telegram_client
        await get_telegram_client().close()

        from clients.streams_client import get_streams_client
        await get_streams_client().close()

        logger.info("Shutdown complete")


if __name__ == "__main__":
    if sys.platform == "win32":
        # Use ProactorEventLoop for subprocess support on Windows
        # (asyncio.WindowsProactorEventLoopPolicy deprecated in 3.14+)
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
        loop.close()
    else:
        asyncio.run(main())
