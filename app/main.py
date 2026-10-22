import asyncio
import logging

from aiogram import Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import router
from app.bot.screens import CleanChatMiddleware, ScreenBot
from app.config import settings
from app.src.models.base import init_db


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    await init_db()
    bot = ScreenBot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.message.outer_middleware(CleanChatMiddleware())
    dispatcher.callback_query.outer_middleware(CleanChatMiddleware())
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
