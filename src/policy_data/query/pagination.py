from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass


class InvalidCursor(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CursorState:
    release_id: str
    filter_digest: str
    occurred_at: str
    roll_call_id: str
    vote_id: str


class CursorCodec:
    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("cursor secret must contain at least 32 bytes")
        self.secret = secret

    def encode(self, state: CursorState) -> str:
        body = json.dumps(
            {
                "v": 1,
                "release_id": state.release_id,
                "filter_digest": state.filter_digest,
                "occurred_at": state.occurred_at,
                "roll_call_id": state.roll_call_id,
                "vote_id": state.vote_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signature = hmac.digest(self.secret, body, "sha256")
        return base64.urlsafe_b64encode(body + signature).rstrip(b"=").decode()

    def decode(self, value: str) -> CursorState:
        try:
            padding = "=" * (-len(value) % 4)
            decoded = base64.urlsafe_b64decode(value + padding)
            body, supplied = decoded[:-32], decoded[-32:]
            expected = hmac.digest(self.secret, body, "sha256")
            if len(supplied) != 32 or not hmac.compare_digest(supplied, expected):
                raise InvalidCursor("cursor signature is invalid")
            payload = json.loads(body)
            if payload.get("v") != 1:
                raise InvalidCursor("cursor version is unsupported")
            return CursorState(
                payload["release_id"],
                payload["filter_digest"],
                payload["occurred_at"],
                payload["roll_call_id"],
                payload["vote_id"],
            )
        except InvalidCursor:
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise InvalidCursor("cursor is malformed") from error


def filter_digest(values: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
