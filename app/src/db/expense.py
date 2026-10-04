from functools import cache

from sqlalchemy import select, update, func

from app.src.db.core import BaseDB
from app.src.models import Expense, ExpenseParticipant, User


class ExpenseDB(BaseDB):
    """Manages expense records in the database.

    Handles expenses, participant shares, payments, and statuses.
    """

    @classmethod
    @cache
    def get_as_dependency(cls):
        return cls()

    async def get(self, event_id) -> list[Expense]:
        """Returns expenses for the selected event.

        Loads every expense matching the event identifier.
        """

        stmt = select(Expense).where(Expense.event_id == event_id)
        async with self.create_session() as session:
            result = await session.execute(stmt)
            result = result.scalars()
            return list(result)

    async def get_expense_by_id(self, expense_id: int) -> Expense | None:
        """Finds an expense by identifier.

        Returns the ORM object or None when absent.
        """

        stmt = select(Expense).where(Expense.id == expense_id)
        async with self.create_session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_expense_participants(
        self, expense_id
    ) -> list[ExpenseParticipant]:
        """Returns participants for the selected expense.

        Loads every participant share linked to the expense.
        """

        stmt = select(ExpenseParticipant).where(
            ExpenseParticipant.expense_id == expense_id
        )

        async with self.create_session() as session:
            result = await session.execute(stmt)
            result = result.scalars()
            return list(result)

    async def get_expense_participant(
        self, expense_id: int, participant_id: int
    ) -> ExpenseParticipant | None:
        """Finds a participant within an expense.

        Matches the expense and participant identifiers.
        """

        stmt = select(ExpenseParticipant).where(
            ExpenseParticipant.expense_id == expense_id,
            ExpenseParticipant.participant_id == participant_id,
        )
        async with self.create_session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def create_expense(self, expense_data: dict) -> Expense:
        """Creates a new expense record.

        Rounds the amount and assigns ACTIVE status.
        """

        expense_data["status"] = "ACTIVE"
        if "amount" in expense_data:
            expense_data["amount"] = round(expense_data["amount"], 2)
        async with self.create_session() as session:
            expense = Expense(**expense_data)
            session.add(expense)
            await session.commit()
            await session.refresh(expense)
            return expense

    async def create_expense_participant(
        self, participant_data: dict
    ) -> ExpenseParticipant:
        """Creates an expense participant record.

        Rounds payment values and assigns PENDING by default.
        """

        participant_data["share_amount"] = round(
            participant_data.get("share_amount", 0.0), 2
        )
        participant_data["paid_amount"] = round(
            participant_data.get("paid_amount", 0.0), 2
        )
        participant_data["status"] = participant_data.get("status") or "PENDING"

        async with self.create_session() as session:
            participant = ExpenseParticipant(**participant_data)
            session.add(participant)
            await session.commit()
            await session.refresh(participant)
            return participant

    async def update_expense_status(self, expense_id: int, status: str):
        """Updates the status of an expense.

        Persists the status and returns the updated expense.
        """

        stmt = (
            update(Expense).where(Expense.id == expense_id).values(status=status)
        )
        async with self.create_session() as session:
            await session.execute(stmt)
            await session.commit()
        return await self.get_expense_by_id(expense_id)

    async def mark_participant_paid(self, participant_id: int, paid_amount: float):
        """Records payment for an expense participant.

        Adds the amount and caps payment at the participant share.
        """

        participant = await self.get_participant_by_id(participant_id)
        if not participant:
            raise ValueError(f"Participant with id {participant_id} not found")

        paid_amount = round(paid_amount + participant.paid_amount, 2)

        share_amount = round(participant.share_amount, 2)
        if paid_amount >= share_amount:
            new_status = "PAID"
            paid_amount = share_amount
        else:
            new_status = "PENDING"

        stmt = (
            update(ExpenseParticipant)
            .where(ExpenseParticipant.id == participant_id)
            .values(paid_amount=paid_amount, status=new_status)
        )
        async with self.create_session() as session:
            await session.execute(stmt)
            await session.commit()

    async def confirm_participant_payment(self, participant_id: int):
        """Confirms an expense participant payment.

        Changes the participant status to CONFIRMED.
        """

        stmt = (
            update(ExpenseParticipant)
            .where(ExpenseParticipant.id == participant_id)
            .values(status="CONFIRMED")
        )
        async with self.create_session() as session:
            await session.execute(stmt)
            await session.commit()

    async def get_participant_by_id(
        self, participant_id: int
    ) -> ExpenseParticipant | None:
        """Finds an expense participant by identifier.

        Returns the ORM object or None when absent.
        """

        stmt = select(ExpenseParticipant).where(ExpenseParticipant.id == participant_id)
        async with self.create_session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_user_expense_participants_by_tg_id(
        self, tg_id: int, event_id: int | None = None
    ) -> list[ExpenseParticipant]:
        """Returns expense shares for a Telegram user.

        Optionally limits the result to one event.
        """

        stmt = (
            select(ExpenseParticipant)
            .join(User, ExpenseParticipant.participant_id == User.id)
            .join(Expense, ExpenseParticipant.expense_id == Expense.id)
            .where(User.tg_id == tg_id)
        )

        if event_id is not None:
            stmt = stmt.where(Expense.event_id == event_id)
        async with self.create_session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_user_total_expense_by_tg_id(
        self, tg_id: int, event_id: int | None = None
    ) -> float:
        """Calculates user expense shares by Telegram ID.

        Optionally limits the total to one event.
        """

        stmt = (
            select(func.sum(ExpenseParticipant.share_amount))
            .join(User, ExpenseParticipant.participant_id == User.id)
            .join(Expense, ExpenseParticipant.expense_id == Expense.id)
            .where(User.tg_id == tg_id)
        )

        if event_id is not None:
            stmt = stmt.where(Expense.event_id == event_id)

        async with self.create_session() as session:
            result = await session.execute(stmt)
            total = result.scalar()
            return total or 0.0

    async def get_expense_participants_with_status(
        self, expense_id: int
    ) -> list[ExpenseParticipant]:
        """Returns participants and their payment statuses.

        Loads every participant share linked to the expense.
        """

        stmt = select(ExpenseParticipant).where(
            ExpenseParticipant.expense_id == expense_id
        )
        async with self.create_session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def recalculate_expense_status(self, expense_id: int):
        """Recalculates an expense payment status.

        Derives the expense status from participant statuses.
        """

        participants = await self.get_expense_participants_with_status(expense_id)

        if not participants:
            return

        all_confirmed = all(p.status == "CONFIRMED" for p in participants)
        any_paid_or_confirmed = any(
            p.status in ["PAID", "CONFIRMED"] for p in participants
        )

        status = "ACTIVE"

        if all_confirmed:
            status = "CLOSED"
        elif any_paid_or_confirmed:
            status = "PARTIALLY_PAID"

        await self.update_expense_status(expense_id, status)
