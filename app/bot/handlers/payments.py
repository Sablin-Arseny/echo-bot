from decimal import Decimal
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.expenses import (
    collect_debts,
    collect_pending_confirmations,
    display_name,
    notify_payment,
    totals_by_creditor,
)
from app.bot.keyboards import main_menu
from app.src.schemas import ConfirmPaymentRequest, MarkParticipantPaidRequest
from app.src.services.expense import ExpenseService

from .common import ensure_user, require_event_member

router = Router()


@router.callback_query(F.data == "menu:debts")
async def user_debts(callback: CallbackQuery) -> None:
    user = await ensure_user(callback)
    debts = await collect_debts(user)
    total = sum(
        (Decimal(str(participant.remaining_amount)) for _, participant in debts),
        Decimal(0),
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
        Decimal(0),
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
        Decimal(0),
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
