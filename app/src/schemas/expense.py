from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.src.schemas.user import User

EXPENSE_STATUS = Literal[
    "ACTIVE",
    "PARTIALLY_PAID",
    "CLOSED",
    "DELETED",
]

PARTICIPANT_STATUS = Literal[
    "PENDING",
    "PAID",
    "CONFIRMED",
]


class ParticipantResponse(BaseModel):
    """Participant share status within an expense."""

    id: int
    user: User
    share_amount: Decimal
    paid_amount: Decimal = Decimal("0.00")
    status: PARTICIPANT_STATUS = "PENDING"
    remaining_amount: Decimal

    model_config = ConfigDict(from_attributes=True)


class ExpenseResponse(BaseModel):
    """Complete expense representation with participants."""

    id: int
    event_id: int
    paid_by: User
    amount: Decimal
    description: str = ""
    status: EXPENSE_STATUS = "ACTIVE"
    participants: list[ParticipantResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ExpenseParticipantRequest(BaseModel):
    """Participant share for expense creation."""

    tg_id: str
    share_amount: Decimal | None = Field(
        default=None, gt=0, max_digits=18, decimal_places=2
    )


class CreateExpenseRequest(BaseModel):
    """Data for creating a shared expense."""

    event_id: int
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    is_equally: bool
    description: str = ""
    participants: list[ExpenseParticipantRequest]

    @model_validator(mode="after")
    def validate_amount_requirement(self):
        if not self.participants:
            raise ValueError("participants must not be empty")
        if len({p.tg_id for p in self.participants}) != len(self.participants):
            raise ValueError("Участники расхода не должны повторяться")
        if self.is_equally and self.amount is None:
            raise ValueError("amount is required when is_equally is True")
        if self.is_equally and self.amount <= 0:
            raise ValueError("amount must be positive")
        if not self.is_equally and any(
            participant.share_amount is None or participant.share_amount <= 0
            for participant in self.participants
        ):
            raise ValueError("participant shares must be positive")
        if not self.is_equally and sum(
            p.share_amount for p in self.participants
        ) >= Decimal(10000000000000000):
            raise ValueError("Общая сумма расхода слишком велика")
        return self


class UserExpenseResponse(BaseModel):
    """User debt for a specific expense."""

    id: int
    expense_id: int
    participant_id: int
    share_amount: Decimal
    status: PARTICIPANT_STATUS


class UserTotalExpenseResponse(BaseModel):
    """Total user debt across expenses."""

    tg_id: str
    total_amount: Decimal
    expenses: list[UserExpenseResponse]


class MarkParticipantPaidRequest(BaseModel):
    """Data for recording a debt payment."""

    expense_id: int
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2)


class ConfirmPaymentRequest(BaseModel):
    """Data for confirming participant payment."""

    expense_id: int
    participant_tg_id: str
