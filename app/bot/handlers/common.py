from decimal import Decimal, InvalidOperation

from aiogram.types import CallbackQuery, Message

from app.src.db.user import UserDB
from app.src.schemas import User
from app.src.services.event import EventService


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
    elif stored.username != profile.username or stored.full_name != profile.full_name:
        stored = await user_db.update(User(id=stored.id), profile)
    return User.model_validate(stored)


async def require_event_member(
    event_id: int, user: User, allow_removed: bool = False
) -> None:
    participants = await EventService.get_as_dependency().get_participants(event_id)
    if not any(
        participant.id == user.id
        and participant.status
        in ({"PARTICIPATING", "DELETED"} if allow_removed else {"PARTICIPATING"})
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
