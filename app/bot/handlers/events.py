from datetime import date, datetime, time
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.deep_linking import create_start_link
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.calendar import date_input_menu, hour_menu, minute_menu, month_menu
from app.bot.invites import invite_payload, invited_event_id
from app.bot.keyboards import cancel_menu, confirmation_menu, event_menu, skip_menu
from app.bot.labels import EVENT_STATUS
from app.bot.states import EventEditForm, EventForm
from app.src.schemas import CreateEventRequest, UpdateEvent
from app.src.services.event import EventService

from .common import ensure_user, require_event_member

router = Router()
MOSCOW = ZoneInfo("Europe/Moscow")


def event_text(event) -> str:
    start_date = (
        event.start_date.astimezone(MOSCOW)
        if event.start_date.tzinfo
        else event.start_date
    )
    lines = [
        f"<b>{escape(event.name)}</b>",
        format_event_date(start_date),
    ]
    if event.event_place:
        lines.append(escape(event.event_place))
    if event.description:
        lines.extend(["", escape(event.description)])
    if getattr(event, "finished_at", None):
        lines.extend(["", "🗂 Событие завершено. Можно погашать существующие долги."])
    return "\n".join(lines)


def format_event_date(value: datetime) -> str:
    pattern = "%d.%m.%Y" if value.time() == time.min else "%d.%m.%Y %H:%M"
    return value.strftime(pattern)


