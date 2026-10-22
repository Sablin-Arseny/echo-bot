import asyncio
import logging
from contextvars import ContextVar
from dataclasses import dataclass

from aiogram import Bot
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import (
    DeleteMessage,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
)
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import main_menu

logger = logging.getLogger(__name__)


@dataclass
class Screen:
    message_id: int
    text: str
    reply_markup: object


class ScreenBot(Bot):
    """Keep one current keyboard message per private chat."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.screens: dict[int, Screen] = {}
        self._screen_locks: dict[int, asyncio.Lock] = {}
        self._input_chat: ContextVar[int | None] = ContextVar(
            "input_chat", default=None
        )

    async def __call__(self, method, request_timeout=None):
        if not isinstance(
            method, (SendMessage, EditMessageText, EditMessageReplyMarkup)
        ):
            return await super().__call__(method, request_timeout)
        chat_id = int(method.chat_id)
        async with self._screen_locks.setdefault(chat_id, asyncio.Lock()):
            result = await super().__call__(method, request_timeout)
            current = self.screens.get(chat_id)
            if isinstance(method, SendMessage):
                if method.reply_markup:
                    self.screens[chat_id] = Screen(
                        result.message_id, method.text, method.reply_markup
                    )
                    if current:
                        if self._input_chat.get() == chat_id:
                            await self._clear_old_keyboard(chat_id, current.message_id)
                        else:
                            await self._remove_old_screen(chat_id, current.message_id)
                elif current:
                    await self._move_screen(chat_id, current)
                else:
                    menu = SendMessage(
                        chat_id=chat_id,
                        text="Что хотите сделать?",
                        reply_markup=main_menu(),
                    )
                    latest = await super().__call__(menu)
                    self.screens[chat_id] = Screen(
                        latest.message_id, menu.text, menu.reply_markup
                    )
            elif isinstance(method, EditMessageText) and method.reply_markup:
                self.screens[chat_id] = Screen(
                    method.message_id, method.text, method.reply_markup
                )
            elif (
                isinstance(method, EditMessageReplyMarkup)
                and current
                and current.message_id == method.message_id
            ):
                current.reply_markup = method.reply_markup
            return result

    async def _move_screen(self, chat_id: int, current: Screen) -> None:
        try:
            new = await super().__call__(
                SendMessage(
                    chat_id=chat_id,
                    text=current.text,
                    reply_markup=current.reply_markup,
                )
            )
        except TelegramAPIError:
            logger.warning("Could not move screen in chat %s", chat_id, exc_info=True)
            return
        self.screens[chat_id] = Screen(
            new.message_id, current.text, current.reply_markup
        )
        await self._remove_old_screen(chat_id, current.message_id)

    async def _remove_old_screen(self, chat_id: int, message_id: int) -> None:
        try:
            await super().__call__(
                DeleteMessage(chat_id=chat_id, message_id=message_id)
            )
        except TelegramAPIError:
            await self._clear_old_keyboard(chat_id, message_id)

    async def _clear_old_keyboard(self, chat_id: int, message_id: int) -> None:
        try:
            await super().__call__(
                EditMessageReplyMarkup(chat_id=chat_id, message_id=message_id)
            )
        except TelegramAPIError:
            logger.warning(
                "Could not clear old screen %s in chat %s", message_id, chat_id
            )


class CleanChatMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        bot: ScreenBot = data["bot"]
        if isinstance(event, CallbackQuery):
            message = event.message
            if not isinstance(message, Message):
                await event.answer("Это сообщение больше недоступно")
                return None
            current = bot.screens.get(message.chat.id)
            if not current or current.message_id != message.message_id:
                await bot._remove_old_screen(message.chat.id, message.message_id)
                if not current:
                    await bot.send_message(
                        message.chat.id, "Что хотите сделать?", reply_markup=main_menu()
                    )
                await event.answer(
                    "Эти кнопки устарели. Используйте последнее сообщение."
                )
                return None
            return await handler(event, data)
        state = data.get("state")
        if (
            isinstance(event, Message)
            and event.text
            and not event.text.startswith("/")
            and state
            and await state.get_state()
        ):
            token = bot._input_chat.set(event.chat.id)
            try:
                return await handler(event, data)
            finally:
                bot._input_chat.reset(token)
        return await handler(event, data)
