import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from test_ingest_followers import DAY1, run_ingest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "ingest_posts.py"
LINT = ROOT / "tools" / "db_lint.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
POSTS = FIXTURES / "posts_page.json"
LIKES = FIXTURES / "likes_page.json"
REPOSTS = FIXTURES / "reposts_page.json"
QUOTES = FIXTURES / "quotes_page.json"
REPLIES = FIXTURES / "replies_page.json"
REPLIES_TWO = FIXTURES / "replies_two.json"
QUOTES_TWO = FIXTURES / "quotes_two.json"
LIKES_NO_VERIFIED = FIXTURES / "likes_no_verified.json"
LIKES_VERIFIED_FALSE = FIXTURES / "likes_verified_false.json"
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


def verified_of(conn, x_user_id):
    return conn.execute(
        "SELECT verified FROM accounts WHERE x_user_id = ?",
        (x_user_id,),
    ).fetchone()[0]


def test_missing_verified_keeps_existing_flag(tmp_path):
    db = tmp_path / "xtend.db"
    run_ingest(db, DAY1, "followers", "2026-01-01")
    run_posts(db, POSTS, "posts", owner="1000")
    run_posts(db, LIKES_NO_VERIFIED, "like", post="2001")

    conn = sqlite3.connect(db)
    assert verified_of(conn, "1002") == 1
    assert verified_of(conn, "1006") == 0
    conn.close()

    run_posts(db, LIKES_VERIFIED_FALSE, "like", post="2001")
    conn = sqlite3.connect(db)
    assert verified_of(conn, "1002") == 0
    conn.close()


def source_ids(conn, x_user_id, kind):
    rows = conn.execute(
        """
        SELECT engagements.source_post_id
        FROM engagements
        JOIN posts ON posts.id = engagements.post_id
        JOIN accounts ON accounts.id = engagements.account_id
        WHERE posts.x_post_id = '2001'
          AND accounts.x_user_id = ?
          AND engagements.kind = ?
        ORDER BY engagements.source_post_id
        """,
        (x_user_id, kind),
    ).fetchall()
    return [row[0] for row in rows]


def test_two_replies_from_one_person_stay_two_rows(tmp_path):
    original = REPLIES_TWO.read_bytes()
    db = tmp_path / "xtend.db"
    run_posts(db, POSTS, "posts", owner="1000")
    first = run_posts(db, REPLIES_TWO, "reply", post="2001")
    assert first["upserted"] == 2

    conn = sqlite3.connect(db)
    assert source_ids(conn, "1004", "reply") == ["3002", "3004"]
    conn.close()

    second = run_posts(db, REPLIES_TWO, "reply", post="2001")
    conn = sqlite3.connect(db)
    assert source_ids(conn, "1004", "reply") == ["3002", "3004"]
    assert second["engagements"] == 2
    conn.close()
    assert REPLIES_TWO.read_bytes() == original

    run_posts(db, QUOTES_TWO, "quote", post="2001")
    run_posts(db, QUOTES_TWO, "quote", post="2001")
    conn = sqlite3.connect(db)
    assert source_ids(conn, "1003", "quote") == ["3011", "3012"]
    conn.close()

    run_posts(db, LIKES, "like", post="2001")
    run_posts(db, LIKES, "like", post="2001")
    conn = sqlite3.connect(db)
    assert source_ids(conn, "1001", "like") == [None]
    conn.close()

    lint = run_lint(db)
    assert lint.returncode == 0, lint.stdout
    assert lint.stdout == ""


# Schema from before source_post_id. CREATE TABLE IF NOT EXISTS will not add the column.
PRIOR_SCHEMA = """
CREATE TABLE accounts (
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
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts (id),
    follows_me INTEGER NOT NULL CHECK (follows_me IN (0, 1)),
    i_follow INTEGER NOT NULL CHECK (i_follow IN (0, 1)),
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


def test_prior_schema_database_accepts_reply_ingest(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(PRIOR_SCHEMA)
    conn.execute(
        """
        INSERT INTO accounts (
            handle, x_user_id, follows_me, i_follow, verified,
            first_seen, last_seen
        ) VALUES ('sample_owner', '1000', 0, 0, 0, '2026-01-03', '2026-01-03')
        """
    )
    conn.execute(
        """
        INSERT INTO accounts (
            handle, x_user_id, follows_me, i_follow, verified,
            first_seen, last_seen
        ) VALUES ('sample_user_1', '1001', 1, 0, 0, '2026-01-01', '2026-01-01')
        """
    )
    conn.execute(
        """
        INSERT INTO posts (x_post_id, account_id, text, created_at, is_repost)
        VALUES ('2001', 1, 'Synthetic post one.', '2026-01-03T15:04:05.000Z', 0)
        """
    )
    conn.execute(
        """
        INSERT INTO engagements (post_id, account_id, kind)
        VALUES (1, 2, 'like')
        """
    )
    conn.commit()
    conn.close()

    run_posts(db, REPLIES_TWO, "reply", post="2001")
    run_posts(db, REPLIES_TWO, "reply", post="2001")

    conn = sqlite3.connect(db)
    assert "source_post_id" in [
        row[1] for row in conn.execute("PRAGMA table_info(engagements)")
    ]
    assert source_ids(conn, "1004", "reply") == ["3002", "3004"]
    assert source_ids(conn, "1001", "like") == [None]
    conn.close()

    lint = run_lint(db)
    assert lint.returncode == 0, lint.stdout
