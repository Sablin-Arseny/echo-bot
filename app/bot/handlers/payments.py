from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.expenses import (
    collect_pending_confirmations,
    display_name,
    notify_payment,
)
from app.bot.keyboards import main_menu
from app.bot.states import SettlementForm
from app.src.schemas import ConfirmPaymentRequest, MarkParticipantPaidRequest
from app.src.schemas.settlement import SettlementQuote
from app.src.services.expense import ExpenseService
from app.src.services.settlement import SettlementService

from .common import ensure_user, require_event_member
from .settlements import notify_settlement

router = Router()


@router.callback_query(F.data == "menu:debts")
async def user_debts(callback: CallbackQuery, state: FSMContext) -> None:
    user = await ensure_user(callback)
    await state.clear()
    quotes = await SettlementService.get_as_dependency().balances(user)
    payable = [q for q in quotes if not q.blocked and q.amount >= 0 and q.outgoing > 0]
    total = sum((q.amount for q in payable), Decimal(0))
    builder = InlineKeyboardBuilder()
    lines = [f"💳 К оплате: {total:.2f}", ""]
    creditors = {}
    for quote in payable:
        key = quote.counterparty.id
        previous = creditors.get(key, (quote.counterparty, Decimal(0)))[1]
        creditors[key] = (quote.counterparty, previous + quote.amount)
    for creditor, amount in creditors.values():
        lines.append(f"Всего для {escape(display_name(creditor))} — {amount:.2f}")
    lines.extend(["", "По событиям"])
    for quote in quotes:
        name = display_name(quote.counterparty)
        label = f"{escape(quote.event_name)} — {escape(name)}"
        if quote.blocked:
            lines.append(f"{label}: есть неподтверждённая оплата")
        elif quote.amount > 0:
            lines.append(f"{label}: вы должны {quote.amount:.2f}")
        elif quote.amount < 0:
            lines.append(f"{label}: вам должны {-quote.amount:.2f}")
        else:
            lines.append(f"{label}: доступен взаимозачёт {quote.outgoing:.2f}")
        builder.button(
            text=f"{quote.event_name} — {name}",
            callback_data=f"event:settlements:{quote.event_id}",
        )
    builder.adjust(1)
    if payable:
        builder.row(
            InlineKeyboardButton(
                text="Отметить всё оплаченным", callback_data="payment:all:preview"
            )
        )
    builder.row(InlineKeyboardButton(text="Главное меню", callback_data="menu:main"))
    text = "\n".join(lines) if quotes else "У вас нет непогашенных обязательств."
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "payment:all:preview")
async def preview_all_payments(callback: CallbackQuery, state: FSMContext) -> None:
    user = await ensure_user(callback)
    quotes = [
        q
        for q in await SettlementService.get_as_dependency().balances(user)
        if not q.blocked and q.amount >= 0 and q.outgoing > 0
    ]
    if not quotes:
        await callback.answer("Непогашенных долгов нет", show_alert=True)
        return
    await state.clear()
    await state.update_data(quotes=[q.model_dump(mode="json") for q in quotes])
    await state.set_state(SettlementForm.batch)
    lines = ["Отметить переводы и взаимозачёты?", ""]
    for quote in quotes:
        lines.append(
            f"{escape(quote.event_name)} — {escape(display_name(quote.counterparty))}: "
            f"перевод {quote.amount:.2f}, зачёт {quote.incoming:.2f}"
        )
    lines.extend(
        ["", "Отмечайте только выполненные переводы. Получатели подтвердят расчёты."]
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="Подтвердить", callback_data="payment:all:confirm")
    builder.button(text="Назад", callback_data="menu:debts")
    builder.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "menu:confirmations")
async def user_confirmations(callback: CallbackQuery, state: FSMContext) -> None:
    user = await ensure_user(callback)
    await state.clear()
    pending = await collect_pending_confirmations(user)
    settlements = [
        s
        for s in await SettlementService.get_as_dependency().history(user)
        if s.status == "PENDING" and s.recipient.id == user.id
    ]
    total = sum(
        (Decimal(str(participant.share_amount)) for _, participant in pending),
        Decimal(0),
    )
    total += sum((s.amount for s in settlements), Decimal(0))
    lines = [
        f"✅ Ожидают подтверждения: {len(pending) + len(settlements)}",
        f"Сумма переводов: {total:.2f}",
    ]
    if pending:
        lines.append("")
        for expense, participant in pending:
            lines.append(
                f"{escape(display_name(participant.user))} — "
                f"{participant.share_amount:.2f} "
                f"({escape(expense.description or f'Расход #{expense.id}')})"
            )
    elif not settlements:
        lines.extend(["", "Нет оплат для подтверждения."])
    builder = InlineKeyboardBuilder()
    for settlement in settlements:
        name = display_name(settlement.payer)
        lines.append(
            f"{escape(settlement.event_name)} — {escape(name)}: перевод {settlement.amount:.2f}, взаимозачёт {settlement.offset_amount:.2f}"
        )
        builder.button(
            text=f"Расчёт — {name} — {settlement.amount:.2f}",
            callback_data=f"settlement:view:{settlement.id}",
        )
    for expense, participant in pending:
        builder.button(
            text=f"{expense.description or 'Расход'} — {display_name(participant.user)}",
            callback_data=f"expense:view:{expense.id}",
        )
    if pending or settlements:
        builder.button(
            text="Подтвердить все оплаты", callback_data="payment:confirm_all:preview"
        )
    builder.button(text="Главное меню", callback_data="menu:main")
    builder.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data == "payment:confirm_all:preview")
