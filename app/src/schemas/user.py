from pydantic import BaseModel, ConfigDict
from typing import Literal


STATUS = Literal[
    "INVITED",
    "REFUSED",
    "PARTICIPATING",
    "DELETED",
    "DRAFT",
]

ROLES = Literal[
    "OWNER",
    "ADMIN",
    "PARTICIPANT",
]


class User(BaseModel):
    """Данные пользовательского профиля."""

    id: int | None = None
    username: str | None = None
    tg_id: str | None = None
    full_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class Participant(User):
    """Участник мероприятия со статусом и ролью."""

    role: ROLES | None = None
    status: STATUS = "DRAFT"
