import logging
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.expenses import display_name
from app.bot.states import SettlementForm
from app.src.schemas.settlement import SettlementQuote
from app.src.services.settlement import SettlementService

from .common import ensure_user, require_event_member

router = Router()
logger = logging.getLogger(__name__)
SETTLEMENT_STATUS = {
    "PENDING": "Ожидает подтверждения",
    "CONFIRMED": "Подтверждён",
    "CANCELLED": "Отменён",
}


def settlement_text(settlement):
    payer = escape(display_name(settlement.payer))
    recipient = escape(display_name(settlement.recipient))
    lines = [
        f"💱 Расчёт в событии «{escape(settlement.event_name)}»",
        "",
        f"{payer} → {recipient}",
        f"Перевод: {settlement.amount:.2f}",
        f"Взаимозачёт: {settlement.offset_amount:.2f}",
        "",
        f"Погашается {payer} → {recipient}: {settlement.amount + settlement.offset_amount:.2f}",
    ]
    if settlement.offset_amount:
        lines.append(
            f"Погашается {recipient} → {payer}: {settlement.offset_amount:.2f}"
        )
    if not settlement.amount:
        lines.append("Денежный перевод не требуется.")
    lines.extend(["", SETTLEMENT_STATUS[settlement.status]])
    return "\n".join(lines)


async def notify_settlement(bot, settlement, text, target_id):
    try:
        await bot.send_message(target_id, f"🔔 {text}\n\n{settlement_text(settlement)}")
    except TelegramAPIError:
        logger.warning(
            "Could not send settlement notification %s", settlement.id, exc_info=True
        )


@router.callback_query(F.data.startswith("event:settlements:"))
async def event_settlements(callback: CallbackQuery, state: FSMContext) -> None:
    event_id = int(callback.data.rsplit(":", 1)[1])
    user = await ensure_user(callback)
    await require_event_member(event_id, user, allow_removed=True)
    await state.clear()
    service = SettlementService.get_as_dependency()
    quotes = await service.balances(user, event_id)
    history = await service.history(user, event_id)
    lines = ["💱 Ваши взаиморасчёты в событии", ""]
    builder = InlineKeyboardBuilder()
    for quote in quotes:
        name = display_name(quote.counterparty)
        if quote.blocked:
            lines.append(f"{escape(name)} — есть неподтверждённая оплата")
        elif quote.amount > 0:
            lines.append(f"Вы должны {escape(name)} — {quote.amount:.2f}")
        elif quote.amount < 0:
            lines.append(f"{escape(name)} должен вам — {-quote.amount:.2f}")
        elif quote.outgoing:
            lines.append(
                f"{escape(name)} — взаимные долги равны, можно зачесть {quote.outgoing:.2f}"
            )
        if not quote.blocked and quote.amount >= 0 and quote.outgoing > 0:
            builder.button(
                text=f"{'Взаимозачёт' if not quote.amount else 'Оплата'} — {name}",
                callback_data=f"settlement:preview:{event_id}:{quote.counterparty.id}",
            )
    if not quotes:
        lines.append("Непогашенных обязательств нет.")
    for settlement in history:
        other = (
            settlement.recipient if settlement.payer.id == user.id else settlement.payer
        )
        builder.button(
            text=f"{SETTLEMENT_STATUS[settlement.status]} — {display_name(other)} — {settlement.amount:.2f}",
            callback_data=f"settlement:view:{settlement.id}",
        )
    builder.button(text="Назад", callback_data=f"event:view:{event_id}")
    builder.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("settlement:preview:"))