async def preview_all_confirmations(callback: CallbackQuery, state: FSMContext) -> None:
    user = await ensure_user(callback)
    pending = await collect_pending_confirmations(user)
    settlements = [
        s
        for s in await SettlementService.get_as_dependency().history(user)
        if s.status == "PENDING" and s.recipient.id == user.id
    ]
    if not pending and not settlements:
        await callback.answer("Оплат для подтверждения нет", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        direct=[
            {"expense_id": e.id, "participant_tg_id": p.user.tg_id} for e, p in pending
        ],
        settlements=[s.id for s in settlements],
    )
    await state.set_state(SettlementForm.confirmations)
    lines = ["Подтвердить перечисленные оплаты?", ""]
    for expense, participant in pending:
        lines.append(
            f"{escape(display_name(participant.user))} — {escape(expense.description or 'Расход')}: {participant.share_amount:.2f}"
        )
    for settlement in settlements:
        lines.append(
            f"{escape(settlement.event_name)} — {escape(display_name(settlement.payer))}: "
            f"перевод {settlement.amount:.2f}, зачёт {settlement.offset_amount:.2f}"
        )
    builder = InlineKeyboardBuilder()
    builder.button(text="Подтвердить", callback_data="payment:confirm_all:confirm")
    builder.button(text="Назад", callback_data="menu:confirmations")
    builder.adjust(1)
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(
    SettlementForm.confirmations, F.data == "payment:confirm_all:confirm"
)
async def confirm_all_payments(callback: CallbackQuery, state: FSMContext) -> None:
    user = await ensure_user(callback)
    data = await state.get_data()
    service = ExpenseService.get_as_dependency()
    completed, failed, notifications = 0, [], []
    for request in data.get("direct", []):
        try:
            await service.confirm_payment(
                ConfirmPaymentRequest.model_validate(request), user
            )
            completed += 1
        except (ValueError, PermissionError) as error:
            failed.append(f"Расход #{request['expense_id']}: {escape(str(error))}")
    settlements = SettlementService.get_as_dependency()
    for settlement_id in data.get("settlements", []):
        try:
            await settlements.confirm(user, settlement_id)
            completed += 1
            notifications.extend(
                await settlements.history(user, settlement_id=settlement_id)
            )
        except (ValueError, PermissionError) as error:
            failed.append(f"Расчёт #{settlement_id}: {escape(str(error))}")
    await state.clear()
    text = f"✅ Подтверждено оплат и расчётов: {completed}."
    if failed:
        text += "\n\nНе выполнены\n" + "\n".join(failed)
    await callback.message.edit_text(text, reply_markup=main_menu())
    await callback.answer()
    for settlement in notifications:
        await notify_settlement(
            callback.bot,
            settlement,
            "Получатель подтвердил расчёт.",
            settlement.payer.tg_id,
        )


@router.callback_query(SettlementForm.batch, F.data == "payment:all:confirm")
async def mark_all_paid(callback: CallbackQuery, state: FSMContext) -> None:
    user = await ensure_user(callback)
    data = await state.get_data()
    service = SettlementService.get_as_dependency()
    created, failed = [], []
    for stored in data.get("quotes", []):
        quote = SettlementQuote.model_validate(stored)
        try:
            created.append(await service.create(user, quote))
        except (ValueError, PermissionError) as error:
            failed.append(
                f"{escape(quote.event_name)} — {escape(display_name(quote.counterparty))}: {escape(str(error))}"
            )
    await state.clear()
    text = f"✅ Отправлено на подтверждение расчётов: {len(created)}."
    if failed:
        text += "\n\nНе выполнены\n" + "\n".join(failed)
    await callback.message.edit_text(
        text,
        reply_markup=main_menu(),
    )
    await callback.answer()
    for settlement in created:
        await notify_settlement(
            callback.bot,
            settlement,
            "Вам отправлен расчёт. Откройте «Подтверждения».",
            settlement.recipient.tg_id,
        )


@router.callback_query(F.data.startswith("payment:create:"))
async def create_payment(callback: CallbackQuery) -> None:
    expense_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    service = ExpenseService.get_as_dependency()
    expense = await service.get_expense_detail(expense_id)
    await require_event_member(expense.event_id, user, allow_removed=True)
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
