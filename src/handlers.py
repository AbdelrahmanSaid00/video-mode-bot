"""Telegram update handlers and the authorization decorator.

Every dependency arrives as a parameter - no module-level globals.
"""

from __future__ import annotations

import asyncio
from functools import wraps
from pathlib import Path
from typing import Awaitable, Callable, NamedTuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from src import download, links
from src.config import Settings
from src.jobs import JobRunner
from src.models import ProcessingMode

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]

user_modes : dict[int , ProcessingMode] = {}

class Handlers(NamedTuple):
    start: Handler
    modes: Handler
    on_media: Handler
    on_text: Handler
    on_callback: Handler


def authorized(allowed_ids: frozenset[int]):
    """Gate a handler: reply "private" and drop the update for disallowed users."""

    def decorator(handler: Handler) -> Handler:
        @wraps(handler)
        async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
            user = update.effective_user
            if allowed_ids and (not user or user.id not in allowed_ids):
                if update.effective_message:
                    await update.effective_message.reply_text("Sorry, this bot is private.")
                return
            return await handler(update, ctx)

        return wrapper

    return decorator


def create_handlers(settings: Settings, runner: JobRunner) -> Handlers:
    gate = authorized(settings.allowed_ids)

    def build_keyboard () -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("⬛ شاشة سوداء", callback_data=f"mode:{ProcessingMode.BLACK_SCREEN.value}"),
                InlineKeyboardButton("🎬 الفيديو الأصلي", callback_data=f"mode:{ProcessingMode.ORIGINAL.value}"),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    @gate
    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Send me:\n"
            "• a video or audio file\n"
            "• a Facebook / Instagram / X / TikTok / YouTube link\n\n"
            "Use /modes to change your preferred download format (Black Screen vs Original Video)."
        )

    @gate
    async def modes (update: Update , ctx : ContextTypes.DEFAULT_TYPE)-> None:
        await update.message.reply_text("اختر طريقة المعالجة الافتراضية الخاصة بك:", reply_markup=build_keyboard())
    
    @gate
    async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        if not query.data or not query.data.startswith("mode:"):
            return

        raw_mode = query.data.split(":", 1)[1]
        mode = ProcessingMode(raw_mode)
        
        user_modes[query.from_user.id] = mode
        
        mode_text = "شاشة سوداء" if mode == ProcessingMode.BLACK_SCREEN else "الفيديو الأصلي"
        
        try:
            await query.edit_message_text(f"✅ تم حفظ اختيارك كـ: {mode_text}. أي لينك هتبعته هيتحمل بالصيغة دي.")
        except BadRequest:
            pass


    @gate
    async def on_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        att = msg.video or msg.audio or msg.voice or msg.video_note or msg.document
        if not att:
            return
        mode = user_modes.get(update.effective_user.id , ProcessingMode.BLACK_SCREEN)
        
        async def fetch(tmp: Path, status: Message) -> Path:
            await status.edit_text("⬇️ Downloading file...")
            tg_file = await att.get_file()
            suffix = Path(tg_file.file_path or "").suffix or ".bin"
            dest = tmp / f"{att.file_unique_id}{suffix}"
            await tg_file.download_to_drive(dest)
            return Path(dest)

        await runner.run(msg, fetch, mode)

    @gate
    async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        found = links.detect_links(msg.text)
        if not found:
            await msg.reply_text(
                "I need a video/audio file or a Facebook / Instagram / X link."
            )
            return
        mode = user_modes.get(update.effective_user.id, ProcessingMode.BLACK_SCREEN)
        
        for link in found:
            async def fetch(tmp: Path, status: Message, link=link) -> Path:
                await status.edit_text(f"⬇️ Downloading from {link.platform}...")
                return await asyncio.to_thread(
                    download.download_audio,
                    link.url,
                    tmp,
                    mode,
                    settings.max_duration, 
                    settings.cookies_file,
                    settings.cookies_browser,
                )

            await runner.run(msg, fetch, mode)

    return Handlers(start, modes, on_media, on_text, on_callback)
