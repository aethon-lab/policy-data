from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from policy_data.auth.codes import (
    code_digest,
    generate_challenge_id,
    generate_code,
    normalize_email,
    secret_digest,
)
from policy_data.auth.keys import (
    GeneratedApiKey,
    api_key_digest,
    api_key_lookup_prefix,
    generate_api_key,
)
from policy_data.auth.repository import (
    ApiKeyRecord,
    AuthRepository,
    RateLimit,
    SessionRecord,
)
from policy_data.auth.sessions import (
    GeneratedSession,
    csrf_digest,
    generate_session,
    session_digest,
)


class OtpSender(Protocol):
    async def send_otp(self, *, email: str, code: str, idempotency_key: str) -> str: ...


@dataclass(frozen=True, slots=True)
class CodeRequestResult:
    challenge_id: str
    public_message: str = "If the address can receive mail, a code has been sent."


@dataclass(frozen=True, slots=True)
class VerifiedSession:
    generated: GeneratedSession
    record: SessionRecord


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    generated: GeneratedApiKey
    record: ApiKeyRecord


SESSION_ABSOLUTE_TTL = timedelta(days=7)
SESSION_IDLE_TTL = timedelta(hours=24)
SESSION_COOKIE_MAX_AGE = int(SESSION_ABSOLUTE_TTL.total_seconds())


class AuthService:
    def __init__(
        self,
        repository: AuthRepository,
        sender: OtpSender,
        *,
        pepper: bytes,
        otp_ttl: timedelta = timedelta(minutes=10),
        session_ttl: timedelta = SESSION_ABSOLUTE_TTL,
    ) -> None:
        self.repository = repository
        self.sender = sender
        self.pepper = pepper
        self.otp_ttl = otp_ttl
        self.session_ttl = session_ttl

    async def request_code(
        self, email: str, *, source_ip: str, now: datetime | None = None
    ) -> CodeRequestResult:
        current = now or datetime.now(UTC)
        normalized = normalize_email(email)
        challenge_id = generate_challenge_id()
        code = generate_code()
        idempotency_key = f"otp/{challenge_id}"
        email_digest = secret_digest(self.pepper, "email", normalized)
        ip_digest = self._source_ip_digest(source_ip)
        accepted = self.repository.create_challenge(
            challenge_id=challenge_id,
            email_digest=email_digest,
            code_digest=code_digest(self.pepper, challenge_id, code),
            idempotency_key=idempotency_key,
            now=current,
            expires_at=current + self.otp_ttl,
            rate_limits=(
                RateLimit(
                    f"otp:req:email:cooldown:{email_digest}",
                    1,
                    timedelta(minutes=1),
                ),
                RateLimit(f"otp:req:email:hour:{email_digest}", 5, timedelta(hours=1)),
                RateLimit(f"otp:req:ip:hour:{ip_digest}", 20, timedelta(hours=1)),
                RateLimit("otp:send:global:hour", 100, timedelta(hours=1)),
                RateLimit("otp:send:global:day", 500, timedelta(days=1)),
            ),
        )
        if accepted:
            await self.sender.send_otp(
                email=normalized, code=code, idempotency_key=idempotency_key
            )
        return CodeRequestResult(challenge_id)

    def verify_code(
        self,
        challenge_id: str,
        code: str,
        *,
        source_ip: str,
        now: datetime | None = None,
    ) -> VerifiedSession | None:
        current = now or datetime.now(UTC)
        challenge_digest = secret_digest(self.pepper, "challenge", challenge_id)
        ip_digest = self._source_ip_digest(source_ip)
        if not self.repository.reserve_verification(
            rate_limits=(
                RateLimit(
                    f"otp:verify:challenge:{challenge_digest}",
                    5,
                    self.otp_ttl,
                ),
                RateLimit(f"otp:verify:ip:{ip_digest}", 25, timedelta(minutes=10)),
            ),
            now=current,
        ):
            return None
        generated = generate_session(self.pepper)
        record = self.repository.consume_challenge(
            challenge_id=challenge_id,
            supplied_digest=code_digest(self.pepper, challenge_id, code),
            session_id=generated.session_id,
            session_digest=generated.token_digest,
            csrf_digest=generated.csrf_digest,
            now=current,
            session_expires_at=current + min(self.session_ttl, SESSION_IDLE_TTL),
        )
        return VerifiedSession(generated, record) if record else None

    def create_api_key(
        self,
        account_id: str,
        label: str,
        *,
        now: datetime | None = None,
    ) -> IssuedApiKey:
        clean_label = label.strip()
        if not clean_label or len(clean_label) > 80:
            raise ValueError("API key label must contain 1-80 characters")
        current = now or datetime.now(UTC)
        generated = generate_api_key(self.pepper)
        record = self.repository.create_api_key(
            key_id=f"key_{uuid.uuid4().hex}",
            account_id=account_id,
            lookup_prefix=generated.lookup_prefix,
            key_digest=generated.digest,
            label=clean_label,
            now=current,
        )
        return IssuedApiKey(generated, record)

    def authenticate_api_key(self, raw: str) -> ApiKeyRecord | None:
        try:
            prefix = api_key_lookup_prefix(raw)
        except ValueError:
            return None
        return self.repository.authenticate_api_key(
            prefix, api_key_digest(self.pepper, raw)
        )

    def authorize_data_request(
        self,
        principal: object,
        *,
        source_ip: str,
        now: datetime | None = None,
    ) -> bool:
        """Atomically apply the shared REST/MCP key and source-IP budgets."""
        if not isinstance(principal, ApiKeyRecord):
            return False
        current = now or datetime.now(UTC)
        ip_digest = self._source_ip_digest(source_ip)
        return self.repository.reserve_verification(
            rate_limits=(
                RateLimit(
                    f"data:key:minute:{principal.key_id}", 120, timedelta(minutes=1)
                ),
                RateLimit(f"data:ip:minute:{ip_digest}", 300, timedelta(minutes=1)),
            ),
            now=current,
        )

    def list_api_keys(self, account_id: str) -> tuple[ApiKeyRecord, ...]:
        return self.repository.list_api_keys(account_id)

    def revoke_api_key(
        self, account_id: str, key_id: str, *, now: datetime | None = None
    ) -> bool:
        return self.repository.revoke_api_key(
            account_id, key_id, now or datetime.now(UTC)
        )

    def validate_session(
        self, raw: str, *, now: datetime | None = None
    ) -> SessionRecord | None:
        return self.repository.validate_session(
            session_digest(self.pepper, raw),
            now or datetime.now(UTC),
            idle_ttl=SESSION_IDLE_TTL,
            absolute_ttl=self.session_ttl,
        )

    def _source_ip_digest(self, source_ip: str) -> str:
        try:
            normalized = ipaddress.ip_address(source_ip).compressed
        except ValueError:
            normalized = "unknown"
        return secret_digest(self.pepper, "source-ip", normalized)

    def verify_csrf(self, session_id: str, raw_csrf: str) -> bool:
        return self.repository.verify_csrf_digest(
            session_id, csrf_digest(self.pepper, session_id, raw_csrf)
        )
