from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.src.schemas.user import User


class SettlementQuote(BaseModel):
    """Current bilateral balance within an event."""

    event_id: int
    event_name: str
    counterparty: User
    outgoing: Decimal
    incoming: Decimal
    fingerprint: str
    blocked: bool = False

    @property
    def amount(self) -> Decimal:
        return self.outgoing - self.incoming


class SettlementResponse(BaseModel):
    """Payment and offset awaiting recipient confirmation."""

    id: int
    event_id: int
    event_name: str
    payer: User
    recipient: User
    amount: Decimal
    offset_amount: Decimal
    status: Literal["PENDING", "CONFIRMED", "CANCELLED"]