async def preview_settlement(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, event_id, other_id = callback.data.split(":")
    user = await ensure_user(callback)
    quotes = await SettlementService.get_as_dependency().balances(user, int(event_id))
    quote = next((q for q in quotes if q.counterparty.id == int(other_id)), None)
    if not quote or quote.blocked or quote.amount < 0 or not quote.outgoing:
        await callback.answer(
            "Расчёт недоступен. Обновите список долгов", show_alert=True
        )
        return
    await state.clear()
    await state.update_data(quote=quote.model_dump(mode="json"))
    await state.set_state(SettlementForm.single)
    name = escape(display_name(quote.counterparty))
    builder = InlineKeyboardBuilder()
    builder.button(
        text=(
            f"Я перевёл {quote.amount:.2f}"
            if quote.amount
            else "Предложить взаимозачёт"
        ),
        callback_data="settlement:save",
    )
    builder.button(text="Назад", callback_data=f"event:settlements:{event_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        f"💱 {escape(quote.event_name)}\n\nВы должны {name}: {quote.outgoing:.2f}\n"
        f"{name} должен вам: {quote.incoming:.2f}\nК переводу: {quote.amount:.2f}\n\n"
        + (
            "Отметьте оплату после перевода. Получатель подтвердит перевод и взаимозачёт."
            if quote.amount
            else "Вторая сторона должна подтвердить взаимозачёт."
        ),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(SettlementForm.single, F.data == "settlement:save")
async def save_settlement(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    settlement = await SettlementService.get_as_dependency().create(
        await ensure_user(callback), SettlementQuote.model_validate(data["quote"])
    )
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Открыть расчёт", callback_data=f"settlement:view:{settlement.id}"
    )
    await callback.message.edit_text(
        f"✅ Расчёт отправлен на подтверждение\n\n{settlement_text(settlement)}",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()
    await notify_settlement(
        callback.bot,
        settlement,
        "Вам отправлен расчёт. Откройте «Подтверждения».",
        settlement.recipient.tg_id,
    )


async def accessible_settlement(user, settlement_id):
    history = await SettlementService.get_as_dependency().history(
        user, settlement_id=settlement_id
    )
    if not history:
        raise PermissionError("Расчёт не найден или недоступен")
    return history[0]


@router.callback_query(F.data.startswith("settlement:view:"))
async def view_settlement(callback: CallbackQuery) -> None:
    user = await ensure_user(callback)
    settlement = await accessible_settlement(user, int(callback.data.rsplit(":", 1)[1]))
    builder = InlineKeyboardBuilder()
    if settlement.status == "PENDING":
        if settlement.recipient.id == user.id:
            builder.button(
                text=(
                    "Подтвердить получение и зачёт"
                    if settlement.amount
                    else "Подтвердить взаимозачёт"
                ),
                callback_data=f"settlement:confirm:{settlement.id}",
            )
        builder.button(
            text="Отменить расчёт", callback_data=f"settlement:cancel:{settlement.id}"
        )
    builder.button(
        text="Взаиморасчёты", callback_data=f"event:settlements:{settlement.event_id}"
    )
    builder.adjust(1)
    await callback.message.edit_text(
        settlement_text(settlement), reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settlement:confirm:"))
async def confirm_settlement(callback: CallbackQuery) -> None:
    user = await ensure_user(callback)
    settlement_id = int(callback.data.rsplit(":", 1)[1])
    service = SettlementService.get_as_dependency()
    await service.confirm(user, settlement_id)
    settlement = await accessible_settlement(user, settlement_id)
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Взаиморасчёты", callback_data=f"event:settlements:{settlement.event_id}"
    )
    await callback.message.edit_text(
        f"✅ Расчёт подтверждён\n\n{settlement_text(settlement)}",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()
    await notify_settlement(
        callback.bot,
        settlement,
        "Получатель подтвердил расчёт.",
        settlement.payer.tg_id,
    )


@router.callback_query(F.data.startswith("settlement:cancel:"))
async def cancel_settlement(callback: CallbackQuery) -> None:
    user = await ensure_user(callback)
    settlement_id = int(callback.data.rsplit(":", 1)[1])
    await SettlementService.get_as_dependency().cancel(user, settlement_id)
    settlement = await accessible_settlement(user, settlement_id)
    other = settlement.recipient if settlement.payer.id == user.id else settlement.payer
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Взаиморасчёты", callback_data=f"event:settlements:{settlement.event_id}"
    )
    await callback.message.edit_text(
        "Расчёт отменён. Исходные долги сохранены. Фактические переводы не возвращаются.",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()
    await notify_settlement(
        callback.bot,
        settlement,
        "Расчёт отменён. Проверьте долги перед следующим переводом.",
        other.tg_id,
    )
