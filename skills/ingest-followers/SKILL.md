# ingest_followers

Use this when a saved raw X API v2 followers or following file should update the shared SQLite store. Do not use it to call X.

Input:

    python tools/ingest_followers.py --db DB --file FILE --list followers|following [--date YYYY-MM-DD]

`FILE` is not modified. It is a JSON object with a `data` array of users (`id`, `username`, `public_metrics`). Treat that array as the full list for the relationship. `--list followers` turns on `follows_me`. `--list following` turns on `i_follow`. The date defaults to today, UTC.

Output is one JSON object: `snapshot_date`, `list`, `upserted`, `cleared`, `accounts`, and `snapshots`. `accounts` and `snapshots` are table row counts. Exit code 0 means success.

The same file and date do not add rows. An account missing from a later full list is saved with that relationship set to 0, plus a snapshot row for the new date.
