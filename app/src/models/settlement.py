from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)

from app.src.models.base import Base


class Settlement(Base):
    """Bilateral payment with an expense offset."""

    __tablename__ = "settlements"
    __table_args__ = (
        CheckConstraint("amount >= 0 AND offset_amount >= 0"),
        CheckConstraint("payer_id <> recipient_id"),
        CheckConstraint("status IN ('PENDING', 'CONFIRMED', 'CANCELLED')"),
    )

    id = Column(Integer, primary_key=True)
    event_id = Column(ForeignKey("events.id"), nullable=False, index=True)
    payer_id = Column(ForeignKey("users.id"), nullable=False, index=True)
    recipient_id = Column(ForeignKey("users.id"), nullable=False, index=True)
    amount = Column(Numeric(38, 2), nullable=False)
    offset_amount = Column(Numeric(38, 2), nullable=False)
    status = Column(String, nullable=False, default="PENDING", index=True)
    created_at = Column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class SettlementAllocation(Base):
    """Expense share included in a settlement."""

    __tablename__ = "settlement_allocations"
    __table_args__ = (
        UniqueConstraint("settlement_id", "expense_id", "debtor_id"),
        CheckConstraint("amount > 0"),
    )

    id = Column(Integer, primary_key=True)
    settlement_id = Column(ForeignKey("settlements.id"), nullable=False, index=True)
    expense_id = Column(ForeignKey("expense.id"), nullable=False, index=True)
    debtor_id = Column(ForeignKey("users.id"), nullable=False)
    creditor_id = Column(ForeignKey("users.id"), nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
