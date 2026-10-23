from functools import cache

from app.src.db.settlement import SettlementDB
from app.src.schemas.settlement import SettlementQuote, SettlementResponse


class SettlementService:
    """Coordinates event balances and payment confirmations."""

    def __init__(self, db: SettlementDB):
        self._db = db

    @classmethod
    @cache
    def get_as_dependency(cls):
        return cls(SettlementDB.get_as_dependency())

    async def balances(self, user, event_id=None):
        """Returns bilateral balances per event."""
        return [
            SettlementQuote.model_validate(row)
            for row in await self._db.balances(user.id, event_id)
        ]

    async def create(self, user, quote: SettlementQuote):
        """Records payment against a reviewed balance."""
        settlement_id = await self._db.create(
            quote.event_id, user.id, quote.counterparty.id, quote.fingerprint
        )
        return (await self.history(user, settlement_id=settlement_id))[0]

    async def history(self, user, event_id=None, settlement_id=None):
        """Returns accessible settlements and their states."""
        return [
            SettlementResponse.model_validate(row)
            for row in await self._db.get(user.id, event_id, settlement_id)
        ]

    async def confirm(self, user, settlement_id: int):
        """Confirms payment and closes covered shares."""
        await self._db.confirm(settlement_id, user.id)

    async def cancel(self, user, settlement_id: int):
        """Cancels a pending settlement reservation."""
        await self._db.cancel(settlement_id, user.id)
