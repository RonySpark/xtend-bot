#!/usr/bin/env python3
"""Rank accounts that engaged with the owner's original posts."""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# X home-mixer weights.
# https://github.com/xai-org/x-algorithm/blob/269340ce2eb8ca785bafa4f2c17a2e8b752bccd3/home-mixer/params/param.rs
# FavoriteWeight -> like, RetweetWeight -> repost, QuoteWeight -> quote, ReplyWeight -> reply.
X_WEIGHTS = {
    "like": 0.708,
    "repost": 1.0,
    "quote": 5.0,
    "reply": 5.0,
}
REPLY_CAP = 3
SCORE_DECIMALS = 3


def parse_utc(value, label):
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{label} must be an ISO 8601 UTC timestamp")
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO 8601 UTC timestamp") from exc
    if parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be an ISO 8601 UTC timestamp")
    return parsed.astimezone(timezone.utc)


def in_window(created_at, since, until):
    if not created_at:
        return False
    try:
        when = parse_utc(created_at, "created_at")
    except ValueError:
        return False
    if when < since:
        return False
    if until is not None and when >= until:
        return False
    return True


def open_readonly(path):
    file_path = Path(path)
    if not file_path.is_file():
        raise ValueError(f"database not found: {file_path}")
    conn = sqlite3.connect(file_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def score(conn, since, until, weights):
    posts = []
    for row in conn.execute(
        """
        SELECT id, created_at, is_repost
        FROM posts
        """
    ):
        if row["is_repost"]:
            continue
        if not in_window(row["created_at"], since, until):
            continue
        posts.append(row["id"])
    post_ids = posts
    totals = {}

    def bucket(user_id, handle):
        row = totals.get(user_id)
        if row is None:
            row = {
                "x_user_id": user_id,
                "handle": handle,
                "likes": 0,
                "reposts": 0,
                "quotes": 0,
                "replies": 0,
                "_reply_ids": {},
                "_quote_ids": set(),
            }
            totals[user_id] = row
        return row

    if post_ids:
        placeholders = ",".join("?" for _ in post_ids)
        rows = list(
            conn.execute(
                f"""
                SELECT accounts.x_user_id, accounts.handle, engagements.kind,
                       engagements.post_id, engagements.source_post_id,
                       authors.x_user_id AS author_user_id
                FROM engagements
                JOIN accounts ON accounts.id = engagements.account_id
                JOIN posts ON posts.id = engagements.post_id
                JOIN accounts AS authors ON authors.id = posts.account_id
                WHERE engagements.post_id IN ({placeholders})
                """,
                tuple(post_ids),
            )
        )
        for row in rows:
            if row["author_user_id"] == row["x_user_id"]:
                continue
            person = bucket(row["x_user_id"], row["handle"])
            kind = row["kind"]
            if kind == "like":
                person["likes"] += 1
            elif kind == "repost":
                person["reposts"] += 1
            elif kind == "quote":
                if row["source_post_id"] is not None:
                    person["_quote_ids"].add(row["source_post_id"])
            elif kind == "reply":
                if row["source_post_id"] is None:
                    continue
                by_post = person["_reply_ids"].setdefault(row["post_id"], set())
                by_post.add(row["source_post_id"])

    ranked = []
    for person in totals.values():
        replies = sum(
            min(len(sources), REPLY_CAP) for sources in person["_reply_ids"].values()
        )
        quotes = len(person["_quote_ids"])
        score_value = (
            person["likes"] * weights["like"]
            + person["reposts"] * weights["repost"]
            + quotes * weights["quote"]
            + replies * weights["reply"]
        )
        if score_value <= 0:
            continue
        ranked.append(
            {
                "x_user_id": person["x_user_id"],
                "handle": person["handle"],
                "score": score_value,
                "likes": person["likes"],
                "reposts": person["reposts"],
                "quotes": quotes,
                "replies": replies,
                "_tie_text": replies + quotes,
            }
        )
    ranked.sort(key=lambda row: (-row["score"], -row["_tie_text"], row["x_user_id"]))
    ordered = []
    for index, row in enumerate(ranked, 1):
        ordered.append(
            {
                "rank": index,
                "x_user_id": row["x_user_id"],
                "handle": row["handle"],
                "score": round(row["score"], SCORE_DECIMALS),
                "likes": row["likes"],
                "reposts": row["reposts"],
                "quotes": row["quotes"],
                "replies": row["replies"],
            }
        )
    return ordered


def format_utc(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Rank accounts that engaged with the owner's original posts. "
            "The database is not changed."
        )
    )
    parser.add_argument("--db", required=True, help="SQLite database file to read")
    parser.add_argument("--since", required=True, help="Window start, ISO 8601 UTC, inclusive")
    parser.add_argument("--until", required=True, help="Window end, ISO 8601 UTC, exclusive")
    parser.add_argument("--like-weight", type=float, default=X_WEIGHTS["like"])
    parser.add_argument("--reply-weight", type=float, default=X_WEIGHTS["reply"])
    parser.add_argument("--repost-weight", type=float, default=X_WEIGHTS["repost"])
    parser.add_argument("--quote-weight", type=float, default=X_WEIGHTS["quote"])
    args = parser.parse_args(argv)
    try:
        since = parse_utc(args.since, "--since")
        until = parse_utc(args.until, "--until")
        if since >= until:
            raise ValueError("--since must be earlier than --until")
        weights = {
            "like": args.like_weight,
            "repost": args.repost_weight,
            "quote": args.quote_weight,
            "reply": args.reply_weight,
        }
        conn = open_readonly(args.db)
        try:
            ranked = score(conn, since, until, weights)
        finally:
            conn.close()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"database is not readable: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "since": format_utc(since),
                "until": format_utc(until),
                "weights": weights,
                "reply_cap": REPLY_CAP,
                "ranked": ranked,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
