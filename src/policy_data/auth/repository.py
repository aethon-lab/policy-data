from __future__ import annotations

import hmac
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    account_id: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ApiKeyRecord:
    key_id: str
    account_id: str
    lookup_prefix: str
    label: str
    created_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class RateLimit:
    bucket_key: str
    limit: int
    window: timedelta


class AuthRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._lock = threading.Lock()

    def create_challenge(
        self,
        *,
        challenge_id: str,
        email_digest: str,
        code_digest: str,
        idempotency_key: str,
        now: datetime,
        expires_at: datetime,
        rate_limits: tuple[RateLimit, ...],
    ) -> bool:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                if not self._reserve_rate_limits(rate_limits, now):
                    self.connection.rollback()
                    return False
                self.connection.execute(
                    """UPDATE otp_challenges SET state = 'expired'
                         WHERE email_digest = ? AND state = 'pending'""",
                    (email_digest,),
                )
                self.connection.execute(
                    """INSERT INTO otp_challenges(
                           challenge_id, email_digest, code_digest, state,
                           provider_idempotency_key, expires_at, created_at
                       ) VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
                    (
                        challenge_id,
                        email_digest,
                        code_digest,
                        idempotency_key,
                        expires_at.isoformat(),
                        now.isoformat(),
                    ),
                )
                self.connection.commit()
                return True
            except Exception:
                self.connection.rollback()
                raise

    def reserve_verification(
        self, *, rate_limits: tuple[RateLimit, ...], now: datetime
    ) -> bool:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                if not self._reserve_rate_limits(rate_limits, now):
                    self.connection.rollback()
                    return False
                self.connection.commit()
                return True
            except Exception:
                self.connection.rollback()
                raise

    def purge_expired_access_state(self, now: datetime) -> dict[str, int]:
        """Apply the v1 privacy retention windows in one short transaction."""
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                challenges = self.connection.execute(
                    "DELETE FROM otp_challenges WHERE created_at < ?",
                    ((now - timedelta(hours=24)).isoformat(),),
                ).rowcount
                sessions = self.connection.execute(
                    "DELETE FROM sessions WHERE expires_at < ?",
                    ((now - timedelta(days=30)).isoformat(),),
                ).rowcount
                buckets = self.connection.execute(
                    "DELETE FROM rate_limit_buckets WHERE expires_at < ?",
                    ((now - timedelta(hours=48)).isoformat(),),
                ).rowcount
                self.connection.commit()
                return {
                    "otp_challenges": challenges,
                    "sessions": sessions,
                    "rate_limit_buckets": buckets,
                }
            except Exception:
                self.connection.rollback()
                raise

    def delete_account(self, account_id: str) -> bool:
        """Irreversibly remove one account and all of its credential state."""
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                exists = self.connection.execute(
                    "SELECT 1 FROM accounts WHERE account_id = ?", (account_id,)
                ).fetchone()
                if exists is None:
                    self.connection.rollback()
                    return False
                self.connection.execute(
                    "DELETE FROM otp_challenges WHERE account_id = ?", (account_id,)
                )
                self.connection.execute(
                    "DELETE FROM sessions WHERE account_id = ?", (account_id,)
                )
                self.connection.execute(
                    "DELETE FROM api_keys WHERE account_id = ?", (account_id,)
                )
                self.connection.execute(
                    "DELETE FROM accounts WHERE account_id = ?", (account_id,)
                )
                self.connection.commit()
                return True
            except Exception:
                self.connection.rollback()
                raise

    def _reserve_rate_limits(
        self, rate_limits: tuple[RateLimit, ...], now: datetime
    ) -> bool:
        reservations: list[tuple[RateLimit, datetime, int]] = []
        for rate_limit in rate_limits:
            row = self.connection.execute(
                """SELECT window_started_at, count, expires_at
                     FROM rate_limit_buckets WHERE bucket_key = ?""",
                (rate_limit.bucket_key,),
            ).fetchone()
            if row is None or datetime.fromisoformat(row[2]) <= now:
                reservations.append((rate_limit, now, 1))
            elif row[1] >= rate_limit.limit:
                return False
            else:
                reservations.append(
                    (rate_limit, datetime.fromisoformat(row[0]), row[1] + 1)
                )
        for rate_limit, window_start, count in reservations:
            self.connection.execute(
                """INSERT INTO rate_limit_buckets(
                       bucket_key, window_started_at, count, expires_at
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(bucket_key) DO UPDATE SET
                       window_started_at = excluded.window_started_at,
                       count = excluded.count,
                       expires_at = excluded.expires_at""",
                (
                    rate_limit.bucket_key,
                    window_start.isoformat(),
                    count,
                    (window_start + rate_limit.window).isoformat(),
                ),
            )
        return True

    def consume_challenge(
        self,
        *,
        challenge_id: str,
        supplied_digest: str,
        session_id: str,
        session_digest: str,
        csrf_digest: str,
        now: datetime,
        session_expires_at: datetime,
    ) -> SessionRecord | None:
        with self._lock:
            return self._consume_challenge(
                challenge_id=challenge_id,
                supplied_digest=supplied_digest,
                session_id=session_id,
                session_digest=session_digest,
                csrf_digest=csrf_digest,
                now=now,
                session_expires_at=session_expires_at,
            )

    def _consume_challenge(
        self,
        *,
        challenge_id: str,
        supplied_digest: str,
        session_id: str,
        session_digest: str,
        csrf_digest: str,
        now: datetime,
        session_expires_at: datetime,
    ) -> SessionRecord | None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                """SELECT email_digest, code_digest, state, attempt_count, expires_at
                     FROM otp_challenges WHERE challenge_id = ?""",
                (challenge_id,),
            ).fetchone()
            if row is None or row[2] != "pending":
                self.connection.commit()
                return None
            if datetime.fromisoformat(row[4]) <= now:
                self.connection.execute(
                    "UPDATE otp_challenges SET state = 'expired' WHERE challenge_id = ?",
                    (challenge_id,),
                )
                self.connection.commit()
                return None
            if not hmac.compare_digest(row[1], supplied_digest):
                attempts = row[3] + 1
                state = "failed" if attempts >= 5 else "pending"
                self.connection.execute(
                    "UPDATE otp_challenges SET attempt_count = ?, state = ? WHERE challenge_id = ?",
                    (attempts, state, challenge_id),
                )
                self.connection.commit()
                return None
            account = self.connection.execute(
                "SELECT account_id FROM accounts WHERE email_digest = ? AND deleted_at IS NULL",
                (row[0],),
            ).fetchone()
            account_id = account[0] if account else f"acct_{uuid.uuid4().hex}"
            if account is None:
                self.connection.execute(
                    "INSERT INTO accounts VALUES (?, ?, ?, NULL)",
                    (account_id, row[0], now.isoformat()),
                )
            updated = self.connection.execute(
                """UPDATE otp_challenges
                      SET state = 'consumed', account_id = ?
                    WHERE challenge_id = ? AND state = 'pending'""",
                (account_id, challenge_id),
            ).rowcount
            if updated != 1:
                self.connection.rollback()
                return None
            self.connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    session_id,
                    account_id,
                    session_digest,
                    now.isoformat(),
                    now.isoformat(),
                    session_expires_at.isoformat(),
                    csrf_digest,
                ),
            )
            self.connection.commit()
            return SessionRecord(session_id, account_id, session_expires_at)
        except Exception:
            self.connection.rollback()
            raise

    def create_api_key(
        self,
        *,
        key_id: str,
        account_id: str,
        lookup_prefix: str,
        key_digest: str,
        label: str,
        now: datetime,
        max_active: int = 10,
    ) -> ApiKeyRecord:
        with self._lock:
            return self._create_api_key(
                key_id=key_id,
                account_id=account_id,
                lookup_prefix=lookup_prefix,
                key_digest=key_digest,
                label=label,
                now=now,
                max_active=max_active,
            )

    def _create_api_key(
        self,
        *,
        key_id: str,
        account_id: str,
        lookup_prefix: str,
        key_digest: str,
        label: str,
        now: datetime,
        max_active: int,
    ) -> ApiKeyRecord:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            count = self.connection.execute(
                "SELECT COUNT(*) FROM api_keys WHERE account_id = ? AND revoked_at IS NULL",
                (account_id,),
            ).fetchone()[0]
            if count >= max_active:
                raise ValueError("active API key limit reached")
            self.connection.execute(
                "INSERT INTO api_keys VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (
                    key_id,
                    account_id,
                    lookup_prefix,
                    key_digest,
                    label,
                    now.isoformat(),
                ),
            )
            self.connection.commit()
            return ApiKeyRecord(key_id, account_id, lookup_prefix, label, now, None)
        except Exception:
            self.connection.rollback()
            raise

    def authenticate_api_key(
        self, lookup_prefix: str, digest: str
    ) -> ApiKeyRecord | None:
        with self._lock:
            row = self.connection.execute(
                """SELECT key_id, account_id, lookup_prefix, key_digest, label,
                          created_at, revoked_at
                     FROM api_keys WHERE lookup_prefix = ?""",
                (lookup_prefix,),
            ).fetchone()
        if row is None or row[6] is not None or not hmac.compare_digest(row[3], digest):
            return None
        return ApiKeyRecord(
            row[0], row[1], row[2], row[4], datetime.fromisoformat(row[5]), None
        )

    def list_api_keys(self, account_id: str) -> tuple[ApiKeyRecord, ...]:
        with self._lock:
            rows = self.connection.execute(
                """SELECT key_id, account_id, lookup_prefix, label, created_at, revoked_at
                     FROM api_keys WHERE account_id = ? ORDER BY created_at, key_id""",
                (account_id,),
            ).fetchall()
        return tuple(
            ApiKeyRecord(
                row[0],
                row[1],
                row[2],
                row[3],
                datetime.fromisoformat(row[4]),
                datetime.fromisoformat(row[5]) if row[5] else None,
            )
            for row in rows
        )

    def revoke_api_key(self, account_id: str, key_id: str, now: datetime) -> bool:
        with self._lock:
            changed = self.connection.execute(
                """UPDATE api_keys SET revoked_at = ?
                     WHERE account_id = ? AND key_id = ? AND revoked_at IS NULL""",
                (now.isoformat(), account_id, key_id),
            ).rowcount
            self.connection.commit()
        return changed == 1

    def validate_session(
        self,
        token_digest: str,
        now: datetime,
        *,
        idle_ttl: timedelta,
        absolute_ttl: timedelta,
    ) -> SessionRecord | None:
        with self._lock:
            return self._validate_session(
                token_digest,
                now,
                idle_ttl=idle_ttl,
                absolute_ttl=absolute_ttl,
            )

    def _validate_session(
        self,
        token_digest: str,
        now: datetime,
        *,
        idle_ttl: timedelta,
        absolute_ttl: timedelta,
    ) -> SessionRecord | None:
        row = self.connection.execute(
            """SELECT session_id, account_id, token_digest, created_at,
                      last_seen_at, expires_at, csrf_digest, revoked_at
                 FROM sessions WHERE token_digest = ?""",
            (token_digest,),
        ).fetchone()
        if (
            row is None
            or row[7] is not None
            or not hmac.compare_digest(row[2], token_digest)
        ):
            return None
        created = datetime.fromisoformat(row[3])
        expires = datetime.fromisoformat(row[5])
        absolute_expiry = created + absolute_ttl
        if now >= expires or now >= absolute_expiry:
            return None
        next_expiry = min(now + idle_ttl, absolute_expiry)
        self.connection.execute(
            "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE session_id = ?",
            (now.isoformat(), next_expiry.isoformat(), row[0]),
        )
        self.connection.commit()
        return SessionRecord(row[0], row[1], next_expiry)

    def verify_csrf_digest(self, session_id: str, supplied_digest: str) -> bool:
        with self._lock:
            row = self.connection.execute(
                "SELECT csrf_digest, revoked_at FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return bool(
            row is not None
            and row[1] is None
            and hmac.compare_digest(row[0], supplied_digest)
        )
