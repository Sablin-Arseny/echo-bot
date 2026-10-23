from decimal import ROUND_DOWN, Decimal
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.expenses import notify_new_expense
from app.bot.keyboards import (
    cancel_menu,
    confirmation_menu,
    event_menu,
    participants_menu,
    split_mode_menu,
)
from app.bot.labels import EXPENSE_STATUS, PAYMENT_STATUS
from app.bot.states import ExpenseForm
from app.src.schemas import CreateExpenseRequest, ExpenseParticipantRequest
from app.src.services.event import EventService
from app.src.services.expense import ExpenseService
from app.src.services.settlement import SettlementService

from .common import ensure_user, parse_amount, require_event_member
from .settlements import notify_settlement

router = Router()


def expense_text(expense) -> str:
    status = EXPENSE_STATUS[expense.status]
    if (
        expense.status != "DELETED"
        and expense.participants
        and all(p.status in {"PAID", "CONFIRMED"} for p in expense.participants)
        and any(p.status == "PAID" for p in expense.participants)
    ):
        status = "Ожидает подтверждения оплат"
    lines = [
        f"<b>{escape(expense.description or 'Расход')}</b>",
        f"Сумма: {expense.amount:.2f}",
        f"Оплатил: {escape(expense.paid_by.full_name or expense.paid_by.username or expense.paid_by.tg_id)}",
        f"Статус: {status}",
        "",
    ]
    for participant in expense.participants:
        name = (
            participant.user.full_name
            or participant.user.username
            or participant.user.tg_id
        )
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


@router.callback_query(F.data.startswith("event:expenses:"))
@router.callback_query(F.data.startswith("event:expense_archive:"))
async def event_expenses(callback: CallbackQuery) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    await require_event_member(event_id, user, allow_removed=True)
    event = await EventService.get_as_dependency().get(event_id)
    archived = callback.data.startswith("event:expense_archive:")
    all_expenses = [
        expense
        async for expense in ExpenseService.get_as_dependency().get(event_id)
        if expense.status != "DELETED"
    ]
    expenses = [
        expense for expense in all_expenses if (expense.status == "CLOSED") == archived
    ]
    total = sum((Decimal(str(expense.amount)) for expense in all_expenses), Decimal(0))
    visible_total = sum(
        (Decimal(str(expense.amount)) for expense in expenses), Decimal(0)
    )
    builder = InlineKeyboardBuilder()
    for expense in expenses:
        label = expense.description or f"Расход #{expense.id}"
        marker = "✅ " if expense.status == "CLOSED" else ""
        builder.button(
            text=f"{marker}{label} — {expense.amount:.2f}",
            callback_data=f"expense:view:{expense.id}",
        )
    builder.adjust(1)
    members = await EventService.get_as_dependency().get_participants(event_id)
    active_member = any(
        p.id == user.id and p.status == "PARTICIPATING" for p in members
    )
    if not event.finished_at and active_member and not archived:
        builder.row(
            InlineKeyboardButton(
                text="Добавить расход", callback_data=f"expense:create:{event_id}"
            )
        )
    if not archived:
        builder.row(
            InlineKeyboardButton(
                text="🗂 Архив расходов",
                callback_data=f"event:expense_archive:{event_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="Назад",
            callback_data=(
                f"event:expenses:{event_id}" if archived else f"event:view:{event_id}"
            ),
        )
    )
    text = "🗂 Архив расходов" if archived else "💰 Расходы события"
    text += f"\n{'Закрытые' if archived else 'Открытые'} расходы: {visible_total:.2f}\nВсего по событию: {total:.2f}"
    if not expenses:
        text += (
            "\n\nЗакрытых расходов пока нет."
            if archived
            else "\n\nОткрытых расходов нет."
        )
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("expense:view:"))
async def view_expense(callback: CallbackQuery) -> None:
    expense_id = int(callback.data.rsplit(":", 1)[1])
    expense = await ExpenseService.get_as_dependency().get_expense_detail(expense_id)
    user = await ensure_user(callback)
    await require_event_member(expense.event_id, user, allow_removed=True)
    event = await EventService.get_as_dependency().get(expense.event_id)
    members = await EventService.get_as_dependency().get_participants(expense.event_id)
    active_member = any(
        p.id == user.id and p.status == "PARTICIPATING" for p in members
    )
    builder = InlineKeyboardBuilder()
    participant = next(
        (item for item in expense.participants if item.user.tg_id == user.tg_id), None
    )
    reserved = await ExpenseService.get_as_dependency().has_pending_settlement(
        expense_id
    )
    own_reserved = (
        reserved
        and await ExpenseService.get_as_dependency().has_pending_settlement(
            expense_id, user.id
        )
    )
    if (
        participant
        and participant.remaining_amount > 0
        and expense.status != "DELETED"
        and not own_reserved
    ):
        builder.button(
            text="Отметить оплату", callback_data=f"payment:create:{expense_id}"
        )
    if expense.paid_by.id == user.id:
        for item in expense.participants:
            if item.status == "PAID" and expense.status != "DELETED":
                name = item.user.full_name or item.user.username or item.user.tg_id
                builder.button(
                    text=f"Подтвердить: {name}",
                    callback_data=f"payment:confirm:{expense_id}:{item.user.tg_id}",
                )
        if not event.finished_at and active_member and expense.status != "DELETED":
            if (
                not reserved
                and expense.status != "CLOSED"
                and not any(
                    p.user.id != user.id and p.paid_amount > 0
                    for p in expense.participants
                )
            ):
                builder.button(
                    text="Редактировать расход",
                    callback_data=f"expense:edit:{expense_id}",
                )
            builder.button(
                text="Удалить расход", callback_data=f"expense:delete:{expense_id}"
            )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="💱 Взаиморасчёты",
            callback_data=f"event:settlements:{expense.event_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Назад",
            callback_data=(
                f"event:expense_archive:{expense.event_id}"
                if expense.status == "CLOSED"
                else f"event:expenses:{expense.event_id}"
            ),
        )
    )
    await callback.message.edit_text(
        expense_text(expense)
        + (
            "\n\n💱 Есть доли во взаиморасчёте, ожидающем подтверждения."
            if reserved
            else ""
        ),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("expense:edit:"))
