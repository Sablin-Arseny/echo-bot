from functools import cache

from sqlalchemy import select

from app.src.db.core import BaseDB
from app.src.models import Event, EventMember
from app.src.models import User as UserOrm
from app.src.schemas import ROLES, STATUS, User


class EventDB(BaseDB):
    """Manages event records in the database.

    Handles events, participants, statuses, and roles.
    """

    @classmethod
    @cache
    def get_as_dependency(cls):
        return cls()

    async def create_event(self, event: dict) -> Event:
        """Creates a new event record.

        Stores provided fields and returns the ORM object.
        """

        async with self.create_session() as session:
            event = Event(**event)
            session.add(event)

        async with self.create_session() as session:
            event = await session.get(Event, event.id)
            return event

    async def update_event(self, event_id: int, event: dict):
        """Updates an existing event record.

        Applies recognized fields and returns the updated object.
        """

        async with self.create_session() as session:
            event_orm = await session.scalar(
                select(Event).where(Event.id == event_id).with_for_update()
            )
            if not event_orm:
                raise ValueError("Событие не найдено")
            if event_orm.finished_at:
                raise ValueError(
                    "Завершённое событие изменять или открывать повторно нельзя"
                )
            if "finished_at" in event and event["finished_at"] is None:
                raise ValueError("Повторное открытие события запрещено")

            for key, value in event.items():
                if hasattr(event_orm, key):
                    setattr(event_orm, key, value)

            await session.commit()
            await session.refresh(event_orm)
            return event_orm

    async def get_event_by_id(self, event_id: int) -> Event:
        """Finds an event by identifier.

        Returns the ORM object or None when absent.
        """

        async with self.create_session() as session:
            result = await session.get(Event, event_id)
            return result

    async def add_relation_event_member(self, event_id: int, user: User, role: ROLES):
        """Adds a user to event participants.

        Creates membership with DRAFT status and the given role.
        """

        async with self.create_session() as session:
            event = await session.scalar(
                select(Event).where(Event.id == event_id).with_for_update()
            )
            if not event or event.finished_at:
                raise ValueError("Присоединиться можно только к активному событию")
            current = await session.get(EventMember, (event_id, user.id))
            if current:
                if current.status == "DELETED":
                    raise PermissionError(
                        "Повторный вход удалённого участника запрещён"
                    )
                return event
            event_member = EventMember(
                event_id=event_id, user_id=user.id, status="DRAFT", role=role
            )
            session.add(event_member)
        return await self.get_event_by_id(event_id)

    async def update_status_of_member(
        self, event_id: int, user_id: int, status: STATUS
    ):
        """Updates an event participant status.

        Updates the user membership and returns the event.
        """

        stmt = select(EventMember).where(
            EventMember.event_id == event_id, EventMember.user_id == user_id
        )
        async with self.create_session() as session:
            event = await session.scalar(
                select(Event).where(Event.id == event_id).with_for_update()
            )
            if not event or event.finished_at:
                raise ValueError("Участников завершённого события изменять нельзя")
            event_member = await session.execute(stmt)
            event_member = event_member.scalar()
            if not event_member:
                raise ValueError("Участник не найден")
            if event_member.status == "DELETED" and status != "DELETED":
                raise PermissionError("Повторный вход удалённого участника запрещён")
            if status == "DELETED" and event_member.role == "OWNER":
                raise ValueError("Создателя события удалить нельзя")
            event_member.status = status
            await session.commit()
        return await self.get_event_by_id(event_id)

    async def update_role_of_member(self, event_id: int, user_id: int, role: ROLES):
        """Updates an event participant role.

        Updates the user membership and returns the event.
        """

        stmt = select(EventMember).where(
            EventMember.event_id == event_id, EventMember.user_id == user_id
        )
        async with self.create_session() as session:
            event = await session.scalar(
                select(Event).where(Event.id == event_id).with_for_update()
            )
            if not event or event.finished_at:
                raise ValueError("Участников завершённого события изменять нельзя")
            event_member = await session.execute(stmt)
            event_member = event_member.scalar()
            if not event_member or event_member.status == "DELETED":
                raise ValueError("Участник не найден")
            event_member.role = role
            await session.commit()
        return await self.get_event_by_id(event_id)

    async def get_members_by_event_id(self, event_id: int):
        """Returns participants for the selected event.

        Each result contains the user, status, and role.
        """

        stmt = (
            select(UserOrm, EventMember.status, EventMember.role)
            .join(EventMember, UserOrm.id == EventMember.user_id)
            .where(EventMember.event_id == event_id)
        )
        async with self.create_session() as session:
            members = await session.execute(stmt)
            members = members.all()
        return members if members else None

    async def get_events_by_member(self, user: User, status: STATUS | None):
        """Returns events for the selected user.

        Filters the result by membership status when provided.
        """

        stmt = (
            select(Event)
            .join(EventMember, Event.id == EventMember.event_id)
            .where(EventMember.user_id == user.id)
        )
        if status:
            stmt = stmt.where(EventMember.status == status)

        async with self.create_session() as session:
            events = await session.execute(stmt)
            events = events.scalars().all()
        return list(events) if events else None
