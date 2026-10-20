import base64
import hashlib
import hmac

from app.config import settings


def invite_payload(event_id: int) -> str:
    signature = hmac.new(
        settings.bot_token.encode(), str(event_id).encode(), hashlib.sha256
    ).digest()[:12]
    token = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"e_{event_id}_{token}"


def invited_event_id(payload: str) -> int | None:
    try:
        prefix, event_id_text, _ = payload.split("_", 2)
        event_id = int(event_id_text)
    except ValueError:
        return None
    if prefix != "e" or event_id <= 0:
        return None
    if not hmac.compare_digest(payload, invite_payload(event_id)):
        return None
    return event_id
