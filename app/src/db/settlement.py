from decimal import Decimal
from functools import cache
from hashlib import sha256

from sqlalchemy import or_, select

from app.src.db.core import BaseDB
from app.src.models import (
    Event,
    EventMember,
    Expense,
    ExpenseParticipant,
    Settlement,
    SettlementAllocation,
    User,
)


async def pending_settlements(session, expense_id: int, debtor_id: int | None = None):
    stmt = (
        select(Settlement)
        .join(SettlementAllocation)
        .where(
            SettlementAllocation.expense_id == expense_id,
            Settlement.status == "PENDING",
        )
    )
    if debtor_id is not None:
        stmt = stmt.where(SettlementAllocation.debtor_id == debtor_id)
    return list((await session.scalars(stmt)).unique().all())


def pair_balance(rows, user_id: int, other_id: int):
    """Calculates bilateral totals and snapshot identity."""
    selected = [
        (p, e)
        for p, e in rows
        if {p.participant_id, e.paid_by_id} == {user_id, other_id}
    ]
    selected.sort(key=lambda row: row[0].id)
    outgoing = sum(
        (
            p.share_amount - p.paid_amount
            for p, _ in selected
            if p.participant_id == user_id
        ),
        Decimal(0),
    )
    incoming = sum(
        (
            p.share_amount - p.paid_amount
            for p, _ in selected
            if p.participant_id == other_id
        ),
        Decimal(0),
    )
    snapshot = "|".join(
        f"{p.id}:{e.id}:{p.share_amount}:{p.paid_amount}:{p.status}"
        for p, e in selected
    )
    return outgoing, incoming, sha256(snapshot.encode()).hexdigest(), selected


