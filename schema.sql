-- Shared SQLite store. Tables used by the follower and post ingest tools.

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    handle TEXT NOT NULL,
    x_user_id TEXT NOT NULL UNIQUE,
    follows_me INTEGER NOT NULL DEFAULT 0 CHECK (follows_me IN (0, 1)),
    i_follow INTEGER NOT NULL DEFAULT 0 CHECK (i_follow IN (0, 1)),
    verified INTEGER DEFAULT 0 CHECK (verified IN (0, 1)),
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

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    x_post_id TEXT NOT NULL UNIQUE,
    account_id INTEGER NOT NULL REFERENCES accounts (id),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_repost INTEGER NOT NULL DEFAULT 0 CHECK (is_repost IN (0, 1))
);

CREATE TABLE IF NOT EXISTS engagements (
    id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts (id),
    account_id INTEGER NOT NULL REFERENCES accounts (id),
    kind TEXT NOT NULL CHECK (kind IN ('like', 'repost', 'quote', 'reply')),
    source_post_id TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS engagements_without_source
ON engagements (post_id, account_id, kind)
WHERE source_post_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS engagements_with_source
ON engagements (post_id, account_id, kind, source_post_id)
WHERE source_post_id IS NOT NULL;
