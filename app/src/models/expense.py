from sqlalchemy import Column, String, Integer, ForeignKey, Float, text
from sqlalchemy.orm import relationship

from app.src.models.base import Base


class Expense(Base):
    """Shared expense within a single event."""

    __tablename__ = "expense"

    id = Column(Integer, primary_key=True, nullable=False, autoincrement=True)
    event_id = Column(ForeignKey("events.id"), nullable=False, index=True)
    paid_by_id = Column(ForeignKey("users.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False, server_default=text("0.0"))
    description = Column(String)
    status = Column(String, nullable=False, server_default=text("'ACTIVE'"))

    events = relationship("Event", back_populates="expenses")
    users = relationship("User", back_populates="expenses")
    expense_participants = relationship(
        "ExpenseParticipant", back_populates="expense"
    )
