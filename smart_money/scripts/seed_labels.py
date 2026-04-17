from __future__ import annotations

"""
One-shot script: seeds known VC/fund/whale wallet labels into the DB.
Run with: python scripts/seed_labels.py

Also updates signals/detector.py evidence text to use labels.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def seed() -> None:
    from config.known_wallets import KNOWN_WALLETS
    from db.init_db import init_db
    from db.models import Wallet
    from db.session import get_db
    from sqlalchemy import select
    from utils.logger import init_logging, get_logger

    init_logging()
    logger = get_logger("seed_labels")
    await init_db()

    count_new = 0
    count_updated = 0

    for address, info in KNOWN_WALLETS.items():
        chain = info.get("chain", "eth")
        label = info.get("label", "")
        tags = info.get("tags", [])

        async with get_db() as db:
            result = await db.execute(
                select(Wallet).where(
                    Wallet.address == address.lower(),
                    Wallet.chain == chain,
                )
            )
            wallet = result.scalar_one_or_none()

            if wallet is None:
                wallet = Wallet(
                    address=address.lower(),
                    chain=chain,
                    label=label,
                    tags=json.dumps(tags),
                )
                db.add(wallet)
                count_new += 1
                logger.info("Created wallet: %s (%s)", label, address[:12])
            else:
                wallet.label = label
                wallet.tags = json.dumps(tags)
                count_updated += 1
                logger.info("Updated label: %s (%s)", label, address[:12])

    logger.info(
        "Seed complete: %d new wallets, %d labels updated",
        count_new, count_updated,
    )


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(seed())
