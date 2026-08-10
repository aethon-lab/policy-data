PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_versions (
    component TEXT PRIMARY KEY,
    version INTEGER NOT NULL CHECK (version > 0)
);
INSERT OR IGNORE INTO schema_versions(component, version) VALUES ('control', 1);

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    email_digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS otp_challenges (
    challenge_id TEXT PRIMARY KEY,
    account_id TEXT REFERENCES accounts(account_id),
    email_digest TEXT NOT NULL,
    code_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending','active','consumed','expired','failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 5),
    provider_idempotency_key TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    token_digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    csrf_digest TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS api_keys (
    key_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(account_id),
    lookup_prefix TEXT NOT NULL UNIQUE,
    key_digest TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS rate_limit_buckets (
    bucket_key TEXT PRIMARY KEY,
    window_started_at TEXT NOT NULL,
    count INTEGER NOT NULL CHECK (count >= 0),
    expires_at TEXT NOT NULL
);
