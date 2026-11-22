import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from test_ingest_followers import DAY1, DAY2, run_ingest
from test_ingest_posts import load_posts_and_engagements

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "db_lint.py"
SCHEMA = ROOT / "schema.sql"


def run_lint(db):
    return subprocess.run(
        [sys.executable, str(TOOL), "--db", str(db)],
        capture_output=True,
        text=True,
        check=False,
    )


def apply_schema(conn):
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def insert_account(conn, last_post_at=None, first_seen="2026-01-01", last_seen="2026-01-01"):
    conn.execute(
        """
        INSERT INTO accounts (
            handle, x_user_id, follows_me, i_follow, verified,
            followers_count, following_count, last_post_at,
            first_seen, last_seen
        ) VALUES ('sample_user_1', '1001', 1, 0, 0, 1, 1, ?, ?, ?)
        """,
        (last_post_at, first_seen, last_seen),
    )


def test_orphan_snapshot_fails(tmp_path):
    db = tmp_path / "broken.db"
    conn = sqlite3.connect(db)
    apply_schema(conn)
    conn.execute(
        """
        INSERT INTO snapshots (date, account_id, follows_me, i_follow)
        VALUES ('2026-01-01', 999, 0, 0)
        """
    )
    conn.commit()
    conn.close()

    completed = run_lint(db)
    assert completed.returncode == 1
    assert completed.stderr == ""
    assert completed.stdout.splitlines() == [
        "foreign_key_check: snapshots row 1 parent accounts fkid 0",
        "snapshots row 1 points at missing accounts 999",
    ]


def test_duplicate_snapshot_rows_fail(tmp_path):
    db = tmp_path / "broken.db"
    conn = sqlite3.connect(db)
    apply_schema(conn)
    insert_account(conn)
    conn.execute("DROP TABLE snapshots")
    conn.execute(
        """
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            account_id INTEGER NOT NULL REFERENCES accounts (id),
            follows_me INTEGER NOT NULL,
            i_follow INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO snapshots (date, account_id, follows_me, i_follow)
        VALUES ('2026-01-01', 1, 1, 0)
        """
    )
    conn.execute(
        """
        INSERT INTO snapshots (date, account_id, follows_me, i_follow)
        VALUES ('2026-01-01', 1, 0, 0)
        """
    )
    conn.commit()
    conn.close()

    completed = run_lint(db)
    assert completed.returncode == 1
    assert completed.stderr == ""
    assert completed.stdout.splitlines() == [
        "duplicate rows in snapshots: date='2026-01-01', account_id=1"
    ]


@pytest.mark.parametrize(
    "value",
    ["yesterday", "2026-01-01T00:00:00", "2026-01-01T00:00:00+05:00"],
)
def test_bad_timestamp_fails(tmp_path, value):
    db = tmp_path / "broken.db"
    conn = sqlite3.connect(db)
    apply_schema(conn)
    insert_account(conn, last_post_at=value)
    conn.commit()
    conn.close()

    completed = run_lint(db)
    assert completed.returncode == 1
    assert completed.stderr == ""
    assert completed.stdout.splitlines() == [
        f"bad timestamp: accounts.last_post_at row 1 value {value!r}"
    ]


