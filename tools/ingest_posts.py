#!/usr/bin/env python3
"""Load saved X API v2 posts and engagement pages into the SQLite store."""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"
ENGAGEMENT_KINDS = ("like", "repost", "quote", "reply")
REFERENCE_TYPES = {"quote": "quoted", "reply": "replied_to"}


def as_count(value, where):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where} must be an integer")
    return value


def parse_user(item, where):
    if not isinstance(item, dict):
        raise ValueError(f"{where} is not an object")
    user_id = item.get("id")
    username = item.get("username")
    if user_id is None or not isinstance(username, str) or username == "":
        raise ValueError(f"{where} needs id and username")
    metrics = item.get("public_metrics") or {}
    if not isinstance(metrics, dict):
        raise ValueError(f"{where}.public_metrics must be an object")
    last_post_at = item.get("last_post_at")
    if last_post_at is not None and not isinstance(last_post_at, str):
        raise ValueError(f"{where}.last_post_at must be a string")
    return {
        "x_user_id": str(user_id),
        "handle": username,
        "verified": 1 if item.get("verified") is True else 0,
        "followers_count": as_count(
            metrics.get("followers_count"), f"{where}.followers_count"
        ),
        "following_count": as_count(
            metrics.get("following_count"), f"{where}.following_count"
        ),
        "last_post_at": last_post_at,
    }


def require_utc(value, where):
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{where} must be an ISO 8601 UTC timestamp")
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{where} must be an ISO 8601 UTC timestamp") from exc
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{where} must be an ISO 8601 UTC timestamp")


def read_payload(path):
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"file not found: {file_path}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {file_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload


def read_data(payload):
    if "data" not in payload:
        meta = payload.get("meta")
        if isinstance(meta, dict) and meta.get("result_count") == 0:
            return []
        raise ValueError("JSON has no data array")
    data = payload["data"]
    if data is None:
        meta = payload.get("meta")
        if isinstance(meta, dict) and meta.get("result_count") == 0:
            return []
        raise ValueError("JSON data is empty")
    if not isinstance(data, list):
        raise ValueError("JSON data must be an array")
    return data


def users_by_id(payload):
    includes = payload.get("includes")
    if includes is None:
        return {}
    if not isinstance(includes, dict):
        raise ValueError("includes must be an object")
    raw = includes.get("users")
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise ValueError("includes.users must be an array")
    users = {}
    for index, item in enumerate(raw):
        user = parse_user(item, f"includes.users[{index}]")
        users[user["x_user_id"]] = user
    return users


def is_plain_repost(item, where):
    refs = item.get("referenced_tweets")
    if refs is None:
        return False
    if not isinstance(refs, list):
        raise ValueError(f"{where}.referenced_tweets must be an array")
    found = False
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            raise ValueError(f"{where}.referenced_tweets[{index}] is not an object")
        if ref.get("type") == "retweeted":
            found = True
    return found


def references_post(item, x_post_id, ref_type):
    refs = item.get("referenced_tweets")
    if not isinstance(refs, list):
        return False
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        if ref.get("type") == ref_type and str(ref.get("id")) == str(x_post_id):
            return True
    return False


def parse_post(item, index, owner_id):
    where = f"data[{index}]"
    if not isinstance(item, dict):
        raise ValueError(f"{where} is not an object")
    post_id = item.get("id")
    text = item.get("text")
    created_at = item.get("created_at")
    author_id = item.get("author_id")
    if post_id is None:
        raise ValueError(f"{where} needs id")
    if not isinstance(text, str):
        raise ValueError(f"{where}.text must be a string")
    require_utc(created_at, f"{where}.created_at")
    repost = is_plain_repost(item, where)
    if not repost and (author_id is None or str(author_id) != owner_id):
        raise ValueError(f"{where} author is {author_id}, owner is {owner_id}")
    return {
        "x_post_id": str(post_id),
        "text": text,
        "created_at": created_at,
        "is_repost": 1 if repost else 0,
    }


def read_posts(path, owner_id):
    payload = read_payload(path)
    owner = users_by_id(payload).get(str(owner_id))
    if owner is None:
        raise ValueError(f"owner not in file: {owner_id}")
    seen = {}
    for index, item in enumerate(read_data(payload)):
        post = parse_post(item, index, str(owner_id))
        seen[post["x_post_id"]] = post
    return owner, list(seen.values())


def read_engagements(path, kind, x_post_id):
    payload = read_payload(path)
    data = read_data(payload)
    if kind in ("like", "repost"):
        users = [
            parse_user(item, f"data[{index}]") for index, item in enumerate(data)
        ]
    else:
        known = users_by_id(payload)
        ref_type = REFERENCE_TYPES[kind]
        users = []
        for index, item in enumerate(data):
            where = f"data[{index}]"
            if not isinstance(item, dict):
                raise ValueError(f"{where} is not an object")
            if not references_post(item, x_post_id, ref_type):
                raise ValueError(f"{where} does not reference post {x_post_id}")
            author_id = item.get("author_id")
            if author_id is None:
                raise ValueError(f"{where} needs author_id")
            user = known.get(str(author_id))
            if user is None:
                raise ValueError(
                    f"{where} author {author_id} is not in includes.users"
                )
            users.append(user)
    return dedupe_users(users)


