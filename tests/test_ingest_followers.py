import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "ingest_followers.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DAY1 = FIXTURES / "followers_day1.json"
DAY2 = FIXTURES / "followers_day2.json"


def run_ingest(db, fixture, list_kind, snapshot_date):
    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--db",
            str(db),
            "--file",
            str(fixture),
            "--list",
            list_kind,
            "--date",
            snapshot_date,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def table_names(conn):
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    return [row[0] for row in rows]


def columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def test_ingest_creates_rows_and_reingest_is_idempotent(tmp_path):
    original = DAY1.read_bytes()
    db = tmp_path / "xtend.db"
    first = run_ingest(db, DAY1, "followers", "2026-01-01")
    assert first == {
        "snapshot_date": "2026-01-01",
        "list": "followers",
        "upserted": 3,
        "cleared": 0,
        "accounts": 3,
        "snapshots": 3,
    }

    conn = sqlite3.connect(db)
    assert table_names(conn) == ["accounts", "engagements", "posts", "snapshots"]
    assert columns(conn, "accounts") == [
        "id",
        "handle",
        "x_user_id",
        "follows_me",
        "i_follow",
        "verified",
        "followers_count",
        "following_count",
        "last_post_at",
        "first_seen",
        "last_seen",
    ]
    assert columns(conn, "snapshots") == [
        "id",
        "date",
        "account_id",
        "follows_me",
        "i_follow",
    ]
    row = conn.execute(
        """
        SELECT handle, follows_me, i_follow, verified, followers_count, following_count,
               first_seen, last_seen
        FROM accounts WHERE x_user_id = '1002'
        """
    ).fetchone()
    assert row == ("sample_user_2", 1, 0, 1, 25, 8, "2026-01-01", "2026-01-01")
    assert conn.execute("SELECT COUNT(*) FROM accounts WHERE follows_me = 1").fetchone()[0] == 3

    second = run_ingest(db, DAY1, "followers", "2026-01-01")
    assert second["accounts"] == 3
    assert second["snapshots"] == 3
    assert second["cleared"] == 0
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 3
    assert DAY1.read_bytes() == original
    conn.close()


@pytest.mark.parametrize(
    ("list_kind", "flag"),
    [("followers", "follows_me"), ("following", "i_follow")],
)
def test_account_missing_from_later_snapshot_is_not_following(tmp_path, list_kind, flag):
    original = DAY2.read_bytes()
    db = tmp_path / "xtend.db"
    run_ingest(db, DAY1, list_kind, "2026-01-01")
    later = run_ingest(db, DAY2, list_kind, "2026-01-02")
    assert later["cleared"] == 1
    assert later["upserted"] == 2
    assert later["accounts"] == 3
    assert later["snapshots"] == 6

    other = "i_follow" if flag == "follows_me" else "follows_me"
    conn = sqlite3.connect(db)
    gone = conn.execute(
        f"""
        SELECT id, {flag}, {other}, first_seen, last_seen, followers_count
        FROM accounts WHERE handle = 'sample_user_2'
        """
    ).fetchone()
    assert gone[1:] == (0, 0, "2026-01-01", "2026-01-01", 25)

    still = conn.execute(
        f"SELECT {flag}, {other}, followers_count FROM accounts WHERE handle = 'sample_user_1'"
    ).fetchone()
    assert still == (1, 0, 11)

    day1_follows_me = 1 if flag == "follows_me" else 0
    day1_i_follow = 1 if flag == "i_follow" else 0
    history = conn.execute(
        """
        SELECT date, follows_me, i_follow
        FROM snapshots WHERE account_id = ? ORDER BY date
        """,
        (gone[0],),
    ).fetchall()
    assert history == [
        ("2026-01-01", day1_follows_me, day1_i_follow),
        ("2026-01-02", 0, 0),
    ]

    again = run_ingest(db, DAY2, list_kind, "2026-01-02")
    assert again["accounts"] == 3
    assert again["snapshots"] == 6
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 6
    assert conn.execute(
        f"SELECT {flag} FROM accounts WHERE handle = 'sample_user_2'"
    ).fetchone()[0] == 0
    assert DAY2.read_bytes() == original
    conn.close()
