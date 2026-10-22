import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
)
from aiogram.types import CallbackQuery, Chat, Message, User

from app.bot.keyboards import main_menu
from app.bot.screens import CleanChatMiddleware, ScreenBot


class FakeSession:
    def __init__(self):
        self.calls = []
        self.next_id = 1

    async def __call__(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            message = SimpleNamespace(message_id=self.next_id)
            self.next_id += 1
            return message
        return True


def test_only_latest_screen_keeps_keyboard_and_follows_notice():
    async def check():
        bot = ScreenBot(token="123456:test")
        session = FakeSession()
        bot.session = session

        await bot.send_message(42, "Первый экран", reply_markup=main_menu())
        await bot.send_message(42, "Уведомление")
        await bot.send_message(42, "Следующий экран", reply_markup=main_menu())

        assert bot.screens[42].message_id == 4
        assert [type(call) for call in session.calls] == [
            SendMessage,
            SendMessage,
            SendMessage,
            DeleteMessage,
            SendMessage,
            DeleteMessage,
        ]
        assert [
            call.message_id for call in session.calls if isinstance(call, DeleteMessage)
        ] == [1, 3]
        assert session.calls[2].text == "Первый экран"

    asyncio.run(check())


def test_edit_updates_screen_used_after_notification():
    async def check():
        bot = ScreenBot(token="123456:test")
        session = FakeSession()
        bot.session = session

        await bot.send_message(42, "Первый экран", reply_markup=main_menu())
        await bot.edit_message_text(
            "Обновлённый экран", chat_id=42, message_id=1, reply_markup=main_menu()
        )
        await bot.send_message(42, "Уведомление")

        assert bot.screens[42].message_id == 3
        assert session.calls[-2].text == "Обновлённый экран"
        assert isinstance(session.calls[1], EditMessageText)

    asyncio.run(check())


def test_notification_starts_menu_for_chat_without_screen():
    async def check():
        bot = ScreenBot(token="123456:test")
        session = FakeSession()
        bot.session = session

        await bot.send_message(42, "Новый расход")

        assert bot.screens[42].message_id == 2
        assert len(session.calls) == 2
        assert session.calls[0].reply_markup is None
        assert session.calls[1].reply_markup is not None

    asyncio.run(check())


def test_old_callback_after_restart_only_opens_new_menu():
    async def check():
        bot = ScreenBot(token="123456:test")
        session = FakeSession()
        bot.session = session
        old_message = Message(
            message_id=7,
            date=datetime.now(UTC),
            chat=Chat(id=42, type="private"),
            text="Старый экран",
            reply_markup=main_menu(),
        )
        callback = CallbackQuery(
            id="1",
            from_user=User(id=42, is_bot=False, first_name="User"),
            chat_instance="1",
            message=old_message,
            data="menu:events",
        )

        async def unexpected_handler(event, data):
            raise AssertionError("Stale callback reached a handler")

        callback.as_(bot)
        await CleanChatMiddleware()(unexpected_handler, callback, {"bot": bot})

        assert bot.screens[42].message_id == 1
        assert isinstance(session.calls[0], DeleteMessage)
        assert isinstance(session.calls[1], SendMessage)
        assert isinstance(session.calls[2], AnswerCallbackQuery)

    asyncio.run(check())


def test_typed_input_keeps_question_and_user_message():
    async def check():
        bot = ScreenBot(token="123456:test")
        session = FakeSession()
        bot.session = session
        await bot.send_message(42, "Введите название", reply_markup=main_menu())
        user_message = Message(
            message_id=10,
            date=datetime.now(UTC),
            chat=Chat(id=42, type="private"),
            text="Название",
        )

        class State:
            async def get_state(self):
                return "EventForm:name"

        async def next_question(event, data):
            await bot.send_message(42, "Введите описание", reply_markup=main_menu())

        await CleanChatMiddleware()(
            next_question, user_message, {"bot": bot, "state": State()}
        )

        assert bot.screens[42].message_id == 2
        assert [type(call) for call in session.calls] == [
            SendMessage,
            SendMessage,
            EditMessageReplyMarkup,
        ]
        assert session.calls[-1].message_id == 1

    asyncio.run(check())


def test_command_replaces_form_question():
    async def check():
        bot = ScreenBot(token="123456:test")
        session = FakeSession()
        bot.session = session
        await bot.send_message(42, "Введите название", reply_markup=main_menu())
        command = Message(
            message_id=10,
            date=datetime.now(UTC),
            chat=Chat(id=42, type="private"),
            text="/cancel",
        )

        class State:
            async def get_state(self):
                return "EventForm:name"

        async def show_menu(event, data):
            await bot.send_message(42, "Главное меню", reply_markup=main_menu())

        await CleanChatMiddleware()(show_menu, command, {"bot": bot, "state": State()})

        assert isinstance(session.calls[-1], DeleteMessage)
        assert session.calls[-1].message_id == 1

    asyncio.run(check())
