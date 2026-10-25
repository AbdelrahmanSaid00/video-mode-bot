"""Assemble the Telegram application and run it.

This is the only module that reads Settings.from_env() and configures logging.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from src.config import Settings
from src.handlers import create_handlers
from src.jobs import JobRunner

log = logging.getLogger("blackscreen-bot")


def build_application(settings: Settings) -> Application:
    builder = Application.builder().token(settings.bot_token)
    if settings.bot_api_url:
        url = settings.bot_api_url
        builder = (
            builder.base_url(f"{url}/bot")
            .base_file_url(f"{url}/file/bot")
            .local_mode(True)
        )
    app = builder.concurrent_updates(True).build()

    runner = JobRunner(
        settings=settings,
        semaphore=asyncio.Semaphore(settings.max_parallel),
    )
    handlers = create_handlers(settings, runner)

    media = (
        filters.VIDEO | filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE
        | filters.Document.VIDEO | filters.Document.AUDIO
    )
    app.add_handler(CommandHandler(["start", "help"], handlers.start))
    app.add_handler(MessageHandler(media, handlers.on_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))
    app.add_handler(CallbackQueryHandler(handlers.on_callback))
    return app


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not settings.allowed_ids:
        log.warning("ALLOWED_USER_IDS is empty: anyone can use this bot!")
    log.info("Bot started")
    build_application(settings).run_polling(allowed_updates=Update.ALL_TYPES)
