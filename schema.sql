-- Shared SQLite store. Tables used by the follower ingest tool.

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    handle TEXT NOT NULL,
    x_user_id TEXT NOT NULL UNIQUE,
    follows_me INTEGER NOT NULL DEFAULT 0 CHECK (follows_me IN (0, 1)),
    i_follow INTEGER NOT NULL DEFAULT 0 CHECK (i_follow IN (0, 1)),
    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    followers_count INTEGER,
    following_count INTEGER,
    last_post_at TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts (id),
    follows_me INTEGER NOT NULL CHECK (follows_me IN (0, 1)),
    i_follow INTEGER NOT NULL CHECK (i_follow IN (0, 1)),
    UNIQUE (date, account_id)
);
