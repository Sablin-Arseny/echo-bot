import logging
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, InlineKeyboardButton, Message
from aiogram.utils.deep_linking import create_start_link
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.calendar import date_input_menu, hour_menu, minute_menu, month_menu
from app.bot.expenses import (
    collect_debts,
    collect_pending_confirmations,
    display_name,
    notify_new_expense,
    notify_payment,
    totals_by_creditor,
)
from app.bot.invites import invite_payload, invited_event_id
from app.bot.keyboards import (
    cancel_menu,
    confirmation_menu,
    event_menu,
    join_menu,
    main_menu,
    participants_menu,
    skip_menu,
    split_mode_menu,
)
from app.bot.labels import EVENT_STATUS, EXPENSE_STATUS, PAYMENT_STATUS
from app.bot.states import EventForm, ExpenseForm
from app.src.db.user import UserDB
from app.src.schemas import (
    ConfirmPaymentRequest,
    CreateEventRequest,
    CreateExpenseRequest,
    ExpenseParticipantRequest,
    MarkParticipantPaidRequest,
    User,
)
from app.src.services.event import EventService
from app.src.services.expense import ExpenseService


router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")


def telegram_user(message_or_callback: Message | CallbackQuery):
    return message_or_callback.from_user


async def ensure_user(message_or_callback: Message | CallbackQuery) -> User:
    telegram = telegram_user(message_or_callback)
    user_db = UserDB.get_as_dependency()
    tg_id = str(telegram.id)
    stored = await user_db.get(User(tg_id=tg_id))
    profile = User(
        username=telegram.username or tg_id,
        tg_id=tg_id,
        full_name=telegram.full_name,
    )
    if not stored:
        stored = await user_db.create(profile)
    elif (
        stored.username != profile.username or stored.full_name != profile.full_name
    ):
        stored = await user_db.update(User(id=stored.id), profile)
    return User.model_validate(stored)


async def require_event_member(event_id: int, user: User) -> None:
    participants = await EventService.get_as_dependency().get_participants(event_id)
    if not any(
        participant.id == user.id and participant.status == "PARTICIPATING"
        for participant in participants
    ):
        raise PermissionError("Вы не участвуете в этом событии")


def parse_amount(value: str) -> Decimal:
    try:
        amount = Decimal(value.replace(",", ".").strip()).quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise ValueError("Введите сумму числом") from error
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    return amount


def event_text(event) -> str:
    start_date = event.start_date.astimezone(MOSCOW) if event.start_date.tzinfo else event.start_date
    lines = [
        f"<b>{escape(event.name)}</b>",
        format_event_date(start_date),
    ]
    if event.event_place:
        lines.append(escape(event.event_place))
    if event.description:
        lines.extend(["", escape(event.description)])
    return "\n".join(lines)


def format_event_date(value: datetime) -> str:
    pattern = "%d.%m.%Y" if value.time() == time.min else "%d.%m.%Y %H:%M"
    return value.strftime(pattern)


def expense_text(expense) -> str:
    lines = [
        f"<b>{escape(expense.description or 'Расход')}</b>",
        f"Сумма: {expense.amount:.2f}",
        f"Оплатил: {escape(expense.paid_by.full_name or expense.paid_by.username or expense.paid_by.tg_id)}",
        f"Статус: {EXPENSE_STATUS[expense.status]}",
        "",
    ]
    for participant in expense.participants:
        name = participant.user.full_name or participant.user.username or participant.user.tg_id
        status = (
            "Частично оплачен"
            if participant.status == "PENDING" and participant.paid_amount > 0
            else PAYMENT_STATUS[participant.status]
        )
        lines.append(
            f"{escape(name)}: {participant.share_amount:.2f}, "
            f"осталось {participant.remaining_amount:.2f} "
            f"({status})"
        )
    return "\n".join(lines)


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


@router.callback_query(F.data.startswith("event:view:"))
async def view_event(callback: CallbackQuery) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    await require_event_member(event_id, user)
    event = await EventService.get_as_dependency().get(event_id)
    if not event:
        await callback.answer("Событие не найдено", show_alert=True)
        return
    participants = await EventService.get_as_dependency().get_participants(event_id)
    await callback.message.edit_text(
        f"{event_text(event)}\n\nУчастников: {len(participants)}",
        reply_markup=event_menu(event_id),
    )
    await callback.answer()


