from decimal import Decimal
from functools import cache

from sqlalchemy import delete, func, select, update

from app.src.db.core import BaseDB
from app.src.models import Event, EventMember, Expense, ExpenseParticipant, User


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

    async def save_expense(
        self, data: dict, shares: list[dict], expense_id: int | None = None
    ):
        """Saves expense data and shares atomically."""
        async with self.create_session() as session:
            event = await session.scalar(
                select(Event).where(Event.id == data["event_id"]).with_for_update()
            )
            if not event or event.finished_at:
                raise ValueError(
                    "Создавать и изменять расходы можно только в активном событии"
                )
            active_ids = set(
                (
                    await session.scalars(
                        select(EventMember.user_id).where(
                            EventMember.event_id == event.id,
                            EventMember.status == "PARTICIPATING",
                        )
                    )
                ).all()
            )
            if data["paid_by_id"] not in active_ids or any(
                p["participant_id"] not in active_ids for p in shares
            ):
                raise PermissionError("Расход доступен только действующим участникам")
            if expense_id is None:
                expense = Expense(**data, status="ACTIVE")
                session.add(expense)
                await session.flush()
            else:
                expense = await session.scalar(
                    select(Expense).where(Expense.id == expense_id).with_for_update()
                )
                if (
                    not expense
                    or expense.paid_by_id != data["paid_by_id"]
                    or expense.event_id != event.id
                ):
                    raise PermissionError("Изменять расход может только его автор")
                if expense.status in {"DELETED", "CLOSED"}:
                    raise ValueError(
                        "Закрытый или удалённый расход нельзя редактировать"
                    )
                existing = (
                    await session.scalars(
                        select(ExpenseParticipant)
                        .where(ExpenseParticipant.expense_id == expense.id)
                        .with_for_update()
                    )
                ).all()
                if any(
                    p.participant_id != expense.paid_by_id and p.paid_amount > 0
                    for p in existing
                ):
                    raise ValueError(
                        "После оплаты расход нельзя редактировать. Доступно удаление"
                    )
                for key, value in data.items():
                    setattr(expense, key, value)
                await session.execute(
                    delete(ExpenseParticipant).where(
                        ExpenseParticipant.expense_id == expense.id
                    )
                )
            for share in shares:
                share.setdefault("status", "PENDING")
                if share["share_amount"] == 0:
                    share["status"] = "CONFIRMED"
                session.add(ExpenseParticipant(expense_id=expense.id, **share))
            expense.status = (
                "CLOSED"
                if all(p["status"] == "CONFIRMED" for p in shares)
                else "ACTIVE"
            )
            await session.flush()
            return expense.id

    async def delete_expense(self, expense_id: int, author_id: int):
        """Deletes an author's active-event expense."""
        async with self.create_session() as session:
            expense = await session.get(Expense, expense_id)
            if not expense:
                raise ValueError("Расход не найден")
            event = await session.scalar(
                select(Event).where(Event.id == expense.event_id).with_for_update()
            )
            member = await session.get(EventMember, (expense.event_id, author_id))
            if (
                expense.paid_by_id != author_id
                or not member
                or member.status != "PARTICIPATING"
            ):
                raise PermissionError(
                    "Удалять расход может только его действующий автор"
                )
            if event.finished_at:
                raise ValueError("Расходы завершённого события изменять нельзя")
            await session.refresh(expense)
            expense.status = "DELETED"

    async def get_expense_by_id(self, expense_id: int) -> Expense | None:
        """Finds an expense by identifier.

        Returns the ORM object or None when absent.
        """

        stmt = select(Expense).where(Expense.id == expense_id)
        async with self.create_session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_expense_participants(self, expense_id) -> list[ExpenseParticipant]:
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
            participant_data.get("share_amount", 0), 2
        )
        participant_data["paid_amount"] = round(
            participant_data.get("paid_amount", 0), 2
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

        stmt = update(Expense).where(Expense.id == expense_id).values(status=status)
        async with self.create_session() as session:
            await session.execute(stmt)
            await session.commit()
        return await self.get_expense_by_id(expense_id)

    async def mark_participant_paid(
        self, participant_id: int, paid_amount, user_id: int
    ):
        """Records payment for an expense participant.

        Adds the amount and caps payment at the participant share.
        """

        async with self.create_session() as session:
            participant, expense = await self._locked_participant(
                session, participant_id
            )
            if participant.participant_id != user_id:
                raise PermissionError("Можно отмечать только собственную оплату")
            if participant.status != "PENDING":
                raise ValueError("Оплата уже отмечена или подтверждена")
            participant.paid_amount = min(
                participant.share_amount, participant.paid_amount + paid_amount
            )
            participant.status = (
                "PAID"
                if participant.paid_amount >= participant.share_amount
                else "PENDING"
            )
            await self._recalculate(session, expense)

    async def confirm_participant_payment(self, participant_id: int, user_id: int):
        """Confirms an expense participant payment.

        Changes the participant status to CONFIRMED.
        """

        async with self.create_session() as session:
            participant, expense = await self._locked_participant(
                session, participant_id
            )
            if expense.paid_by_id != user_id:
                raise PermissionError("Подтвердить оплату может только автор расхода")
            if (
                participant.status != "PAID"
                or participant.paid_amount < participant.share_amount
            ):
                raise ValueError("Нет полной оплаты для подтверждения")
            participant.status = "CONFIRMED"
            await self._recalculate(session, expense)

    async def _locked_participant(self, session, participant_id):
        participant = await session.get(ExpenseParticipant, participant_id)
        if not participant:
            raise ValueError("Доля участника не найдена")
        expense = await session.get(Expense, participant.expense_id)
        await session.execute(
            select(Event).where(Event.id == expense.event_id).with_for_update()
        )
        await session.refresh(expense)
        participant = await session.scalar(
            select(ExpenseParticipant)
            .where(ExpenseParticipant.id == participant_id)
            .execution_options(populate_existing=True)
        )
        if not participant or expense.status in {"CLOSED", "DELETED"}:
            raise ValueError("Расход изменён, закрыт или удалён")
        return participant, expense

    async def _recalculate(self, session, expense):
        await session.flush()
        participants = (
            await session.scalars(
                select(ExpenseParticipant).where(
                    ExpenseParticipant.expense_id == expense.id
                )
            )
        ).all()
        for participant in participants:
            if not participant.share_amount:
                participant.status = "CONFIRMED"
        if all(p.status == "CONFIRMED" for p in participants):
            expense.status = "CLOSED"
        elif any(p.paid_amount > 0 for p in participants):
            expense.status = "PARTIALLY_PAID"
        else:
            expense.status = "ACTIVE"

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
    ) -> Decimal:
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
            return total or Decimal(0)

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

        async with self.create_session() as session:
            expense = await session.get(Expense, expense_id)
            if not expense:
                raise ValueError("Расход не найден")
            await session.execute(
                select(Event).where(Event.id == expense.event_id).with_for_update()
            )
            await session.refresh(expense)
            if expense.status != "DELETED":
                await self._recalculate(session, expense)
