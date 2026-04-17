from __future__ import annotations

"""
Async Telegram bot client.
Sends messages to a configured chat ID.
Handles rate limiting (30 msg/s per bot, 1 msg/s per chat).
"""

import asyncio
from typing import Optional

import aiohttp

from config.settings import get_settings
from utils.logger import get_logger

logger = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_send: float = 0.0
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def send_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
    ) -> bool:
        if not self._settings.telegram_bot_token:
            logger.warning("Telegram bot token not configured")
            return False

        target_chat = chat_id or self._settings.telegram_chat_id
        if not target_chat:
            logger.warning("Telegram chat_id not configured")
            return False

        async with self._lock:
            # Respect ~1 msg/s per chat limit
            import time
            elapsed = time.monotonic() - self._last_send
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)

            url = TELEGRAM_API.format(token=self._settings.telegram_bot_token)
            payload = {
                "chat_id": target_chat,
                "text": text[:4096],  # Telegram max message length
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_web_page_preview,
            }

            session = await self._get_session()
            try:
                async with session.post(url, json=payload) as resp:
                    import time
                    self._last_send = time.monotonic()
                    if resp.status == 200:
                        return True
                    body = await resp.text()
                    if resp.status == 429:
                        retry_after = float(
                            resp.headers.get("Retry-After", "5")
                        )
                        logger.warning("Telegram 429 – retrying after %.1fs", retry_after)
                        await asyncio.sleep(retry_after)
                        return await self.send_message(text, chat_id, parse_mode)
                    logger.error("Telegram send failed %d: %s", resp.status, body[:200])
                    return False
            except aiohttp.ClientError as exc:
                logger.error("Telegram network error: %s", exc)
                return False

    async def send_long_message(self, text: str, **kwargs) -> None:
        """Split and send messages longer than 4096 chars."""
        chunks = [text[i : i + 4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            await self.send_message(chunk, **kwargs)
            if len(chunks) > 1:
                await asyncio.sleep(0.5)


_client: TelegramClient | None = None


def get_telegram_client() -> TelegramClient:
    global _client
    if _client is None:
        _client = TelegramClient()
    return _client