@router.callback_query(F.data == "event:create")
async def create_event(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(EventForm.name)
    await callback.message.edit_text("Введите название события", reply_markup=cancel_menu())
    await callback.answer()


@router.message(EventForm.name)
async def event_name(message: Message, state: FSMContext) -> None:
    if not message.text or not message.text.strip():
        await message.answer("Название не может быть пустым.")
        return
    await state.update_data(name=message.text.strip())
    await state.set_state(EventForm.description)
    await message.answer("Введите описание", reply_markup=skip_menu("event_description"))


@router.message(EventForm.description)
async def event_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text.strip() if message.text else None)
    await ask_event_date(message, state)


@router.callback_query(EventForm.description, F.data == "skip:event_description")
async def skip_event_description(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(description=None)
    await state.set_state(EventForm.start_date)
    await callback.message.edit_text(
        "Введите дату ДД.ММ.ГГГГ или дату со временем ДД.ММ.ГГГГ ЧЧ:ММ (МСК)",
        reply_markup=date_input_menu(),
    )
    await callback.answer()


async def ask_event_date(message: Message, state: FSMContext) -> None:
    await state.set_state(EventForm.start_date)
    await message.answer(
        "Введите дату ДД.ММ.ГГГГ или дату со временем ДД.ММ.ГГГГ ЧЧ:ММ (МСК)",
        reply_markup=date_input_menu(),
    )


def parse_event_date(value: str) -> datetime:
    for pattern in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).replace(tzinfo=MOSCOW)
        except ValueError:
            pass
    raise ValueError("Используйте формат ДД.ММ.ГГГГ или ДД.ММ.ГГГГ ЧЧ:ММ.")


async def ask_event_place(message: Message, state: FSMContext, edit: bool = False) -> None:
    await state.set_state(EventForm.place)
    method = message.edit_text if edit else message.answer
    await method("Введите место", reply_markup=skip_menu("event_place"))


@router.message(EventForm.start_date)
async def event_start_date(message: Message, state: FSMContext) -> None:
    try:
        start_date = parse_event_date(message.text or "")
    except (AttributeError, ValueError):
        await message.answer("Используйте формат ДД.ММ.ГГГГ или ДД.ММ.ГГГГ ЧЧ:ММ.")
        return
    await state.update_data(start_date=start_date.isoformat())
    await ask_event_place(message, state)


@router.callback_query(EventForm.start_date, F.data == "calendar:open")
async def open_calendar(callback: CallbackQuery) -> None:
    today = datetime.now(MOSCOW)
    await callback.message.edit_text(
        "Выберите дату события", reply_markup=month_menu(today.year, today.month)
    )
    await callback.answer()


@router.callback_query(EventForm.start_date, F.data.startswith("calendar:month:"))
async def change_calendar_month(callback: CallbackQuery) -> None:
    try:
        year, month = map(int, callback.data.rsplit(":", 1)[1].split("-"))
        markup = month_menu(year, month)
    except ValueError:
        await callback.answer("Некорректный месяц", show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=markup)
    await callback.answer()


