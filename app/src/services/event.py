from functools import cache

from app.src.db.event import EventDB
from app.src.db.user import UserDB
from app.src.schemas import (
    CreateEventRequest,
    User,
    Participant,
    EventResponse,
    UpdateEvent,
    STATUS,
    ROLES,
)


class EventService:
    """Manages event and participant business operations.

    Coordinates EventDB and UserDB for domain workflows.
    """

    _event_db: EventDB
    _user_db: UserDB

    def __init__(self, event_db: EventDB, user_db: UserDB):
        self._event_db = event_db
        self._user_db = user_db

    @classmethod
    @cache
    def get_as_dependency(cls):
        return cls(
            EventDB.get_as_dependency(),
            UserDB.get_as_dependency(),
        )

    async def create(self, event: CreateEventRequest, user: User):
        """Creates an event with its participants.

        Assigns ownership to the creator and confirms membership.
        """

        participants = [
            await self._user_db.get(User(id=uid)) for uid in event.participants
        ]

        event = await self._event_db.create_event(
            event.model_dump(exclude_none=True, exclude={"participants"})
        )
        if not event:
            return

        await self._event_db.add_relation_event_member(event.id, user, "OWNER")
        for participant in participants:
            await self._event_db.add_relation_event_member(
                event.id, participant, "PARTICIPANT"
            )

        await self._event_db.update_status_of_member(event.id, user.id, "PARTICIPATING")

        return await self.get(event.id)

    async def update(self, event: UpdateEvent):
        """Updates data for an existing event.

        Passes populated fields to EventDB.
        """

        update_event = event.model_dump(exclude_none=True, exclude={"id"})
        event = await self._event_db.update_event(event.id, update_event)
        return EventResponse.model_validate(event)

    async def get(self, id: int):
        """Returns an event by identifier.

        Converts the ORM object into EventResponse.
        """

        event = await self._event_db.get_event_by_id(id)
        if not event:
            return None
        return EventResponse.model_validate(event)

    async def get_by_user(self, user: User, status: STATUS | None):
        """Returns events for the specified user.

        Optionally filters them by membership status.
        """

        events = await self._event_db.get_events_by_member(user, status)
        if not events:
            return []
        return [EventResponse.model_validate(event) for event in events]

    async def get_participants(self, event_id: int) -> list[Participant]:
        """Returns participants for the specified event.

        Adds membership status and role to user data.
        """

        result = await self._event_db.get_members_by_event_id(event_id)

        if not result:
            return []

        participants = []
        for user_orm, status, role in result:
            participant = Participant.model_validate(user_orm)
            participant.status = status
            participant.role = role
            participants.append(participant)

        return participants

    async def add_user_to_event(self, event_id: int, user_to_add: User, user: User):
        """Adds a user to an event.

        Allows the operation for owners or administrators.
        """

        participants = await self.get_participants(event_id)
        users = [p for p in participants if p.id == user.id]
        if not user:
            raise LookupError(f"User is not found for event {event_id}")
        user_role = users[0].role
        if user_role != "ADMIN" and user_role != "OWNER":
            raise ValueError("User role must be ADMIN or OWNER")
        user_to_add = await self._user_db.get(user_to_add)
        return await self._event_db.add_relation_event_member(
            event_id, user_to_add, "PARTICIPANT"
        )

    async def add_user_by_invite(self, event_id: int, user_to_add: User):
        """Adds a user through an invitation.

        Creates membership with the PARTICIPANT role.
        """

        user_to_add = await self._user_db.get(user_to_add)
        return await self._event_db.add_relation_event_member(
            event_id, user_to_add, "PARTICIPANT"
        )

    async def update_member_role(
        self, event_id: int, user_to_update: User, role: ROLES, user: User
    ):
        """Updates an event participant role.

        Allows the operation for owners or administrators.
        """

        participants = await self.get_participants(event_id)
        users = [p for p in participants if p.id == user.id]
        if not user:
            raise LookupError(f"User is not found for event {event_id}")
        user_role = users[0].role
        if user_role != "ADMIN" and user_role != "OWNER":
            raise ValueError("User role must be ADMIN or OWNER")

        user_to_update = await self._user_db.get(user_to_update)
        return await self._event_db.update_role_of_member(
            event_id, user_to_update.id, role
        )

    async def update_status_of_member(self, event_id: int, user: User, status: STATUS):
        """Updates an event participant status.

        Finds the user and updates the related membership.
        """

        user = await self._user_db.get(user)
        return await self._event_db.update_status_of_member(event_id, user.id, status)
