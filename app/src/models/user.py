from sqlalchemy import Column, String, Integer

from app.src.models.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True, nullable=False)
    username = Column(String, unique=True, nullable=False)
    tg_id = Column(String, unique=True, nullable=False)
    full_name = Column(String)
