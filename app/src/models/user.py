from sqlalchemy import Column, String, Integer
from sqlalchemy.orm import relationship

from app.src.models.base import Base


class User(Base):
    """Пользователь системы с Telegram-профилем."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    username = Column(String, unique=True, nullable=False)
    tg_id = Column(String, unique=True, nullable=False)
    full_name = Column(String)

    event_members = relationship("EventMember", back_populates="users")
    expense_participants = relationship("ExpenseParticipant", back_populates="users")
    expenses = relationship("Expense", back_populates="users")
