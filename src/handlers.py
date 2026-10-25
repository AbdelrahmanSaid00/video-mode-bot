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


class Handlers(NamedTuple):
    start: Handler
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

    def build_keyboard() -> InlineKeyboardMarkup:
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
            "Choose whether to convert it to a black-screen video or get the original media."
        )

    @gate
    async def on_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        att = msg.video or msg.audio or msg.voice or msg.video_note or msg.document

        if not att:
            return

        sent_msg = await msg.reply_text("اختر طريقة المعالجة:", reply_markup=build_keyboard())
        ctx.user_data[f"pending_{sent_msg.message_id}"] = {
            "type": "media",
            "att": att,
            "target_msg": msg,
        }

    @gate
    async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        found = links.detect_links(msg.text)
        if not found:
            await msg.reply_text(
                "I need a video/audio file or a Facebook / Instagram / X / YouTube / TikTok link."
            )
            return

        sent_msg = await msg.reply_text("اختر طريقة المعالجة:", reply_markup=build_keyboard())
        ctx.user_data[f"pending_{sent_msg.message_id}"] = {
            "type": "links",
            "links": found,
            "target_msg": msg,
        }

    @gate
    async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        if not query.data or not query.data.startswith("mode:"):
            return

        raw_mode = query.data.split(":", 1)[1]
        mode = ProcessingMode(raw_mode)

        bot_msg = query.message
        key = f"pending_{bot_msg.message_id}"

        if key not in ctx.user_data:
            err_text = "❌ حدث خطأ أو انتهت صلاحية الطلب. يرجى إعادة إرسال الملف أو اللينك."
            if bot_msg.text != err_text:
                try:
                    await query.edit_message_text(err_text)
                except BadRequest:
                    pass
            return

        pending_data = ctx.user_data.pop(key)
        target_msg = pending_data["target_msg"]

        try:
            await query.delete_message()
        except BadRequest:
            pass

        if pending_data["type"] == "media":
            att = pending_data["att"]

            async def fetch(tmp: Path, status: Message) -> Path:
                await status.edit_text("⬇️ Downloading file...")
                tg_file = await att.get_file()
                suffix = Path(tg_file.file_path or "").suffix or ".bin"
                dest = tmp / f"{att.file_unique_id}{suffix}"
                await tg_file.download_to_drive(dest)
                return Path(dest)

            await runner.run(target_msg, fetch, mode)

        elif pending_data["type"] == "links":
            for link in pending_data["links"]:

                async def fetch(tmp: Path, status: Message, link=link) -> Path:
                    await status.edit_text(f"⬇️ Downloading from {link.platform}...")
                    return await asyncio.to_thread(
                        download.download_media,
                        link.url,
                        tmp,
                        mode,
                        settings.cookies_file,
                        settings.cookies_browser,
                    )

                await runner.run(target_msg, fetch, mode)

    return Handlers(start, on_media, on_text, on_callback)