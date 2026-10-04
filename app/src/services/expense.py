from functools import cache

from app.src.db.expense import ExpenseDB
from app.src.db.user import UserDB
from app.src.schemas import (
    ExpenseResponse,
    User,
    CreateExpenseRequest,
    UserExpenseResponse,
    UserTotalExpenseResponse,
    MarkParticipantPaidRequest,
    ConfirmPaymentRequest,
    ParticipantResponse,
)


class ExpenseService:
    """Manages expense and payment business operations.

    Coordinates ExpenseDB and UserDB for expense workflows.
    """

    _expense_db: ExpenseDB
    _user_db: UserDB

    def __init__(self, expense_db: ExpenseDB, user_db: UserDB):
        self._expense_db = expense_db
        self._user_db = user_db

    @classmethod
    @cache
    def get_as_dependency(cls):
        return cls(ExpenseDB.get_as_dependency(), UserDB.get_as_dependency())

    async def get(self, event_id: int):
        """Returns expenses for the specified event.

        Builds responses with payer and participant data.
        """

        expenses = await self._expense_db.get(event_id)
        for expense in expenses:
            yield await self._build_expense_response(expense)

    async def _build_participant_response(
        self, participant, user_orm
    ) -> ParticipantResponse:
        user = User.model_validate(user_orm)

        if participant.status == "CONFIRMED":
            remaining = 0.0
            paid_amount = participant.share_amount
        else:
            remaining = round(participant.share_amount - participant.paid_amount, 2)
            paid_amount = round(participant.paid_amount, 2)

        return ParticipantResponse(
            id=participant.id,
            user=user,
            share_amount=round(participant.share_amount, 2),
            paid_amount=paid_amount,
            status=participant.status,
            remaining_amount=remaining,
        )

    async def _build_expense_response(self, expense) -> ExpenseResponse:
        paid_by_orm = await self._user_db.get(User(id=expense.paid_by_id))
        paid_by = User.model_validate(paid_by_orm)

        participants_orm = await self._expense_db.get_expense_participants(expense.id)
        participants = []

        for participant in participants_orm:
            user_orm = await self._user_db.get(User(id=participant.participant_id))
            participant_response = await self._build_participant_response(
                participant, user_orm
            )
            participants.append(participant_response)

        return ExpenseResponse(
            id=expense.id,
            event_id=expense.event_id,
            paid_by=paid_by,
            amount=expense.amount,
            description=expense.description or "",
            status=expense.status,
            participants=participants,
        )

    async def create_expense_with_participants(
        self, expense_request: CreateExpenseRequest, paid_by: User
    ) -> ExpenseResponse:
        """Creates an expense with participant shares.

        Supports equal division or individually specified amounts.
        """

        paid_by_user_orm = await self._user_db.get(User(tg_id=paid_by.tg_id))
        if not paid_by_user_orm:
            raise ValueError(f"User with tg_id {paid_by.tg_id} not found")

        participants_map = {}
        for participant_request in expense_request.participants:
            user_orm = await self._user_db.get(User(tg_id=participant_request.tg_id))
            if not user_orm:
                raise ValueError(
                    f"User with tg_id {participant_request.tg_id} not found"
                )
            participants_map[participant_request.tg_id] = user_orm

        if expense_request.is_equally:
            total_amount = expense_request.amount
        else:
            total_amount = sum(
                participant.share_amount or 0
                for participant in expense_request.participants
            )
            total_amount = round(total_amount, 2)

        expense_data = {
            "event_id": expense_request.event_id,
            "paid_by_id": paid_by_user_orm.id,
            "amount": total_amount,
            "description": expense_request.description,
        }
        expense_orm = await self._expense_db.create_expense(expense_data)

        if expense_request.is_equally:
            share_amount = round(
                total_amount / len(expense_request.participants),
                2,
            )
            for participant_request in expense_request.participants:
                user_orm = participants_map[participant_request.tg_id]

                if user_orm.id == paid_by_user_orm.id:
                    participant_data = {
                        "expense_id": expense_orm.id,
                        "participant_id": user_orm.id,
                        "share_amount": share_amount,
                        "paid_amount": share_amount,
                        "status": "CONFIRMED",
                    }
                else:
                    participant_data = {
                        "expense_id": expense_orm.id,
                        "participant_id": user_orm.id,
                        "share_amount": share_amount,
                    }
                await self._expense_db.create_expense_participant(participant_data)
        else:
            for participant_request in expense_request.participants:
                user_orm = participants_map[participant_request.tg_id]
                share_amount = round(participant_request.share_amount or 0, 2)

                if user_orm.id == paid_by_user_orm.id:
                    participant_data = {
                        "expense_id": expense_orm.id,
                        "participant_id": user_orm.id,
                        "share_amount": share_amount,
                        "paid_amount": share_amount,
                        "status": "CONFIRMED",
                    }
                else:
                    participant_data = {
                        "expense_id": expense_orm.id,
                        "participant_id": user_orm.id,
                        "share_amount": share_amount,
                    }
                await self._expense_db.create_expense_participant(participant_data)

        await self._expense_db.recalculate_expense_status(expense_orm.id)

        return await self.get_expense_detail(expense_orm.id)

    async def mark_participant_paid(
        self, request: MarkParticipantPaidRequest, current_user: User
    ) -> ParticipantResponse:
        """Records the current user's debt payment.

        Validates the debt and updates its payment status.
        """

        expense = await self._expense_db.get_expense_by_id(request.expense_id)
        if not expense:
            raise ValueError(f"Expense with id {request.expense_id} not found")

        if expense.status == "CLOSED":
            raise ValueError("Expense is already closed")

        participant_orm = await self._user_db.get(User(tg_id=current_user.tg_id))
        if not participant_orm:
            raise ValueError(f"User with tg_id {current_user.tg_id} not found")

        current_user_orm = await self._user_db.get(User(tg_id=current_user.tg_id))
        if current_user_orm.id != participant_orm.id:
            raise ValueError("You can only mark your own debts as paid")

        expense_participant = await self._expense_db.get_expense_participant(
            request.expense_id, participant_orm.id
        )
        if not expense_participant:
            raise ValueError("User is not a participant in this expense")

        if expense_participant.status == "CONFIRMED":
            raise ValueError("This debt is already confirmed")

        paid_amount = request.amount

        if paid_amount is None or paid_amount <= 0:
            raise ValueError("Payment amount must be positive")

        if paid_amount > expense_participant.share_amount:
            paid_amount = expense_participant.share_amount

        await self._expense_db.mark_participant_paid(
            expense_participant.id, paid_amount
        )

        await self._expense_db.recalculate_expense_status(request.expense_id)

        updated_participant = await self._expense_db.get_expense_participant(
            request.expense_id, participant_orm.id
        )

        return await self._build_participant_response(
            updated_participant, participant_orm
        )

    async def confirm_payment(
        self, request: ConfirmPaymentRequest, current_user: User
    ) -> ParticipantResponse:
        """Confirms a participant's completed payment.

        Allows confirmation only for the expense creator.
        """

        expense = await self._expense_db.get_expense_by_id(request.expense_id)
        if not expense:
            raise ValueError(f"Expense with id {request.expense_id} not found")

        if expense.status == "CLOSED":
            raise ValueError("Expense is already closed")

        current_user_orm = await self._user_db.get(User(tg_id=current_user.tg_id))
        if current_user_orm.id != expense.paid_by_id:
            raise ValueError("Only the expense creator can confirm payments")

        participant_orm = await self._user_db.get(User(tg_id=request.participant_tg_id))
        if not participant_orm:
            raise ValueError(f"User with tg_id {request.participant_tg_id} not found")

        expense_participant = await self._expense_db.get_expense_participant(
            request.expense_id, participant_orm.id
        )
        if not expense_participant:
            raise ValueError("User is not a participant in this expense")

        if expense_participant.status == "CONFIRMED":
            raise ValueError("This debt is already confirmed")

        if expense_participant.paid_amount < expense_participant.share_amount:
            raise ValueError(
                f"Cannot confirm partial payment. "
                f"Participant has paid {expense_participant.paid_amount}/{expense_participant.share_amount}. "
                f"Full payment required for confirmation."
            )

        await self._expense_db.confirm_participant_payment(expense_participant.id)

        await self._expense_db.recalculate_expense_status(request.expense_id)

        updated_participant = await self._expense_db.get_expense_participant(
            request.expense_id, participant_orm.id
        )

        return await self._build_participant_response(
            updated_participant, participant_orm
        )

    async def get_user_expenses(
        self, user: User, event_id: int | None = None
    ) -> UserTotalExpenseResponse:
        """Returns expenses assigned to a user.

        Includes the total amount and individual expense shares.
        """

        user_orm = await self._user_db.get(user)
        if not user_orm:
            raise ValueError("User not found")

        total_amount = await self._expense_db.get_user_total_expense_by_tg_id(
            user_orm.tg_id, event_id
        )

        expense_participants = (
            await self._expense_db.get_user_expense_participants_by_tg_id(
                user_orm.tg_id, event_id
            )
        )

        expenses = []
        for ep in expense_participants:
            expenses.append(
                UserExpenseResponse(
                    id=ep.id,
                    expense_id=ep.expense_id,
                    participant_id=ep.participant_id,
                    share_amount=ep.share_amount,
                    status=ep.status,
                )
            )

        return UserTotalExpenseResponse(
            tg_id=user_orm.tg_id, total_amount=total_amount, expenses=expenses
        )

    async def get_expense_detail(self, expense_id: int) -> ExpenseResponse:
        """Returns complete details for an expense.

        Builds payer and participant information for the response.
        """

        expense = await self._expense_db.get_expense_by_id(expense_id)
        if not expense:
            raise ValueError(f"Expense with id {expense_id} not found")

        return await self._build_expense_response(expense)

    async def delete_expense(
        self, expense_id: int, paid_by: User
    ) -> ExpenseResponse:
        """Marks an expense as deleted.

        Allows deletion only for the expense creator.
        """

        expense = await self._expense_db.get_expense_by_id(expense_id)
        if not expense:
            raise ValueError(f"Expense with id {expense_id} not found")

        if paid_by.id != expense.paid_by_id:
            raise ValueError("Only the expense creator can delete it")

        await self._expense_db.update_expense_status(expense_id, "DELETED")

        return await self.get_expense_detail(expense_id)
