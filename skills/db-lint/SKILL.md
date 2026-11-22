# db_lint

Use this when the shared SQLite store should be checked and left unchanged. Do not use it to load followers or following.

Input:

    python tools/db_lint.py --db DB

`DB` is the SQLite file. The tool reads that file and `schema.sql`. It does not write to either one.

Each problem is one line on stdout. Exit code 0 means there are no problems. Exit code 1 means there is at least one.

It checks that:

- Every table and column in `schema.sql` is present.
- `PRAGMA integrity_check` is ok and `PRAGMA foreign_key_check` is empty.
- No snapshot row points at a missing account.
- No rows repeat a unique key declared in `schema.sql`, including a unique index. Snapshots are unique on the date and the account. A quote or reply is unique on the post, the account, the kind, and `source_post_id`. A like or repost has no source post id and is unique on the post, the account, and the kind.
- Timestamp values are ISO 8601 UTC. A calendar date `YYYY-MM-DD` is accepted. A value that includes a time must use `Z` or `+00:00`. The columns checked are `date`, names ending in `_at`, and names ending in `_seen`. Null is allowed only where `schema.sql` does not say `NOT NULL`.