async def edit_expense(callback: CallbackQuery, state: FSMContext) -> None:
    expense_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    expense = await ExpenseService.get_as_dependency().require_editable(
        expense_id, user
    )
    await require_event_member(expense.event_id, user)
    await state.clear()
    await state.update_data(event_id=expense.event_id, expense_id=expense_id)
    await state.set_state(ExpenseForm.description)
    await callback.message.edit_text(
        f"Текущий расход\n\n{expense_text(expense)}\n\nВведите новое описание расхода",
        reply_markup=cancel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("expense:create:"))
async def create_expense(callback: CallbackQuery, state: FSMContext) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    await require_event_member(event_id, user)
    await EventService.get_as_dependency().require_active(event_id)
    await state.clear()
    await state.update_data(event_id=event_id)
    await state.set_state(ExpenseForm.description)
    await callback.message.edit_text(
        "Введите описание расхода", reply_markup=cancel_menu()
    )
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
    participants = await EventService.get_as_dependency().get_participants(
        data["event_id"]
    )
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
    participants = await EventService.get_as_dependency().get_participants(
        data["event_id"]
    )
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
        item
        for item in data["available_participants"]
        if item["tg_id"] in data["selected"]
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
    assigned = sum((Decimal(value) for value in shares.values()), Decimal(0))
    remaining = total - assigned
    if amount >= remaining:
        await message.answer(
            f"Оставьте положительную долю последнему участнику. Доступно меньше {remaining:.2f}."
        )
        return
    shares[participants[index]["tg_id"]] = str(amount)
    index += 1
    if index == len(participants) - 1:
        assigned = sum((Decimal(value) for value in shares.values()), Decimal(0))
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
    return [
        base + (Decimal("0.01") if index < remainder else 0) for index in range(count)
    ]


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
        lines.append(
            f"{escape(participant['name'])}: {shares[participant['tg_id']]:.2f}"
        )
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
                tg_id=item["tg_id"], share_amount=Decimal(data["shares"][item["tg_id"]])
            )
            for item in participants
        ]
    request = CreateExpenseRequest(
        event_id=data["event_id"],
        amount=Decimal(data["amount"]),
        is_equally=data["mode"] == "equal",
        description=data["description"],
        participants=expense_participants,
    )
    user = await ensure_user(callback)
    await require_event_member(data["event_id"], user)
    service = ExpenseService.get_as_dependency()
    if data.get("expense_id"):
        expense = await service.update_expense(data["expense_id"], request, user)
    else:
        expense = await service.create_expense_with_participants(request, user)
    await state.clear()
    await callback.message.edit_text(
        expense_text(expense), reply_markup=event_menu(expense.event_id)
    )
    await callback.answer()
    event = await EventService.get_as_dependency().get(expense.event_id)
    await notify_new_expense(
        callback.bot, event.name, expense, edited=bool(data.get("expense_id"))
    )


@router.callback_query(F.data.startswith("expense:delete:"))
async def preview_delete_expense(callback: CallbackQuery) -> None:
    expense_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    expense = await ExpenseService.get_as_dependency().get_expense_detail(expense_id)
    if expense.paid_by.id != user.id:
        raise PermissionError("Удалять расход может только его автор")
    await require_event_member(expense.event_id, user)
    await EventService.get_as_dependency().require_active(expense.event_id)
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Удалить расход", callback_data=f"expense:delete_confirm:{expense_id}"
    )
    builder.button(text="Назад", callback_data=f"expense:view:{expense_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        f"Удалить расход «{escape(expense.description or 'Расход')}» на {expense.amount:.2f}?\n\n"
        "Он будет исключён из списка расходов и расчёта долгов. "
        "Связанные неподтверждённые взаиморасчёты отменятся. Уже выполненные переводы не возвращаются.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("expense:delete_confirm:"))
async def delete_expense(callback: CallbackQuery) -> None:
    expense_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    service = SettlementService.get_as_dependency()
    before = {s.id for s in await service.history(user) if s.status == "PENDING"}
    expense = await ExpenseService.get_as_dependency().delete_expense(expense_id, user)
    await callback.message.edit_text(
        "Расход удалён.", reply_markup=event_menu(expense.event_id)
    )
    await callback.answer()
    for settlement in await service.history(user, event_id=expense.event_id):
        if settlement.id in before and settlement.status == "CANCELLED":
            other = (
                settlement.recipient
                if settlement.payer.id == user.id
                else settlement.payer
            )
            await notify_settlement(
                callback.bot,
                settlement,
                "Расчёт отменён из-за удаления исходного расхода. Проверьте долги перед новым переводом.",
                other.tg_id,
            )
