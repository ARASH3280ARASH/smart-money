from __future__ import annotations

import asyncio

from db.models import Base
from db.session import get_engine
from utils.logger import get_logger

logger = get_logger(__name__)


async def init_db() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema initialized")


if __name__ == "__main__":
    asyncio.run(init_db())
