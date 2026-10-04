from pydantic import BaseModel, ConfigDict
from datetime import datetime

from app.src.schemas import Participant


class CreateEventRequest(BaseModel):
    """Данные для создания нового мероприятия."""

    name: str
    description: str | None = None
    start_date: datetime
    cancel_of_event_date: datetime | None = None
    event_place: str | None = None
    participants: list[int] = []


class EventResponse(BaseModel):
    """Полное представление мероприятия с участниками."""

    id: int
    name: str
    description: str | None = None
    start_date: datetime
    cancel_of_event_date: datetime | None = None
    created_at: datetime
    tg_chat: str | None = None
    event_place: str | None = None
    participants: list[Participant] = []

    model_config = ConfigDict(from_attributes=True)


class UpdateEvent(BaseModel):
    """Данные для изменения существующего мероприятия."""

    id: int
    name: str
    description: str | None = None
    start_date: datetime
    cancel_of_event_date: datetime | None = None
    event_place: str | None = None
