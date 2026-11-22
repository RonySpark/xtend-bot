import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "score_neighbours.py"
SCHEMA = ROOT / "schema.sql"
SINCE = "2026-01-01T00:00:00Z"
UNTIL = "2026-01-06T00:00:00Z"

# X home-mixer weights from param.rs @ 269340ce2e:
# like FavoriteWeight 0.708, repost RetweetWeight 1.0,
# quote QuoteWeight 5.0, reply ReplyWeight 5.0.
# At most 3 distinct replies from one account count on one original post.
# The window is [since, until). A plain repost adds nothing, and the post's
# author does not score on their own post. Displayed scores are rounded to
# 3 decimals. Rank uses the exact sum.
#
# sample_user_1: 1*0.708 + min(4, 3)*5.0 + 1*5.0 = 0.708 + 15 + 5 = 20.708.
#   A like on plain repost 2002 adds 0. A like on 2009, after the window, adds 0.
# sample_user_4: 2*5.0 + 2*5.0 = 20.0.
# sample_user_3: 2*5.0 = 10.0. A like on plain repost 2002 adds 0.
# sample_user_2: 1*1.0 + 1*5.0 = 6.0.
#   A like on 2010, whose created_at equals until, adds 0.
# sample_user_5: 1*5.0 = 5.0.
# sample_user_6: 1*5.0 = 5.0.
# Equal scores rank the larger reply-plus-quote count first, then the smaller
# x_user_id. Both have 1, so sample_user_5 ranks above sample_user_6.
EXPECTED = {
    "since": SINCE,
    "until": UNTIL,
    "weights": {"like": 0.708, "repost": 1.0, "quote": 5.0, "reply": 5.0},
    "reply_cap": 3,
    "ranked": [
        {
            "rank": 1,
            "x_user_id": "1001",
            "handle": "sample_user_1",
            "score": 20.708,
            "likes": 1,
            "reposts": 0,
            "quotes": 1,
            "replies": 3,
        },
        {
            "rank": 2,
            "x_user_id": "1004",
            "handle": "sample_user_4",
            "score": 20.0,
            "likes": 0,
            "reposts": 0,
            "quotes": 0,
            "replies": 4,
        },
        {
            "rank": 3,
            "x_user_id": "1003",
            "handle": "sample_user_3",
            "score": 10.0,
            "likes": 0,
            "reposts": 0,
            "quotes": 2,
            "replies": 0,
        },
        {
            "rank": 4,
            "x_user_id": "1002",
            "handle": "sample_user_2",
            "score": 6.0,
            "likes": 0,
            "reposts": 1,
            "quotes": 0,
            "replies": 1,
        },
        {
            "rank": 5,
            "x_user_id": "1005",
            "handle": "sample_user_5",
            "score": 5.0,
            "likes": 0,
            "reposts": 0,
            "quotes": 0,
            "replies": 1,
        },
        {
            "rank": 6,
            "x_user_id": "1006",
            "handle": "sample_user_6",
            "score": 5.0,
            "likes": 0,
            "reposts": 0,
            "quotes": 1,
            "replies": 0,
        },
    ],
}


def add_account(conn, handle, x_user_id):
    conn.execute(
        """
        INSERT INTO accounts (
            handle, x_user_id, follows_me, i_follow, verified,
            first_seen, last_seen
        ) VALUES (?, ?, 0, 0, 0, '2026-01-01', '2026-01-01')
        """,
        (handle, x_user_id),
    )


def add_post(conn, x_post_id, created_at, is_repost):
    conn.execute(
        """
        INSERT INTO posts (x_post_id, account_id, text, created_at, is_repost)
        VALUES (?, 1, 'Synthetic post.', ?, ?)
        """,
        (x_post_id, created_at, is_repost),
    )


def post_row(conn, x_post_id):
    return conn.execute(
        "SELECT id FROM posts WHERE x_post_id = ?",
        (x_post_id,),
    ).fetchone()[0]


def account_row(conn, x_user_id):
    return conn.execute(
        "SELECT id FROM accounts WHERE x_user_id = ?",
        (x_user_id,),
    ).fetchone()[0]


def add_engagement(conn, x_post_id, x_user_id, kind, source_post_id=None):
    conn.execute(
        """
        INSERT INTO engagements (post_id, account_id, kind, source_post_id)
        VALUES (?, ?, ?, ?)
        """,
        (post_row(conn, x_post_id), account_row(conn, x_user_id), kind, source_post_id),
    )


def build_store(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    add_account(conn, "sample_owner", "1000")
    for handle, user_id in (
        ("sample_user_1", "1001"),
        ("sample_user_2", "1002"),
        ("sample_user_3", "1003"),
        ("sample_user_4", "1004"),
        ("sample_user_5", "1005"),
        ("sample_user_6", "1006"),
    ):
        add_account(conn, handle, user_id)
    add_post(conn, "2001", "2026-01-03T15:04:05.000Z", 0)
    add_post(conn, "2002", "2026-01-04T15:04:05.000Z", 1)
    add_post(conn, "2003", "2026-01-05T12:00:00.000Z", 0)
    add_post(conn, "2009", "2026-01-10T00:00:00.000Z", 0)
    add_post(conn, "2010", "2026-01-06T00:00:00.000Z", 0)
    add_engagement(conn, "2001", "1001", "like")
    for source_id in ("3101", "3102", "3103", "3104"):
        add_engagement(conn, "2001", "1001", "reply", source_id)
    add_engagement(conn, "2001", "1001", "quote", "3201")
    add_engagement(conn, "2002", "1001", "like")
    add_engagement(conn, "2009", "1001", "like")
    add_engagement(conn, "2001", "1000", "like")
    add_engagement(conn, "2001", "1002", "repost")
    add_engagement(conn, "2003", "1002", "reply", "3110")
    add_engagement(conn, "2010", "1002", "like")
    add_engagement(conn, "2001", "1003", "quote", "3211")
    add_engagement(conn, "2001", "1003", "quote", "3212")
    add_engagement(conn, "2002", "1003", "like")
    add_engagement(conn, "2001", "1004", "reply", "3121")
    add_engagement(conn, "2001", "1004", "reply", "3122")
    add_engagement(conn, "2003", "1004", "reply", "3123")
    add_engagement(conn, "2003", "1004", "reply", "3124")
    add_engagement(conn, "2001", "1005", "reply", "3131")
    add_engagement(conn, "2003", "1006", "quote", "3221")
    conn.commit()
    conn.close()


def test_ranking_matches_hand_score(tmp_path):
    db = tmp_path / "xtend.db"
    build_store(db)
    before = db.read_bytes()
    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--db",
            str(db),
            "--since",
            SINCE,
            "--until",
            UNTIL,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == EXPECTED
    assert db.read_bytes() == before