class SettlementDB(BaseDB):
    """Stores atomic payments and bilateral offsets."""

    @classmethod
    @cache
    def get_as_dependency(cls):
        return cls()

    async def _rows(self, session, event_id: int):
        return (
            await session.execute(
                select(ExpenseParticipant, Expense)
                .join(Expense)
                .where(
                    Expense.event_id == event_id,
                    Expense.status.notin_(["DELETED", "CLOSED"]),
                    ExpenseParticipant.status != "CONFIRMED",
                    ExpenseParticipant.share_amount > 0,
                    ExpenseParticipant.participant_id != Expense.paid_by_id,
                )
            )
        ).all()

    async def balances(self, user_id: int, event_id: int | None = None):
        async with self.create_session() as session:
            stmt = (
                select(Event)
                .join(EventMember)
                .where(
                    EventMember.user_id == user_id,
                    EventMember.status.in_(["PARTICIPATING", "DELETED"]),
                )
            )
            if event_id is not None:
                stmt = stmt.where(Event.id == event_id)
            events = (await session.scalars(stmt)).all()
            result = []
            for event in events:
                rows = await self._rows(session, event.id)
                others = {
                    e.paid_by_id if p.participant_id == user_id else p.participant_id
                    for p, e in rows
                    if user_id in {p.participant_id, e.paid_by_id}
                }
                for other_id in sorted(others):
                    outgoing, incoming, fingerprint, selected = pair_balance(
                        rows, user_id, other_id
                    )
                    pending = await self._pending_pair(
                        session, event.id, user_id, other_id
                    )
                    blocked = bool(pending) or any(
                        p.paid_amount > 0 for p, _ in selected
                    )
                    result.append(
                        {
                            "event_id": event.id,
                            "event_name": event.name,
                            "counterparty": await session.get(User, other_id),
                            "outgoing": outgoing,
                            "incoming": incoming,
                            "fingerprint": fingerprint,
                            "blocked": blocked,
                        }
                    )
            return result

    async def _pending_pair(self, session, event_id, user_id, other_id):
        return await session.scalar(
            select(Settlement).where(
                Settlement.event_id == event_id,
                Settlement.status == "PENDING",
                or_(
                    (Settlement.payer_id == user_id)
                    & (Settlement.recipient_id == other_id),
                    (Settlement.payer_id == other_id)
                    & (Settlement.recipient_id == user_id),
                ),
            )
        )

    async def create(
        self, event_id: int, user_id: int, other_id: int, fingerprint: str
    ):
        """Reserves the exact reviewed expense shares."""
        async with self.create_session() as session:
            event = await session.scalar(
                select(Event).where(Event.id == event_id).with_for_update()
            )
            if not event:
                raise ValueError("Событие не найдено")
            for member_id in (user_id, other_id):
                member = await session.get(EventMember, (event_id, member_id))
                if not member or member.status not in {"PARTICIPATING", "DELETED"}:
                    raise PermissionError("Участник не найден в событии")
            rows = await self._rows(session, event_id)
            outgoing, incoming, current, selected = pair_balance(
                rows, user_id, other_id
            )
            if current != fingerprint:
                raise ValueError("Суммы изменились. Откройте расчёт заново")
            if not selected or not outgoing and not incoming:
                raise ValueError("Непогашенных долгов нет")
            if outgoing < incoming:
                raise ValueError(
                    "Отметить перевод должен участник, который должен больше"
                )
            if await self._pending_pair(session, event_id, user_id, other_id) or any(
                p.paid_amount > 0 for p, _ in selected
            ):
                raise ValueError("Сначала подтвердите или отмените предыдущую оплату")
            settlement = Settlement(
                event_id=event_id,
                payer_id=user_id,
                recipient_id=other_id,
                amount=outgoing - incoming,
                offset_amount=incoming,
                status="PENDING",
            )
            session.add(settlement)
            await session.flush()
            for participant, expense in selected:
                if participant.share_amount > 0:
                    session.add(
                        SettlementAllocation(
                            settlement_id=settlement.id,
                            expense_id=expense.id,
                            debtor_id=participant.participant_id,
                            creditor_id=expense.paid_by_id,
                            amount=participant.share_amount,
                        )
                    )
            return settlement.id

    async def _locked(self, session, settlement_id):
        settlement = await session.get(Settlement, settlement_id)
        if not settlement:
            raise ValueError("Расчёт не найден")
        await session.execute(
            select(Event).where(Event.id == settlement.event_id).with_for_update()
        )
        await session.refresh(settlement)
        if settlement.status != "PENDING":
            raise ValueError("Расчёт уже подтверждён или отменён")
        return settlement

    async def confirm(self, settlement_id: int, user_id: int):
        """Confirms all linked shares atomically."""
        async with self.create_session() as session:
            settlement = await self._locked(session, settlement_id)
            if settlement.recipient_id != user_id:
                raise PermissionError("Подтвердить расчёт может только получатель")
            allocations = (
                await session.scalars(
                    select(SettlementAllocation).where(
                        SettlementAllocation.settlement_id == settlement.id
                    )
                )
            ).all()
            expense_ids = set()
            for allocation in allocations:
                expense = await session.get(Expense, allocation.expense_id)
                participant = await session.scalar(
                    select(ExpenseParticipant).where(
                        ExpenseParticipant.expense_id == allocation.expense_id,
                        ExpenseParticipant.participant_id == allocation.debtor_id,
                    )
                )
                if (
                    not expense
                    or expense.status == "DELETED"
                    or not participant
                    or participant.status != "PENDING"
                    or participant.paid_amount
                    or participant.share_amount != allocation.amount
                ):
                    raise ValueError(
                        "Исходные расходы изменились. Отмените расчёт и создайте новый"
                    )
                participant.paid_amount = participant.share_amount
                participant.status = "CONFIRMED"
                expense_ids.add(expense.id)
            await session.flush()
            for expense_id in sorted(expense_ids):
                expense = await session.get(Expense, expense_id)
                participants = (
                    await session.scalars(
                        select(ExpenseParticipant).where(
                            ExpenseParticipant.expense_id == expense_id
                        )
                    )
                ).all()
                for participant in participants:
                    if not participant.share_amount:
                        participant.status = "CONFIRMED"
                expense.status = (
                    "CLOSED"
                    if all(p.status == "CONFIRMED" for p in participants)
                    else "PARTIALLY_PAID"
                )
            settlement.status = "CONFIRMED"

    async def cancel(self, settlement_id: int, user_id: int):
        """Cancels a pending calculation without changing debts."""
        async with self.create_session() as session:
            settlement = await self._locked(session, settlement_id)
            if user_id not in {settlement.payer_id, settlement.recipient_id}:
                raise PermissionError("Расчёт доступен только его участникам")
            settlement.status = "CANCELLED"

    async def get(
        self,
        user_id: int,
        event_id: int | None = None,
        settlement_id: int | None = None,
    ):
        """Returns a user's payment history."""
        async with self.create_session() as session:
            stmt = (
                select(Settlement, Event, User)
                .select_from(Settlement)
                .join(Event)
                .join(User, User.id == Settlement.payer_id)
                .where(
                    or_(
                        Settlement.payer_id == user_id,
                        Settlement.recipient_id == user_id,
                    )
                )
            )
            if event_id is not None:
                stmt = stmt.where(Settlement.event_id == event_id)
            if settlement_id is not None:
                stmt = stmt.where(Settlement.id == settlement_id)
            result = []
            for settlement, event, payer in (
                await session.execute(stmt.order_by(Settlement.id.desc()))
            ).all():
                result.append(
                    {
                        "id": settlement.id,
                        "event_id": event.id,
                        "event_name": event.name,
                        "payer": payer,
                        "recipient": await session.get(User, settlement.recipient_id),
                        "amount": settlement.amount,
                        "offset_amount": settlement.offset_amount,
                        "status": settlement.status,
                    }
                )
            return result
