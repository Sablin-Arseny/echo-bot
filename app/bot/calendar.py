import calendar
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def date_input_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть календарь", callback_data="calendar:open"
                )
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="form:cancel")],
        ]
    )


def month_menu(year: int, month: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    previous = (
        date(year - 1, 12, 1)
        if month == 1 and year > 1
        else date(year, max(1, month - 1), 1)
    )
    next_month = (
        date(year + 1, 1, 1)
        if month == 12 and year < 9999
        else date(year, min(12, month + 1), 1)
    )
    builder.row(
        InlineKeyboardButton(
            text="‹", callback_data=f"calendar:month:{previous:%Y-%m}"
        ),
        InlineKeyboardButton(
            text=f"{MONTHS[month - 1]} {year}", callback_data="calendar:noop"
        ),
        InlineKeyboardButton(
            text="›", callback_data=f"calendar:month:{next_month:%Y-%m}"
        ),
    )
    builder.row(
        *(
            InlineKeyboardButton(text=label, callback_data="calendar:noop")
            for label in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
        )
    )
    for week in calendar.monthcalendar(year, month):
        builder.row(
            *(
                InlineKeyboardButton(
                    text=str(day) if day else "·",
                    callback_data=(
                        f"calendar:day:{year:04d}-{month:02d}-{day:02d}"
                        if day
                        else "calendar:noop"
                    ),
                )
                for day in week
            )
        )
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="form:cancel"))
    return builder.as_markup()


def hour_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Без времени", callback_data="calendar:no_time")
    builder.adjust(1)
    for hour in range(24):
        builder.button(text=f"{hour:02d}", callback_data=f"calendar:hour:{hour:02d}")
    builder.adjust(1, *([6] * 4))
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="form:cancel"))
    return builder.as_markup()


def minute_menu(hour: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for minute in (0, 15, 30, 45):
        builder.button(
            text=f"{hour:02d}:{minute:02d}",
            callback_data=f"calendar:time:{hour:02d}:{minute:02d}",
        )
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(
            text="Другие минуты — введите ЧЧ:ММ", callback_data="calendar:noop"
        )
    )
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="form:cancel"))
    return builder.as_markup()
