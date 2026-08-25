"""Telegram bridge: control IRIS from your phone, anywhere, for free.

Create a bot with @BotFather, put its token in ``TELEGRAM_BOT_TOKEN``, add your
numeric Telegram user id to ``TELEGRAM_ALLOWED_USER_IDS`` and set
``TELEGRAM_ENABLED=true``. IRIS long-polls the Bot API (no public IP, no tunnel,
no webhook needed) and answers messages through the same kernel pipeline as the
web UI. Voice notes are transcribed when a server STT engine exists.

Security: messages from any user id not on the allowlist are ignored (with one
polite notice), because this bridge can drive the whole desktop.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from iris.app.core.config import settings
from iris.app.core.logging import get_logger

logger = get_logger("services.telegram")

_API = "https://api.telegram.org"


class TelegramBridge:
    """Long-polling Telegram bot bridging chats into the agent kernel."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task[None]] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._offset = 0
        self._warned: set[int] = set()
        self.running = False

    @property
    def configured(self) -> bool:
        return bool(settings.TELEGRAM_ENABLED and settings.TELEGRAM_BOT_TOKEN)

    def _base(self) -> str:
        return f"{_API}/bot{settings.TELEGRAM_BOT_TOKEN}"

    async def start(self) -> None:
        if self.running or not self.configured:
            if not self.configured and settings.TELEGRAM_ENABLED:
                logger.warning("Telegram enabled but TELEGRAM_BOT_TOKEN is missing.")
            return
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(65.0, connect=10.0))
        self._task = asyncio.create_task(self._loop(), name="iris-telegram")
        self.running = True
        logger.info("Telegram bridge started.")

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ loop
    async def _loop(self) -> None:
        assert self._client is not None
        while self.running:
            try:
                response = await self._client.get(
                    f"{self._base()}/getUpdates",
                    params={"timeout": 50, "offset": self._offset + 1,
                            "allowed_updates": '["message"]'},
                )
                if response.status_code != 200:
                    logger.warning("getUpdates HTTP %s; backing off.", response.status_code)
                    await asyncio.sleep(10)
                    continue
                for update in response.json().get("result", []):
                    self._offset = max(self._offset, update.get("update_id", 0))
                    try:
                        await self._handle_update(update)
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Telegram update failed: %s", exc, exc_info=True)
            except asyncio.CancelledError:
                raise
            except httpx.HTTPError as exc:
                logger.debug("Telegram poll error (%s); retrying.", type(exc).__name__)
                await asyncio.sleep(settings.TELEGRAM_POLL_INTERVAL * 3)
            except Exception as exc:  # noqa: BLE001
                logger.error("Telegram loop error: %s", exc, exc_info=True)
                await asyncio.sleep(10)

    def _authorized(self, user_id: int) -> bool:
        allowed = {str(uid).strip() for uid in settings.TELEGRAM_ALLOWED_USER_IDS}
        return str(user_id) in allowed

    async def _handle_update(self, update: dict[str, Any]) -> None:
        assert self._client is not None
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        user_id = (message.get("from") or {}).get("id")
        if chat_id is None or user_id is None:
            return

        if not self._authorized(user_id):
            if user_id not in self._warned:
                self._warned.add(user_id)
                await self._send(chat_id, "Sorry, this IRIS instance isn't paired with your account.")
                logger.warning("Ignored Telegram message from unauthorized user %s.", user_id)
            return

        text = (message.get("text") or "").strip()

        # Voice notes: download and transcribe when possible.
        if not text and message.get("voice"):
            text = await self._transcribe_voice(message["voice"]) or ""
            if not text:
                await self._send(chat_id, "I couldn't transcribe that voice note here — try typing it.")
                return

        if not text:
            return

        if text in ("/start", "/help"):
            await self._send(
                chat_id,
                "Hi! I'm IRIS, connected to your computer. Tell me things like:\n"
                "• open youtube\n• take a screenshot\n• remind me in 10 minutes to stretch\n"
                "• what's the weather\n• make a ppt about space",
            )
            return

        await self._client.post(f"{self._base()}/sendChatAction",
                                data={"chat_id": chat_id, "action": "typing"})

        from iris.app.agent.kernel import default_kernel

        response = await default_kernel.process_request(
            user_input=text,
            conversation_id=f"tg_{chat_id}",
            channel="telegram",
        )
        reply = response.response or "Done."
        if response.artifacts:
            names = ", ".join(a.split("/")[-1].split("\\")[-1] for a in response.artifacts)
            reply += f"\n\n📎 Created: {names} (in your Iris folder on the computer)"
        await self._send(chat_id, reply[:4000])

        # Send small produced files directly to the phone.
        for artifact in response.artifacts[:3]:
            await self._send_document(chat_id, artifact)

    async def _transcribe_voice(self, voice: dict[str, Any]) -> Optional[str]:
        assert self._client is not None
        try:
            file_info = await self._client.get(
                f"{self._base()}/getFile", params={"file_id": voice.get("file_id")}
            )
            file_path = ((file_info.json() or {}).get("result") or {}).get("file_path")
            if not file_path:
                return None
            audio = await self._client.get(
                f"{_API}/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file_path}"
            )
            from iris.app.voice.service import default_voice_service

            result = await default_voice_service.transcribe(audio.content, "audio/ogg")
            return (result or {}).get("text")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Voice note transcription failed: %s", exc)
            return None

    async def _send(self, chat_id: int, text: str) -> None:
        assert self._client is not None
        try:
            await self._client.post(
                f"{self._base()}/sendMessage",
                data={"chat_id": chat_id, "text": text},
            )
        except httpx.HTTPError as exc:
            logger.warning("Telegram send failed: %s", exc)

    async def _send_document(self, chat_id: int, path: str) -> None:
        assert self._client is not None
        from pathlib import Path

        file = Path(path)
        if not file.is_file() or file.stat().st_size > 20_000_000:
            return
        try:
            with file.open("rb") as fh:
                await self._client.post(
                    f"{self._base()}/sendDocument",
                    data={"chat_id": chat_id},
                    files={"document": (file.name, fh)},
                )
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("Telegram document send failed: %s", exc)


default_telegram_bridge = TelegramBridge()