def dedupe_users(users):
    by_id = {}
    for user in users:
        by_id[user["x_user_id"]] = user
    return list(by_id.values())


def connect(db_path):
    if not SCHEMA_PATH.is_file():
        raise ValueError(f"schema not found: {SCHEMA_PATH}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def upsert_account(conn, user, snapshot_date):
    conn.execute(
        """
        INSERT INTO accounts (
            handle, x_user_id, follows_me, i_follow, verified,
            followers_count, following_count, last_post_at,
            first_seen, last_seen
        ) VALUES (?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (x_user_id) DO UPDATE SET
            handle = excluded.handle,
            verified = excluded.verified,
            followers_count = COALESCE(excluded.followers_count, accounts.followers_count),
            following_count = COALESCE(excluded.following_count, accounts.following_count),
            last_post_at = COALESCE(excluded.last_post_at, accounts.last_post_at),
            last_seen = excluded.last_seen
        """,
        (
            user["handle"],
            user["x_user_id"],
            user["verified"],
            user["followers_count"],
            user["following_count"],
            user["last_post_at"],
            snapshot_date,
            snapshot_date,
        ),
    )


def count_rows(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def ingest_posts(conn, owner, posts, snapshot_date):
    with conn:
        upsert_account(conn, owner, snapshot_date)
        owner_row = conn.execute(
            "SELECT id FROM accounts WHERE x_user_id = ?",
            (owner["x_user_id"],),
        ).fetchone()
        conn.executemany(
            """
            INSERT INTO posts (x_post_id, account_id, text, created_at, is_repost)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (x_post_id) DO UPDATE SET
                account_id = excluded.account_id,
                text = excluded.text,
                created_at = excluded.created_at,
                is_repost = excluded.is_repost
            """,
            [
                (
                    post["x_post_id"],
                    owner_row["id"],
                    post["text"],
                    post["created_at"],
                    post["is_repost"],
                )
                for post in posts
            ],
        )
        post_count = count_rows(conn, "posts")
        account_count = count_rows(conn, "accounts")
    return {
        "snapshot_date": snapshot_date,
        "kind": "posts",
        "upserted": len(posts),
        "reposts": sum(post["is_repost"] for post in posts),
        "posts": post_count,
        "accounts": account_count,
    }


def ingest_engagements(conn, kind, x_post_id, users, snapshot_date):
    with conn:
        post = conn.execute(
            "SELECT id FROM posts WHERE x_post_id = ?",
            (x_post_id,),
        ).fetchone()
        if post is None:
            raise ValueError(f"post not in store: {x_post_id}")
        for user in users:
            upsert_account(conn, user, snapshot_date)
            account = conn.execute(
                "SELECT id FROM accounts WHERE x_user_id = ?",
                (user["x_user_id"],),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO engagements (post_id, account_id, kind)
                VALUES (?, ?, ?)
                ON CONFLICT (post_id, account_id, kind) DO NOTHING
                """,
                (post["id"], account["id"], kind),
            )
        engagement_count = count_rows(conn, "engagements")
        account_count = count_rows(conn, "accounts")
    return {
        "snapshot_date": snapshot_date,
        "kind": kind,
        "x_post_id": x_post_id,
        "upserted": len(users),
        "engagements": engagement_count,
        "accounts": account_count,
    }


def snapshot_date_from_arg(value):
    if value is None:
        return datetime.now(timezone.utc).date().isoformat()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("--date must be YYYY-MM-DD") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Load saved X API v2 posts and engagement pages into the SQLite store."
        )
    )
    parser.add_argument("--db", required=True, help="SQLite database file to create or update")
    parser.add_argument("--file", required=True, help="Path to the raw JSON page (read only)")
    parser.add_argument(
        "--kind",
        required=True,
        choices=("posts",) + ENGAGEMENT_KINDS,
        help="posts loads the owner's posts; the other kinds load engagements on one post",
    )
    parser.add_argument(
        "--owner",
        help="x_user_id of the account whose posts are in the file",
    )
    parser.add_argument("--post", help="x_post_id the engagement page belongs to")
    parser.add_argument(
        "--date",
        help="Date YYYY-MM-DD for account first_seen and last_seen (default: today UTC)",
    )
    args = parser.parse_args(argv)
    try:
        snapshot_date = snapshot_date_from_arg(args.date)
        if args.kind == "posts":
            if not args.owner:
                raise ValueError("--owner is required for kind posts")
            if args.post is not None:
                raise ValueError("--post is only used for an engagement kind")
            owner, posts = read_posts(args.file, args.owner)
            conn = connect(args.db)
            try:
                summary = ingest_posts(conn, owner, posts, snapshot_date)
            finally:
                conn.close()
        else:
            if not args.post:
                raise ValueError("--post is required for an engagement kind")
            if args.owner is not None:
                raise ValueError("--owner is only used for kind posts")
            users = read_engagements(args.file, args.kind, args.post)
            conn = connect(args.db)
            try:
                summary = ingest_engagements(
                    conn, args.kind, args.post, users, snapshot_date
                )
            finally:
                conn.close()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
