#!/usr/bin/env python3
"""Load a saved X API v2 followers or following response into the SQLite store."""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"
LIST_FLAGS = {
    "followers": "follows_me",
    "following": "i_follow",
}


def as_count(value, where):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where} must be an integer")
    return value


def parse_user(item, index):
    where = f"data[{index}]"
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


def read_users(path):
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
    return [parse_user(item, index) for index, item in enumerate(data)]


def connect(db_path):
    if not SCHEMA_PATH.is_file():
        raise ValueError(f"schema not found: {SCHEMA_PATH}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def dedupe_users(users):
    by_id = {}
    for user in users:
        by_id[user["x_user_id"]] = user
    return list(by_id.values())


def upsert_account(conn, user, flag, snapshot_date):
    follows_me = 1 if flag == "follows_me" else 0
    i_follow = 1 if flag == "i_follow" else 0
    conn.execute(
        f"""
        INSERT INTO accounts (
            handle, x_user_id, follows_me, i_follow, verified,
            followers_count, following_count, last_post_at,
            first_seen, last_seen
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (x_user_id) DO UPDATE SET
            handle = excluded.handle,
            {flag} = 1,
            verified = excluded.verified,
            followers_count = COALESCE(excluded.followers_count, accounts.followers_count),
            following_count = COALESCE(excluded.following_count, accounts.following_count),
            last_post_at = COALESCE(excluded.last_post_at, accounts.last_post_at),
            last_seen = excluded.last_seen
        """,
        (
            user["handle"],
            user["x_user_id"],
            follows_me,
            i_follow,
            user["verified"],
            user["followers_count"],
            user["following_count"],
            user["last_post_at"],
            snapshot_date,
            snapshot_date,
        ),
    )


def write_snapshot(conn, account_id, snapshot_date):
    row = conn.execute(
        "SELECT follows_me, i_follow FROM accounts WHERE id = ?",
        (account_id,),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO snapshots (date, account_id, follows_me, i_follow)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (date, account_id) DO UPDATE SET
            follows_me = excluded.follows_me,
            i_follow = excluded.i_follow
        """,
        (snapshot_date, account_id, row["follows_me"], row["i_follow"]),
    )


def ingest(conn, users, list_kind, snapshot_date):
    if list_kind not in LIST_FLAGS:
        raise ValueError("list must be followers or following")
    flag = LIST_FLAGS[list_kind]
    users = dedupe_users(users)
    with conn:
        conn.execute("DROP TABLE IF EXISTS ingest_ids")
        conn.execute("CREATE TEMP TABLE ingest_ids (x_user_id TEXT PRIMARY KEY)")
        conn.executemany(
            "INSERT INTO ingest_ids (x_user_id) VALUES (?)",
            [(user["x_user_id"],) for user in users],
        )
        for user in users:
            upsert_account(conn, user, flag, snapshot_date)
        present_ids = [
            row["id"]
            for row in conn.execute(
                """
                SELECT accounts.id
                FROM accounts
                JOIN ingest_ids ON ingest_ids.x_user_id = accounts.x_user_id
                """
            )
        ]
        cleared_ids = [
            row["id"]
            for row in conn.execute(
                f"""
                SELECT id FROM accounts
                WHERE {flag} = 1
                  AND x_user_id NOT IN (SELECT x_user_id FROM ingest_ids)
                """
            )
        ]
        conn.execute(
            f"""
            UPDATE accounts
            SET {flag} = 0
            WHERE {flag} = 1
              AND x_user_id NOT IN (SELECT x_user_id FROM ingest_ids)
            """
        )
        for account_id in present_ids + cleared_ids:
            write_snapshot(conn, account_id, snapshot_date)
        conn.execute("DROP TABLE ingest_ids")
        account_count = conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"]
        snapshot_count = conn.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"]
    return {
        "snapshot_date": snapshot_date,
        "list": list_kind,
        "upserted": len(users),
        "cleared": len(cleared_ids),
        "accounts": account_count,
        "snapshots": snapshot_count,
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
            "Load a saved X API v2 followers or following response into the SQLite store."
        )
    )
    parser.add_argument("--db", required=True, help="SQLite database file to create or update")
    parser.add_argument("--file", required=True, help="Path to the raw JSON response (read only)")
    parser.add_argument(
        "--list",
        required=True,
        choices=tuple(LIST_FLAGS),
        help="followers updates follows_me; following updates i_follow",
    )
    parser.add_argument("--date", help="Snapshot date YYYY-MM-DD (default: today UTC)")
    args = parser.parse_args(argv)
    try:
        snapshot_date = snapshot_date_from_arg(args.date)
        users = read_users(args.file)
        conn = connect(args.db)
        try:
            summary = ingest(conn, users, args.list, snapshot_date)
        finally:
            conn.close()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