def test_null_required_timestamp_fails(tmp_path):
    db = tmp_path / "broken.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY,
            handle TEXT NOT NULL,
            x_user_id TEXT NOT NULL UNIQUE,
            follows_me INTEGER NOT NULL DEFAULT 0,
            i_follow INTEGER NOT NULL DEFAULT 0,
            verified INTEGER NOT NULL DEFAULT 0,
            followers_count INTEGER,
            following_count INTEGER,
            last_post_at TEXT,
            first_seen TEXT,
            last_seen TEXT NOT NULL
        );
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            account_id INTEGER NOT NULL REFERENCES accounts (id),
            follows_me INTEGER NOT NULL,
            i_follow INTEGER NOT NULL,
            UNIQUE (date, account_id)
        );
        CREATE TABLE posts (
            id INTEGER PRIMARY KEY,
            x_post_id TEXT NOT NULL UNIQUE,
            account_id INTEGER NOT NULL REFERENCES accounts (id),
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_repost INTEGER NOT NULL DEFAULT 0 CHECK (is_repost IN (0, 1))
        );
        CREATE TABLE engagements (
            id INTEGER PRIMARY KEY,
            post_id INTEGER NOT NULL REFERENCES posts (id),
            account_id INTEGER NOT NULL REFERENCES accounts (id),
            kind TEXT NOT NULL CHECK (kind IN ('like', 'repost', 'quote', 'reply')),
            UNIQUE (post_id, account_id, kind)
        );
        INSERT INTO accounts (
            handle, x_user_id, follows_me, i_follow, verified,
            first_seen, last_seen
        ) VALUES ('sample_user_1', '1001', 1, 0, 0, NULL, '2026-01-01');
        """
    )
    conn.commit()
    conn.close()

    completed = run_lint(db)
    assert completed.returncode == 1
    assert completed.stderr == ""
    assert completed.stdout.splitlines() == [
        "bad timestamp: accounts.first_seen row 1 is null"
    ]


def test_missing_column_fails(tmp_path):
    db = tmp_path / "broken.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE accounts (
            id INTEGER PRIMARY KEY,
            handle TEXT NOT NULL,
            x_user_id TEXT NOT NULL UNIQUE,
            follows_me INTEGER NOT NULL DEFAULT 0,
            i_follow INTEGER NOT NULL DEFAULT 0,
            verified INTEGER NOT NULL DEFAULT 0,
            followers_count INTEGER,
            following_count INTEGER,
            last_post_at TEXT,
            first_seen TEXT NOT NULL
        );
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY,
            date TEXT NOT NULL,
            account_id INTEGER NOT NULL REFERENCES accounts (id),
            follows_me INTEGER NOT NULL,
            i_follow INTEGER NOT NULL,
            UNIQUE (date, account_id)
        );
        CREATE TABLE posts (
            id INTEGER PRIMARY KEY,
            x_post_id TEXT NOT NULL UNIQUE,
            account_id INTEGER NOT NULL REFERENCES accounts (id),
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            is_repost INTEGER NOT NULL DEFAULT 0 CHECK (is_repost IN (0, 1))
        );
        CREATE TABLE engagements (
            id INTEGER PRIMARY KEY,
            post_id INTEGER NOT NULL REFERENCES posts (id),
            account_id INTEGER NOT NULL REFERENCES accounts (id),
            kind TEXT NOT NULL CHECK (kind IN ('like', 'repost', 'quote', 'reply')),
            UNIQUE (post_id, account_id, kind)
        );
        """
    )
    conn.commit()
    conn.close()

    completed = run_lint(db)
    assert completed.returncode == 1
    assert completed.stderr == ""
    assert completed.stdout.splitlines() == ["missing column: accounts.last_seen"]


def test_empty_database_reports_missing_tables(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    completed = run_lint(db)
    assert completed.returncode == 1
    assert completed.stderr == ""
    assert completed.stdout.splitlines() == [
        "missing table: accounts",
        "missing table: snapshots",
        "missing table: posts",
        "missing table: engagements",
    ]


def test_missing_database_fails(tmp_path):
    db = tmp_path / "missing.db"
    completed = run_lint(db)
    assert completed.returncode == 1
    assert completed.stderr == ""
    assert completed.stdout.splitlines() == [f"database not found: {db}"]
    assert not db.exists()


@pytest.mark.parametrize(
    "value",
    ["2026-01-01T15:04:05Z", "2026-01-01T15:04:05+00:00"],
)
def test_utc_timestamp_passes(tmp_path, value):
    db = tmp_path / "ok.db"
    conn = sqlite3.connect(db)
    apply_schema(conn)
    insert_account(conn, last_post_at=value)
    conn.execute(
        """
        INSERT INTO snapshots (date, account_id, follows_me, i_follow)
        VALUES ('2026-01-01', 1, 1, 0)
        """
    )
    conn.commit()
    conn.close()

    before = db.read_bytes()
    completed = run_lint(db)
    assert completed.returncode == 0, completed.stdout
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert db.read_bytes() == before


def test_ingested_fixtures_are_clean(tmp_path):
    db = tmp_path / "xtend.db"
    run_ingest(db, DAY1, "followers", "2026-01-01")
    run_ingest(db, DAY2, "followers", "2026-01-02")
    load_posts_and_engagements(db)
    before = db.read_bytes()
    completed = run_lint(db)
    assert completed.returncode == 0, completed.stdout
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert db.read_bytes() == before
