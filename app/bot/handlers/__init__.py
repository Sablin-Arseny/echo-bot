import logging

from aiogram import F, Router
from aiogram.types import ErrorEvent

from . import events, expenses, menu, payments


router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
router.include_routers(menu.router, events.router, expenses.router, payments.router)
logger = logging.getLogger(__name__)


@router.error()
async def handle_error(event: ErrorEvent) -> bool:
    logger.exception("Telegram update failed", exc_info=event.exception)
    update = event.update
    if update.callback_query:
        await update.callback_query.answer(str(event.exception), show_alert=True)
    elif update.message:
        await update.message.answer(f"Не удалось выполнить действие: {event.exception}")
    return True