@router.callback_query(EventForm.start_date, F.data.startswith("calendar:day:"))
async def choose_calendar_day(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        selected = date.fromisoformat(callback.data.rsplit(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректная дата", show_alert=True)
        return
    await state.update_data(selected_date=selected.isoformat())
    await state.set_state(EventForm.time)
    await callback.message.edit_text(
        f"{selected:%d.%m.%Y}. Выберите час, нажмите «Без времени» "
        "или введите ЧЧ:ММ:",
        reply_markup=hour_menu(),
    )
    await callback.answer()


@router.callback_query(EventForm.time, F.data.startswith("calendar:hour:"))
async def choose_calendar_hour(callback: CallbackQuery) -> None:
    hour = int(callback.data.rsplit(":", 1)[1])
    await callback.message.edit_text(
        "Выберите минуты или введите ЧЧ:ММ", reply_markup=minute_menu(hour)
    )
    await callback.answer()


@router.callback_query(EventForm.time, F.data == "calendar:no_time")
async def choose_date_only(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    start_date = datetime.combine(date.fromisoformat(data["selected_date"]), time.min, MOSCOW)
    await state.update_data(start_date=start_date.isoformat())
    await ask_event_place(callback.message, state, edit=True)
    await callback.answer()


@router.callback_query(EventForm.time, F.data.startswith("calendar:time:"))
async def choose_calendar_time(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        _, _, hour_text, minute_text = callback.data.split(":")
        selected_time = time(int(hour_text), int(minute_text))
    except ValueError:
        await callback.answer("Некорректное время", show_alert=True)
        return
    data = await state.get_data()
    start_date = datetime.combine(
        date.fromisoformat(data["selected_date"]), selected_time, MOSCOW
    )
    await state.update_data(start_date=start_date.isoformat())
    await ask_event_place(callback.message, state, edit=True)
    await callback.answer()


@router.message(EventForm.time)
async def type_calendar_time(message: Message, state: FSMContext) -> None:
    try:
        selected_time = datetime.strptime(
            (message.text or "").strip(), "%H:%M"
        ).time()
    except ValueError:
        await message.answer("Введите время в формате ЧЧ:ММ.")
        return
    data = await state.get_data()
    start_date = datetime.combine(
        date.fromisoformat(data["selected_date"]), selected_time, MOSCOW
    )
    await state.update_data(start_date=start_date.isoformat())
    await ask_event_place(message, state)


@router.callback_query(F.data == "calendar:noop")
async def ignore_calendar_button(callback: CallbackQuery) -> None:
    await callback.answer()


@router.message(EventForm.place)
async def event_place(message: Message, state: FSMContext) -> None:
    await state.update_data(place=message.text.strip() if message.text else None)
    await show_event_confirmation(message, state)


@router.callback_query(EventForm.place, F.data == "skip:event_place")
async def skip_event_place(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(place=None)
    await show_event_confirmation(callback.message, state, edit=True)
    await callback.answer()


async def show_event_confirmation(
    message: Message, state: FSMContext, edit: bool = False
) -> None:
    data = await state.get_data()
    start_date = datetime.fromisoformat(data["start_date"])
    text = (
        "Проверьте событие:\n\n"
        f"<b>{escape(data['name'])}</b>\n"
        f"{format_event_date(start_date)}\n"
        f"{escape(data.get('place') or 'Место не указано')}\n\n"
        f"{escape(data.get('description') or 'Без описания')}"
    )
    await state.set_state(EventForm.confirmation)
    method = message.edit_text if edit else message.answer
    await method(text, reply_markup=confirmation_menu("event"))


@router.callback_query(EventForm.confirmation, F.data == "save:event")
async def save_event(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    user = await ensure_user(callback)
    event = await EventService.get_as_dependency().create(
        CreateEventRequest(
            name=data["name"],
            description=data.get("description"),
            start_date=datetime.fromisoformat(data["start_date"]),
            event_place=data.get("place"),
        ),
        user,
    )
    await state.clear()
    await callback.message.edit_text(
        f"Событие создано.\n\n{event_text(event)}",
        reply_markup=event_menu(event.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("event:join:"))
async def join_event(callback: CallbackQuery) -> None:
    payload = callback.data.split(":", 2)[2]
    event_id = invited_event_id(payload)
    if event_id is None:
        await callback.answer("Ссылка приглашения недействительна", show_alert=True)
        return
    event = await EventService.get_as_dependency().get(event_id)
    if not event:
        await callback.answer("Событие не найдено", show_alert=True)
        return
    user = await ensure_user(callback)
    service = EventService.get_as_dependency()
    participants = await service.get_participants(event_id)
    current = next((participant for participant in participants if participant.id == user.id), None)
    if not current:
        await service.add_user_by_invite(event_id, user)
    await service.update_status_of_member(event_id, user, "PARTICIPATING")
    await callback.message.edit_text(
        f"Вы присоединились к событию.\n\n{event_text(event)}",
        reply_markup=event_menu(event_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("event:invite:"))
async def invite_to_event(callback: CallbackQuery) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    await require_event_member(event_id, user)
    link = await create_start_link(
        callback.bot, invite_payload(event_id), encode=False
    )
    await callback.message.answer(f"Ссылка для приглашения:\n{link}")
    await callback.answer()


@router.callback_query(F.data.startswith("event:members:"))
async def event_members(callback: CallbackQuery) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    await require_event_member(event_id, user)
    participants = await EventService.get_as_dependency().get_participants(event_id)
    lines = ["<b>👥 Участники</b>", ""]
    for participant in participants:
        name = participant.full_name or participant.username or participant.tg_id
        lines.append(f"{escape(name)} — {EVENT_STATUS[participant.status]}")
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Пригласить", callback_data=f"event:invite:{event_id}")
    builder.button(text="Назад", callback_data=f"event:view:{event_id}")
    builder.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("event:expenses:"))
async def event_expenses(callback: CallbackQuery) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    await require_event_member(event_id, user)
    expenses = [
        expense async for expense in ExpenseService.get_as_dependency().get(event_id)
        if expense.status != "DELETED"
    ]
    total = sum((Decimal(str(expense.amount)) for expense in expenses), Decimal("0"))
    builder = InlineKeyboardBuilder()
    for expense in expenses:
        label = expense.description or f"Расход #{expense.id}"
        marker = "✅ " if expense.status == "CLOSED" else ""
        builder.button(
            text=f"{marker}{label} — {expense.amount:.2f}",
            callback_data=f"expense:view:{expense.id}",
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="Добавить расход", callback_data=f"expense:create:{event_id}"
        )
    )
    builder.row(InlineKeyboardButton(text="Назад", callback_data=f"event:view:{event_id}"))
    text = f"💰 Расходы события\nВсего: {total:.2f}"
    if not expenses:
        text += "\n\nРасходов пока нет."
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("expense:view:"))
async def view_expense(callback: CallbackQuery) -> None:
    expense_id = int(callback.data.rsplit(":", 1)[1])
    expense = await ExpenseService.get_as_dependency().get_expense_detail(expense_id)
    user = await ensure_user(callback)
    await require_event_member(expense.event_id, user)
    builder = InlineKeyboardBuilder()
    participant = next(
        (item for item in expense.participants if item.user.tg_id == user.tg_id), None
    )
    if participant and participant.remaining_amount > 0:
        builder.button(text="Отметить оплату", callback_data=f"payment:create:{expense_id}")
    if expense.paid_by.id == user.id:
        for item in expense.participants:
            if item.status == "PAID":
                name = item.user.full_name or item.user.username or item.user.tg_id
                builder.button(
                    text=f"Подтвердить: {name}",
                    callback_data=f"payment:confirm:{expense_id}:{item.user.tg_id}",
                )
        builder.button(text="Удалить расход", callback_data=f"expense:delete:{expense_id}")
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="Назад", callback_data=f"event:expenses:{expense.event_id}"
        )
    )
    await callback.message.edit_text(expense_text(expense), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("expense:create:"))
async def create_expense(callback: CallbackQuery, state: FSMContext) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    await require_event_member(event_id, user)
    await state.clear()
    await state.update_data(event_id=event_id)
    await state.set_state(ExpenseForm.description)
    await callback.message.edit_text("Введите описание расхода", reply_markup=cancel_menu())
    await callback.answer()


@router.message(ExpenseForm.description)
async def expense_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=(message.text or "").strip())
    await state.set_state(ExpenseForm.amount)
    await message.answer("Введите общую сумму", reply_markup=cancel_menu())


@router.message(ExpenseForm.amount)
async def expense_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = parse_amount(message.text or "")
    except ValueError as error:
        await message.answer(str(error))
        return
    await state.update_data(amount=str(amount))
    await state.set_state(ExpenseForm.mode)
    await message.answer("Как разделить расход?", reply_markup=split_mode_menu())


@router.callback_query(ExpenseForm.mode, F.data.startswith("split:"))
async def expense_mode(callback: CallbackQuery, state: FSMContext) -> None:
    mode = callback.data.rsplit(":", 1)[1]
    data = await state.get_data()
    participants = await EventService.get_as_dependency().get_participants(data["event_id"])
    participants = [item for item in participants if item.status == "PARTICIPATING"]
    serialized = [
        {
            "tg_id": str(item.tg_id),
            "name": item.full_name or item.username or str(item.tg_id),
        }
        for item in participants
    ]
    await state.update_data(mode=mode, available_participants=serialized, selected=[])
    await state.set_state(ExpenseForm.participants)
    await callback.message.edit_text(
        "Выберите участников расхода",
        reply_markup=participants_menu(participants, set()),
    )
    await callback.answer()


@router.callback_query(ExpenseForm.participants, F.data.startswith("member:toggle:"))
async def toggle_expense_member(callback: CallbackQuery, state: FSMContext) -> None:
    tg_id = callback.data.rsplit(":", 1)[1]
    data = await state.get_data()
    selected = set(data["selected"])
    selected.remove(tg_id) if tg_id in selected else selected.add(tg_id)
    await state.update_data(selected=list(selected))
    participants = await EventService.get_as_dependency().get_participants(data["event_id"])
    participants = [item for item in participants if item.status == "PARTICIPATING"]
    await callback.message.edit_reply_markup(
        reply_markup=participants_menu(participants, selected)
    )
    await callback.answer()


@router.callback_query(ExpenseForm.participants, F.data == "member:done")
async def finish_expense_members(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data["selected"]:
        await callback.answer("Выберите хотя бы одного участника", show_alert=True)
        return
    selected = [
        item for item in data["available_participants"] if item["tg_id"] in data["selected"]
    ]
    await state.update_data(selected_participants=selected)
    if data["mode"] == "equal":
        await show_expense_confirmation(callback.message, state, edit=True)
        await callback.answer()
        return
    if len(selected) == 1:
        await state.update_data(shares={selected[0]["tg_id"]: data["amount"]})
        await show_expense_confirmation(callback.message, state, edit=True)
        await callback.answer()
        return
    await state.update_data(shares={}, manual_index=0)
    await state.set_state(ExpenseForm.manual_shares)
    await callback.message.edit_text(
        f"Введите долю для {escape(selected[0]['name'])}", reply_markup=cancel_menu()
    )
    await callback.answer()


@router.message(ExpenseForm.manual_shares)
async def manual_share(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        amount = parse_amount(message.text or "")
    except ValueError as error:
        await message.answer(str(error))
        return
    participants = data["selected_participants"]
    index = data["manual_index"]
    shares = data["shares"]
    total = Decimal(data["amount"])
    assigned = sum((Decimal(value) for value in shares.values()), Decimal("0"))
    remaining = total - assigned
    if amount >= remaining:
        await message.answer(
            f"Оставьте положительную долю последнему участнику. Доступно меньше {remaining:.2f}."
        )
        return
    shares[participants[index]["tg_id"]] = str(amount)
    index += 1
    if index == len(participants) - 1:
        assigned = sum((Decimal(value) for value in shares.values()), Decimal("0"))
        shares[participants[index]["tg_id"]] = str(total - assigned)
        await state.update_data(shares=shares, manual_index=index)
        await show_expense_confirmation(message, state)
        return
    await state.update_data(shares=shares, manual_index=index)
    await message.answer(
        f"Введите долю для {escape(participants[index]['name'])}",
        reply_markup=cancel_menu(),
    )


def equal_shares(total: Decimal, count: int) -> list[Decimal]:
    base = (total / count).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    remainder = int((total - base * count) * 100)
    return [base + (Decimal("0.01") if index < remainder else 0) for index in range(count)]


async def show_expense_confirmation(
    message: Message, state: FSMContext, edit: bool = False
) -> None:
    data = await state.get_data()
    participants = data["selected_participants"]
    total = Decimal(data["amount"])
    if data["mode"] == "equal":
        amounts = equal_shares(total, len(participants))
        shares = dict(zip((item["tg_id"] for item in participants), amounts))
    else:
        shares = {key: Decimal(value) for key, value in data["shares"].items()}
    lines = [
        "Проверьте расход:",
        "",
        f"<b>{escape(data['description'] or 'Расход')}</b> — {total:.2f}",
        "",
    ]
    for participant in participants:
        lines.append(f"{escape(participant['name'])}: {shares[participant['tg_id']]:.2f}")
    await state.set_state(ExpenseForm.confirmation)
    method = message.edit_text if edit else message.answer
    await method("\n".join(lines), reply_markup=confirmation_menu("expense"))


@router.callback_query(ExpenseForm.confirmation, F.data == "save:expense")
async def save_expense(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    participants = data["selected_participants"]
    if data["mode"] == "equal":
        expense_participants = [
            ExpenseParticipantRequest(tg_id=item["tg_id"]) for item in participants
        ]
    else:
        expense_participants = [
            ExpenseParticipantRequest(
                tg_id=item["tg_id"], share_amount=float(data["shares"][item["tg_id"]])
            )
            for item in participants
        ]
    request = CreateExpenseRequest(
        event_id=data["event_id"],
        amount=float(data["amount"]),
        is_equally=data["mode"] == "equal",
        description=data["description"],
        participants=expense_participants,
    )
    user = await ensure_user(callback)
    await require_event_member(data["event_id"], user)
    expense = await ExpenseService.get_as_dependency().create_expense_with_participants(
        request, user
    )
    await state.clear()
    await callback.message.edit_text(expense_text(expense), reply_markup=event_menu(expense.event_id))
    await callback.answer()
    event = await EventService.get_as_dependency().get(expense.event_id)
    await notify_new_expense(callback.bot, event.name, expense)


@router.callback_query(F.data == "menu:debts")
async def user_debts(callback: CallbackQuery) -> None:
    user = await ensure_user(callback)
    debts = await collect_debts(user)
    total = sum(
        (Decimal(str(participant.remaining_amount)) for _, participant in debts),
        Decimal("0"),
    )
    builder = InlineKeyboardBuilder()
    lines = [f"💳 Всего должны: {total:.2f}", ""]
    if debts:
        lines.append("Кому вы должны")
        for creditor, amount in totals_by_creditor(debts):
            lines.append(f"{escape(display_name(creditor))} — {amount:.2f}")
        lines.append("")
    for expense, participant in debts:
        label = expense.description or f"Расход #{expense.id}"
        creditor = display_name(expense.paid_by)
        lines.append(
            f"{escape(label)} — {participant.remaining_amount:.2f} "
            f"→ {escape(creditor)}"
        )
        builder.button(
            text=f"{label} — {participant.remaining_amount:.2f}",
            callback_data=f"expense:view:{expense.id}",
        )
    builder.adjust(1)
    if debts:
        builder.row(
            InlineKeyboardButton(
                text="Отметить всё оплаченным", callback_data="payment:all:preview"
            )
        )
    builder.row(InlineKeyboardButton(text="Главное меню", callback_data="menu:main"))
    text = "\n".join(lines) if debts else "У вас нет непогашенных долгов."
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "payment:all:preview")
async def preview_all_payments(callback: CallbackQuery) -> None:
    user = await ensure_user(callback)
    debts = await collect_debts(user)
    if not debts:
        await callback.answer("Непогашенных долгов нет", show_alert=True)
        return
    lines = ["Отметить оплату всех долгов?", ""]
    for creditor, amount in totals_by_creditor(debts):
        lines.append(f"{escape(display_name(creditor))} — {amount:.2f}")
    lines.extend(["", "Получатели подтвердят оплату каждого расхода."])
    builder = InlineKeyboardBuilder()
    builder.button(text="Подтвердить", callback_data="payment:all:confirm")
    builder.button(text="Назад", callback_data="menu:debts")
    builder.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "menu:confirmations")
async def user_confirmations(callback: CallbackQuery) -> None:
    user = await ensure_user(callback)
    pending = await collect_pending_confirmations(user)
    total = sum(
        (Decimal(str(participant.share_amount)) for _, participant in pending),
        Decimal("0"),
    )
    lines = [f"✅ Ожидают подтверждения: {len(pending)}", f"Сумма: {total:.2f}"]
    if pending:
        lines.append("")
        for expense, participant in pending:
            lines.append(
                f"{escape(display_name(participant.user))} — "
                f"{participant.share_amount:.2f} "
                f"({escape(expense.description or f'Расход #{expense.id}')})"
            )
    else:
        lines.extend(["", "Нет оплат для подтверждения."])
    builder = InlineKeyboardBuilder()
    if pending:
        builder.button(
            text="Подтвердить все оплаты", callback_data="payment:confirm_all:preview"
        )
    builder.button(text="Главное меню", callback_data="menu:main")
    builder.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "payment:confirm_all:preview")
async def preview_all_confirmations(callback: CallbackQuery) -> None:
    user = await ensure_user(callback)
    pending = await collect_pending_confirmations(user)
    if not pending:
        await callback.answer("Оплат для подтверждения нет", show_alert=True)
        return
    total = sum(
        (Decimal(str(participant.share_amount)) for _, participant in pending),
        Decimal("0"),
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="Подтвердить", callback_data="payment:confirm_all:confirm")
    builder.button(text="Назад", callback_data="menu:confirmations")
    builder.adjust(1)
    await callback.message.edit_text(
        f"Подтвердить все отмеченные оплаты?\n\nРасходов: {len(pending)}\nСумма: {total:.2f}",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "payment:confirm_all:confirm")
async def confirm_all_payments(callback: CallbackQuery) -> None:
    user = await ensure_user(callback)
    pending = await collect_pending_confirmations(user)
    if not pending:
        await callback.answer("Оплат для подтверждения нет", show_alert=True)
        return
    service = ExpenseService.get_as_dependency()
    for expense, participant in pending:
        await service.confirm_payment(
            ConfirmPaymentRequest(
                expense_id=expense.id, participant_tg_id=participant.user.tg_id
            ),
            user,
        )
    await callback.message.edit_text(
        f"✅ Подтверждено оплат: {len(pending)}.", reply_markup=main_menu()
    )
    await callback.answer()


@router.callback_query(F.data == "payment:all:confirm")
async def mark_all_paid(callback: CallbackQuery) -> None:
    user = await ensure_user(callback)
    service = ExpenseService.get_as_dependency()
    debts = await collect_debts(user, service)
    if not debts:
        await callback.answer("Непогашенных долгов нет", show_alert=True)
        return
    for expense, participant in debts:
        updated = await service.mark_participant_paid(
            MarkParticipantPaidRequest(
                expense_id=expense.id, amount=participant.remaining_amount
            ),
            user,
        )
        await notify_payment(callback.bot, expense, user, updated)
    await callback.message.edit_text(
        f"Отмечена оплата {len(debts)} расходов. Получатели смогут подтвердить её в боте.",
        reply_markup=main_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("payment:create:"))
async def create_payment(callback: CallbackQuery) -> None:
    expense_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    service = ExpenseService.get_as_dependency()
    expense = await service.get_expense_detail(expense_id)
    await require_event_member(expense.event_id, user)
    debt = next(
        (item for item in expense.participants if item.user.tg_id == user.tg_id),
        None,
    )
    if not debt or debt.remaining_amount <= 0:
        await callback.answer("Непогашенного долга нет", show_alert=True)
        return
    updated = await service.mark_participant_paid(
        MarkParticipantPaidRequest(expense_id=expense_id, amount=debt.remaining_amount),
        user,
    )
    await callback.message.edit_text(
        "✅ Оплата расхода отмечена полностью. Получатель сможет подтвердить её.",
        reply_markup=main_menu(),
    )
    await callback.answer()
    await notify_payment(callback.bot, expense, user, updated)


@router.callback_query(F.data.startswith("payment:confirm:"))
async def confirm_payment(callback: CallbackQuery) -> None:
    _, _, expense_id, participant_tg_id = callback.data.split(":", 3)
    user = await ensure_user(callback)
    await ExpenseService.get_as_dependency().confirm_payment(
        ConfirmPaymentRequest(
            expense_id=int(expense_id), participant_tg_id=participant_tg_id
        ),
        user,
    )
    await callback.message.edit_text("Оплата подтверждена.", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("expense:delete:"))
async def delete_expense(callback: CallbackQuery) -> None:
    expense_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    expense = await ExpenseService.get_as_dependency().delete_expense(expense_id, user)
    await callback.message.edit_text("Расход удалён.", reply_markup=event_menu(expense.event_id))
    await callback.answer()


@router.error()
async def handle_error(event: ErrorEvent) -> bool:
    logger.exception("Telegram update failed", exc_info=event.exception)
    update = event.update
    if update.callback_query:
        await update.callback_query.answer(str(event.exception), show_alert=True)
    elif update.message:
        await update.message.answer(f"Не удалось выполнить действие: {event.exception}")
    return True
