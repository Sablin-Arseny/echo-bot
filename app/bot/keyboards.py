from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.src.schemas import Participant


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Мои события", callback_data="menu:events"),
                InlineKeyboardButton(text="Мои долги", callback_data="menu:debts"),
            ],
            [
                InlineKeyboardButton(
                    text="Подтверждения", callback_data="menu:confirmations"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Создать событие", callback_data="event:create"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗂 Архив событий", callback_data="menu:archive"
                )
            ],
        ]
    )


def event_menu(
    event_id: int, finished: bool = False, owner: bool = False, member: bool = True
) -> InlineKeyboardMarkup:
    if finished or not member:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💰 Расходы", callback_data=f"event:expenses:{event_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👥 Участники", callback_data=f"event:members:{event_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Назад",
                        callback_data="menu:archive" if finished else "menu:events",
                    )
                ],
            ]
        )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 Расходы", callback_data=f"event:expenses:{event_id}"
                ),
                InlineKeyboardButton(
                    text="👥 Участники", callback_data=f"event:members:{event_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Добавить расход", callback_data=f"expense:create:{event_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Пригласить", callback_data=f"event:invite:{event_id}"
                ),
                InlineKeyboardButton(text="Назад", callback_data="menu:events"),
            ],
        ]
    )
    if owner:
        markup.inline_keyboard.insert(
            -1,
            [
                InlineKeyboardButton(
                    text="Редактировать событие", callback_data=f"event:edit:{event_id}"
                ),
                InlineKeyboardButton(
                    text="Завершить событие", callback_data=f"event:finish:{event_id}"
                ),
            ],
        )
    return markup


def cancel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="form:cancel")]
        ]
    )


def skip_menu(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data=f"skip:{field}")],
            [InlineKeyboardButton(text="Отмена", callback_data="form:cancel")],
        ]
    )


def confirmation_menu(kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сохранить", callback_data=f"save:{kind}")],
            [InlineKeyboardButton(text="Отмена", callback_data="form:cancel")],
        ]
    )


def split_mode_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Поровну", callback_data="split:equal"),
                InlineKeyboardButton(text="Вручную", callback_data="split:manual"),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="form:cancel")],
        ]
    )


def participants_menu(
    participants: list[Participant], selected: set[str]
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for participant in participants:
        tg_id = str(participant.tg_id)
        marker = "✓ " if tg_id in selected else ""
        label = participant.full_name or participant.username or tg_id
        builder.button(text=f"{marker}{label}", callback_data=f"member:toggle:{tg_id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="Готово", callback_data="member:done"))
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="form:cancel"))
    return builder.as_markup()


def join_menu(payload: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Присоединиться", callback_data=f"event:join:{payload}"
                )
            ],
            [InlineKeyboardButton(text="Главное меню", callback_data="menu:main")],
        ]
    )