@router.callback_query(F.data.startswith("event:view:"))
async def view_event(callback: CallbackQuery) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    await require_event_member(event_id, user, allow_removed=True)
    event = await EventService.get_as_dependency().get(event_id)
    if not event:
        await callback.answer("Событие не найдено", show_alert=True)
        return
    participants = await EventService.get_as_dependency().get_participants(event_id)
    participants = [p for p in participants if p.status != "DELETED"]
    await callback.message.edit_text(
        f"{event_text(event)}\n\nУчастников: {len(participants)}",
        reply_markup=event_menu(
            event_id,
            finished=bool(event.finished_at),
            owner=any(p.id == user.id and p.role == "OWNER" for p in participants),
            member=any(
                p.id == user.id and p.status == "PARTICIPATING" for p in participants
            ),
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "event:create")
async def create_event(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(EventForm.name)
    await callback.message.edit_text(
        "Введите название события", reply_markup=cancel_menu()
    )
    await callback.answer()


@router.message(EventForm.name)
async def event_name(message: Message, state: FSMContext) -> None:
    if not message.text or not message.text.strip():
        await message.answer("Название не может быть пустым.")
        return
    await state.update_data(name=message.text.strip())
    await state.set_state(EventForm.description)
    await message.answer(
        "Введите описание", reply_markup=skip_menu("event_description")
    )


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


async def ask_event_place(
    message: Message, state: FSMContext, edit: bool = False
) -> None:
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
    start_date = datetime.combine(
        date.fromisoformat(data["selected_date"]), time.min, MOSCOW
    )
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
        selected_time = datetime.strptime((message.text or "").strip(), "%H:%M").time()
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
        reply_markup=event_menu(event.id, owner=True),
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
    await service.require_active(event_id)
    participants = await service.get_participants(event_id)
    current = next(
        (participant for participant in participants if participant.id == user.id), None
    )
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
    await EventService.get_as_dependency().require_active(event_id)
    link = await create_start_link(callback.bot, invite_payload(event_id), encode=False)
    await callback.message.answer(f"Ссылка для приглашения:\n{link}")
    await callback.answer()


@router.callback_query(F.data.startswith("event:members:"))
async def event_members(callback: CallbackQuery) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    await require_event_member(event_id, user, allow_removed=True)
    participants = await EventService.get_as_dependency().get_participants(event_id)
    participants = [p for p in participants if p.status != "DELETED"]
    lines = ["<b>👥 Участники</b>", ""]
    for participant in participants:
        name = participant.full_name or participant.username or participant.tg_id
        lines.append(f"{escape(name)} — {EVENT_STATUS[participant.status]}")
    builder = InlineKeyboardBuilder()
    event = await EventService.get_as_dependency().get(event_id)
    if not event.finished_at and any(
        p.id == user.id and p.status == "PARTICIPATING" for p in participants
    ):
        if any(p.id == user.id and p.role == "OWNER" for p in participants):
            for participant in participants:
                if participant.role != "OWNER":
                    name = (
                        participant.full_name
                        or participant.username
                        or participant.tg_id
                    )
                    builder.button(
                        text=f"Удалить — {name}",
                        callback_data=f"event:remove:{event_id}:{participant.id}",
                    )
        builder.button(text="🔗 Пригласить", callback_data=f"event:invite:{event_id}")
    builder.button(text="Назад", callback_data=f"event:view:{event_id}")
    builder.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("event:remove:"))
async def preview_remove_member(callback: CallbackQuery) -> None:
    _, _, event_id, member_id = callback.data.split(":")
    service = EventService.get_as_dependency()
    await service.require_owner(int(event_id), await ensure_user(callback))
    await service.require_active(int(event_id))
    member = next(
        (
            p
            for p in await service.get_participants(int(event_id))
            if p.id == int(member_id)
        ),
        None,
    )
    if not member or member.status == "DELETED" or member.role == "OWNER":
        await callback.answer("Участника нельзя удалить", show_alert=True)
        return
    name = member.full_name or member.username or member.tg_id
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Удалить участника",
        callback_data=f"event:remove_confirm:{event_id}:{member_id}",
    )
    builder.button(text="Назад", callback_data=f"event:members:{event_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        f"Удалить участника {escape(name)}?\n\nЕго старые расходы и долги сохранятся. "
        "Вернуться по приглашению он не сможет.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("event:remove_confirm:"))
async def remove_member(callback: CallbackQuery) -> None:
    _, _, event_id, member_id = callback.data.split(":")
    await EventService.get_as_dependency().remove_member(
        int(event_id), int(member_id), await ensure_user(callback)
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="Участники", callback_data=f"event:members:{event_id}")
    await callback.message.edit_text(
        "Участник удалён. Его долги и история сохранены.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("event:finish:"))
async def preview_finish_event(callback: CallbackQuery) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    service = EventService.get_as_dependency()
    await service.require_owner(event_id, user)
    event = await service.require_active(event_id)
    builder = InlineKeyboardBuilder()
    builder.button(text="Завершить", callback_data=f"event:finish_confirm:{event_id}")
    builder.button(text="Назад", callback_data=f"event:view:{event_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        f"Завершить событие «{escape(event.name)}»?\n\n"
        "Оно попадёт в архив. Изменять событие, участников и расходы больше нельзя. "
        "Старые долги можно погашать. Повторное открытие недоступно.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("event:finish_confirm:"))
async def finish_event(callback: CallbackQuery) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    event = await EventService.get_as_dependency().finish(
        event_id, await ensure_user(callback)
    )
    await callback.message.edit_text(
        event_text(event), reply_markup=event_menu(event_id, finished=True)
    )
    await callback.answer("Событие отправлено в архив")


EDIT_FIELDS = {
    "name": "название",
    "description": "описание",
    "start_date": "дату",
    "event_place": "место",
}


@router.callback_query(F.data.startswith("event:edit:"))
async def edit_event(callback: CallbackQuery, state: FSMContext) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    service = EventService.get_as_dependency()
    await service.require_owner(event_id, await ensure_user(callback))
    await service.require_active(event_id)
    await state.clear()
    builder = InlineKeyboardBuilder()
    for field, label in EDIT_FIELDS.items():
        builder.button(
            text="Дата" if field == "start_date" else label.capitalize(),
            callback_data=f"event:field:{event_id}:{field}",
        )
    builder.button(text="Назад", callback_data=f"event:view:{event_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        "Что изменить в событии?", reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("event:field:"))
async def edit_event_field(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, event_id, field = callback.data.split(":")
    if field not in EDIT_FIELDS:
        return
    service = EventService.get_as_dependency()
    await service.require_owner(int(event_id), await ensure_user(callback))
    event = await service.require_active(int(event_id))
    await state.clear()
    await state.update_data(event=event.model_dump(mode="json"), field=field)
    await state.set_state(EventEditForm.value)
    hint = " ДД.ММ.ГГГГ или ДД.ММ.ГГГГ ЧЧ:ММ (МСК)" if field == "start_date" else ""
    if field in {"description", "event_place"}:
        hint = ". Отправьте «-», чтобы очистить"
    await callback.message.edit_text(
        f"Введите {'новую' if field == 'start_date' else 'новое'} {EDIT_FIELDS[field]}{hint}",
        reply_markup=cancel_menu(),
    )
    await callback.answer()


@router.message(EventEditForm.value)
async def event_edit_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data["field"]
    value = (message.text or "").strip()
    if not value:
        await message.answer("Введите значение текстом")
        return
    if field == "start_date":
        try:
            value = parse_event_date(value).isoformat()
        except ValueError as error:
            await message.answer(str(error))
            return
    elif field in {"description", "event_place"} and value == "-":
        value = None
    draft = data["event"]
    draft[field] = value
    await state.update_data(event=draft)
    await state.set_state(EventEditForm.confirmation)
    event = UpdateEvent.model_validate(draft)
    await message.answer(
        f"Проверьте изменения\n\n{event_text(event)}",
        reply_markup=confirmation_menu("event_edit"),
    )


@router.callback_query(EventEditForm.confirmation, F.data == "save:event_edit")
async def save_event_edit(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    event = await EventService.get_as_dependency().update(
        UpdateEvent.model_validate(data["event"]), await ensure_user(callback)
    )
    await state.clear()
    await callback.message.edit_text(
        f"✅ Событие обновлено\n\n{event_text(event)}",
        reply_markup=event_menu(event.id, owner=True),
    )
    await callback.answer()
