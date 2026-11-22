# xtend-bot

XTend keeps a creator's X followers real and grows genuine conversations.

This repo is a tool chest. Each tool does one job from the command line and uses one shared SQLite file.

## Store

`schema.sql` holds the tables. This version has `accounts` and `snapshots`. The ingest tool creates the database file and applies that schema.

## Run the tests

```bash
python -m pip install pytest
python -m pytest
```

The tests use made-up accounts such as `sample_user_1`. They do not use the network.

## Load a followers or following file

Save a raw X API v2 response, then run:

```bash
python tools/ingest_followers.py --db xtend.db --file tests/fixtures/followers_day1.json --list followers
```

Use `--list following` for a following response. The command prints one JSON summary. It does not change the JSON file. Running it again on the same file and the same day keeps the same row counts.

An account that was in an earlier full list and is missing from a later one is stored with that relationship turned off.

`skills/ingest-followers/SKILL.md` says when a bot should run the tool and what the JSON means.

## Check the database

```bash
python tools/db_lint.py --db xtend.db
```

The command reads the SQLite file and `schema.sql`. It prints one line per problem and does not change the file. Exit code 0 means the checks are clean.

`skills/db-lint/SKILL.md` says when a bot should run the tool.
