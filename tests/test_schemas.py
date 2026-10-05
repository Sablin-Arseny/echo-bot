"""Тесты валидации Pydantic-схем проекта Echo."""
import pytest
from pydantic import ValidationError

from app.src.schemas.user import User, Participant
from app.src.schemas.event import CreateEventRequest
from app.src.schemas.expense import CreateExpenseRequest, ExpenseParticipantRequest


def test_participant_inherits_user():
    participant = Participant(
        id=1,
        username="maria",
        tg_id="123",
        full_name="Мария Расторгуева",
        role="OWNER",
        status="PARTICIPATING"
    )
    assert participant.username == "maria"
    assert participant.role == "OWNER"


def test_event_requires_name_and_date():
    with pytest.raises(ValidationError):
        CreateEventRequest(
            description="нет названия и даты"
        )


def test_equal_split_valid():
    expense = CreateExpenseRequest(
        event_id=1,
        amount=900,
        is_equally=True,
        participants=[
            ExpenseParticipantRequest(tg_id="111"),
            ExpenseParticipantRequest(tg_id="222")
        ],
    )
    assert expense.amount == 900


def test_equal_split_requires_amount():
    with pytest.raises(ValidationError):
        CreateExpenseRequest(
            event_id=1,
            is_equally=True,
            participants=[
                ExpenseParticipantRequest(tg_id="111")
            ],
        )
