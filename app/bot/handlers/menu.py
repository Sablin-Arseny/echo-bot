from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.invites import invited_event_id
from app.bot.keyboards import join_menu, main_menu
from app.src.db.user import UserDB
from app.src.schemas import User
from app.src.services.event import EventService

from .common import ensure_user
from .events import event_text


router = Router()

@router.message(CommandStart())
async def start(message: Message, command: CommandObject) -> None:
    existing = await UserDB.get_as_dependency().get(
        User(tg_id=str(message.from_user.id))
    )
    user = await ensure_user(message)
    name = escape(user.full_name or user.username or "друг")
    greeting = (
        f"Привет, {name}! Я помогу вести события, делить расходы и отмечать оплаты. "
        "Начните с создания события или откройте приглашение."
        if existing is None
        else f"С возвращением, {name}! Ваши события и расходы доступны в меню."
    )
    if command.args:
        event_id = invited_event_id(command.args)
        if event_id is None:
            await message.answer(
                f"{greeting}\n\nСсылка приглашения недействительна.",
                reply_markup=main_menu(),
            )
            return
        event = await EventService.get_as_dependency().get(event_id)
        if not event:
            await message.answer(
                f"{greeting}\n\nСобытие не найдено.", reply_markup=main_menu()
            )
            return
        await message.answer(
            f"{greeting}\n\nВас пригласили в событие:\n\n{event_text(event)}",
            reply_markup=join_menu(command.args),
        )
        return
    await message.answer(greeting, reply_markup=main_menu())


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Бот помогает вести события, общие расходы и оплаты. "
        "Используйте кнопки меню. /cancel отменяет текущую форму.",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())


@router.callback_query(F.data == "menu:main")
async def main_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Что хотите сделать?", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "form:cancel")
async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Действие отменено.", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:events")
async def list_events(callback: CallbackQuery) -> None:
    user = await ensure_user(callback)
    events = await EventService.get_as_dependency().get_by_user(user, "PARTICIPATING")
    builder = InlineKeyboardBuilder()
    for event in events:
        builder.button(text=event.name, callback_data=f"event:view:{event.id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Создать событие", callback_data="event:create"))
    builder.row(InlineKeyboardButton(text="Главное меню", callback_data="menu:main"))
    text = "Ваши события:" if events else "У вас пока нет событий."
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()
