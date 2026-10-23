from aiogram.fsm.state import State, StatesGroup


class EventForm(StatesGroup):
    name = State()
    description = State()
    start_date = State()
    time = State()
    place = State()
    confirmation = State()


class ExpenseForm(StatesGroup):
    description = State()
    amount = State()
    mode = State()
    participants = State()
    manual_shares = State()
    confirmation = State()


class EventEditForm(StatesGroup):
    value = State()
    confirmation = State()


class SettlementForm(StatesGroup):
    single = State()
    batch = State()
    confirmations = State()
