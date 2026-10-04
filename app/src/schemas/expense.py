from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal

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
    share_amount: float
    paid_amount: float = 0.0
    status: PARTICIPANT_STATUS = "PENDING"
    remaining_amount: float

    model_config = ConfigDict(from_attributes=True)


class ExpenseResponse(BaseModel):
    """Complete expense representation with participants."""

    id: int
    event_id: int
    paid_by: User
    amount: float
    description: str = ""
    status: EXPENSE_STATUS = "ACTIVE"
    participants: list[ParticipantResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ExpenseParticipantRequest(BaseModel):
    """Participant share for expense creation."""

    tg_id: str
    share_amount: float | None = None


class CreateExpenseRequest(BaseModel):
    """Data for creating a shared expense."""

    event_id: int
    amount: float | None = None
    is_equally: bool
    description: str = ""
    participants: list[ExpenseParticipantRequest]

    @model_validator(mode="after")
    def validate_amount_requirement(self):
        if self.is_equally and self.amount is None:
            raise ValueError("amount is required when is_equally is True")
        return self


class UserExpenseResponse(BaseModel):
    """User debt for a specific expense."""

    id: int
    expense_id: int
    participant_id: int
    share_amount: float
    status: PARTICIPANT_STATUS


class UserTotalExpenseResponse(BaseModel):
    """Total user debt across expenses."""

    tg_id: str
    total_amount: float
    expenses: list[UserExpenseResponse]


class MarkParticipantPaidRequest(BaseModel):
    """Data for recording a debt payment."""

    expense_id: int
    amount: float | None = None


class ConfirmPaymentRequest(BaseModel):
    """Data for confirming participant payment."""

    expense_id: int
    participant_tg_id: str
