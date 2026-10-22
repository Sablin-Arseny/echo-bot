import logging
from decimal import Decimal
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.src.schemas import ExpenseResponse, ParticipantResponse, User
from app.src.services.event import EventService
from app.src.services.expense import ExpenseService

logger = logging.getLogger(__name__)


def display_name(user: User) -> str:
    return user.full_name or user.username or user.tg_id or "Пользователь"


async def collect_debts(
    user: User, service: ExpenseService | None = None
) -> list[tuple[ExpenseResponse, ParticipantResponse]]:
    service = service or ExpenseService.get_as_dependency()
    summary = await service.get_user_expenses(user)
    debts = []
    for item in summary.expenses:
        if item.status == "CONFIRMED":
            continue
        expense = await service.get_expense_detail(item.expense_id)
        if expense.status == "DELETED":
            continue
        participant = next(
            (value for value in expense.participants if value.user.tg_id == user.tg_id),
            None,
        )
        if participant and participant.remaining_amount > 0:
            debts.append((expense, participant))
    return debts


def totals_by_creditor(
    debts: list[tuple[ExpenseResponse, ParticipantResponse]],
) -> list[tuple[User, Decimal]]:
    creditors: dict[str, tuple[User, Decimal]] = {}
    for expense, participant in debts:
        creditor = expense.paid_by
        key = creditor.tg_id or str(creditor.id)
        previous = creditors.get(key, (creditor, Decimal(0)))[1]
        creditors[key] = (
            creditor,
            previous + Decimal(str(participant.remaining_amount)),
        )
    return list(creditors.values())


async def collect_pending_confirmations(
    user: User,
) -> list[tuple[ExpenseResponse, ParticipantResponse]]:
    event_service = EventService.get_as_dependency()
    expense_service = ExpenseService.get_as_dependency()
    events = await event_service.get_by_user(user, "PARTICIPATING")
    pending = []
    for event in events:
        async for expense in expense_service.get(event.id):
            if expense.status in {"DELETED", "CLOSED"} or expense.paid_by.id != user.id:
                continue
            for participant in expense.participants:
                if participant.status == "PAID":
                    pending.append((expense, participant))
    return pending


async def notify_new_expense(
    bot: Bot, event_name: str, expense: ExpenseResponse
) -> None:
    for participant in expense.participants:
        if participant.user.tg_id == expense.paid_by.tg_id:
            continue
        try:
            await bot.send_message(
                chat_id=participant.user.tg_id,
                text=(
                    f"🔔 Новый расход в событии «{escape(event_name)}»: "
                    f"{escape(expense.description or 'Расход')} — {expense.amount:.2f}.\n"
                    f"Ваша доля: {participant.share_amount:.2f}. "
                    f"Оплатил: {escape(display_name(expense.paid_by))}."
                ),
            )
        except TelegramAPIError:
            logger.warning(
                "Could not notify participant %s about expense %s",
                participant.user.tg_id,
                expense.id,
                exc_info=True,
            )


async def notify_payment(
    bot: Bot,
    expense: ExpenseResponse,
    payer: User,
    participant: ParticipantResponse,
) -> None:
    try:
        await bot.send_message(
            chat_id=expense.paid_by.tg_id,
            text=(
                f"{escape(display_name(payer))} отметил оплату "
                f"{participant.paid_amount:.2f} по расходу "
                f"«{escape(expense.description or str(expense.id))}»."
            ),
        )
    except TelegramAPIError:
        logger.warning(
            "Could not notify payer %s about expense %s",
            expense.paid_by.tg_id,
            expense.id,
            exc_info=True,
        )
