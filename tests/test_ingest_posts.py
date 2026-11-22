import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "ingest_posts.py"
LINT = ROOT / "tools" / "db_lint.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
POSTS = FIXTURES / "posts_page.json"
LIKES = FIXTURES / "likes_page.json"
REPOSTS = FIXTURES / "reposts_page.json"
QUOTES = FIXTURES / "quotes_page.json"
REPLIES = FIXTURES / "replies_page.json"
DATE = "2026-01-03"
ENGAGEMENT_FILES = (
    ("like", LIKES),
    ("repost", REPOSTS),
    ("quote", QUOTES),
    ("reply", REPLIES),
)


def run_posts(db, fixture, kind, owner=None, post=None, check=True):
    command = [
        sys.executable,
        str(TOOL),
        "--db",
        str(db),
        "--file",
        str(fixture),
        "--kind",
        kind,
        "--date",
        DATE,
    ]
    if owner is not None:
        command.extend(["--owner", owner])
    if post is not None:
        command.extend(["--post", post])
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if check:
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)
    return completed


def run_lint(db):
    return subprocess.run(
        [sys.executable, str(LINT), "--db", str(db)],
        capture_output=True,
        text=True,
        check=False,
    )


def load_posts_and_engagements(db):
    posts = run_posts(db, POSTS, "posts", owner="1000")
    engagements = [
        run_posts(db, fixture, kind, post="2001")
        for kind, fixture in ENGAGEMENT_FILES
    ]
    return posts, engagements


def test_reingest_posts_and_engagements_is_idempotent(tmp_path):
    originals = {
        path: path.read_bytes()
        for path in (POSTS, LIKES, REPOSTS, QUOTES, REPLIES)
    }
    db = tmp_path / "xtend.db"
    posts, engagements = load_posts_and_engagements(db)
    assert posts == {
        "snapshot_date": DATE,
        "kind": "posts",
        "upserted": 3,
        "reposts": 1,
        "posts": 3,
        "accounts": 1,
    }
    assert [item["kind"] for item in engagements] == [
        "like",
        "repost",
        "quote",
        "reply",
    ]
    assert [item["upserted"] for item in engagements] == [1, 1, 1, 1]
    assert engagements[-1]["engagements"] == 4
    assert engagements[-1]["accounts"] == 5
    assert engagements[-1]["x_post_id"] == "2001"

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM engagements").fetchone()[0] == 4
    rows = conn.execute(
        """
        SELECT accounts.handle, engagements.kind
        FROM engagements
        JOIN posts ON posts.id = engagements.post_id
        JOIN accounts ON accounts.id = engagements.account_id
        WHERE posts.x_post_id = '2001'
        ORDER BY engagements.kind, accounts.handle
        """
    ).fetchall()
    assert rows == [
        ("sample_user_1", "like"),
        ("sample_user_3", "quote"),
        ("sample_user_4", "reply"),
        ("sample_user_2", "repost"),
    ]
    assert conn.execute(
        "SELECT COUNT(*) FROM posts WHERE x_post_id IN ('3001', '3002', '9001')"
    ).fetchone()[0] == 0

    again_posts, again_engagements = load_posts_and_engagements(db)
    assert again_posts["posts"] == 3
    assert again_posts["accounts"] == 5
    assert again_posts["reposts"] == 1
    assert [item["engagements"] for item in again_engagements] == [4, 4, 4, 4]
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM engagements").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 5
    conn.close()

    completed = run_lint(db)
    assert completed.returncode == 0, completed.stdout
    assert completed.stdout == ""
    for path, original in originals.items():
        assert path.read_bytes() == original


def test_engagement_for_missing_post_is_refused(tmp_path):
    db = tmp_path / "xtend.db"
    run_posts(db, POSTS, "posts", owner="1000")
    before = sqlite3.connect(db)
    posts_before = before.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    accounts_before = before.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    before.close()

    completed = run_posts(db, LIKES, "like", post="2999", check=False)
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr.strip() == "post not in store: 2999"

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    assert conn.execute("SELECT COUNT(*) FROM engagements").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == posts_before
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == accounts_before
    assert conn.execute(
        "SELECT 1 FROM accounts WHERE x_user_id = '1001'"
    ).fetchone() is None
    assert list(conn.execute("PRAGMA foreign_key_check")) == []
    conn.close()

    lint = run_lint(db)
    assert lint.returncode == 0, lint.stdout
    assert lint.stdout == ""


def test_plain_repost_is_flagged_on_the_owner(tmp_path):
    db = tmp_path / "xtend.db"
    summary = run_posts(db, POSTS, "posts", owner="1000")
    assert summary["reposts"] == 1
    assert summary["posts"] == 3

    conn = sqlite3.connect(db)
    rows = conn.execute(
        """
        SELECT posts.x_post_id, posts.text, posts.created_at, posts.is_repost,
               accounts.handle, accounts.x_user_id
        FROM posts
        JOIN accounts ON accounts.id = posts.account_id
        ORDER BY posts.x_post_id
        """
    ).fetchall()
    assert rows == [
        (
            "2001",
            "Synthetic post one.",
            "2026-01-03T15:04:05.000Z",
            0,
            "sample_owner",
            "1000",
        ),
        (
            "2002",
            "Synthetic post by someone else.",
            "2026-01-04T15:04:05.000Z",
            1,
            "sample_owner",
            "1000",
        ),
        (
            "2003",
            "Synthetic quote of another post.",
            "2026-01-05T15:04:05.000Z",
            0,
            "sample_owner",
            "1000",
        ),
    ]
    assert conn.execute(
        "SELECT 1 FROM posts WHERE x_post_id = '9001'"
    ).fetchone() is None
    assert conn.execute(
        "SELECT 1 FROM accounts WHERE x_user_id = '1009'"
    ).fetchone() is None
    assert conn.execute(
        "SELECT follows_me, i_follow FROM accounts WHERE x_user_id = '1000'"
    ).fetchone() == (0, 0)
    conn.close()
