# xtend-bot

XTend keeps a creator's X followers real and grows genuine conversations.

This repo is a tool chest. Each tool does one job from the command line and uses one shared SQLite file.

## Store

`schema.sql` holds the tables. This version has `accounts`, `snapshots`, `posts`, and `engagements`. The ingest tools create the database file and apply that schema.

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

## Load posts and engagements

Save a raw X API v2 user posts page, then run:

```bash
python tools/ingest_posts.py --db xtend.db --file tests/fixtures/posts_page.json --kind posts --owner 1000 --date 2026-01-03
```

Save a likes, reposts, quotes, or replies page for one post, then run:

```bash
python tools/ingest_posts.py --db xtend.db --file tests/fixtures/likes_page.json --kind like --post 2001 --date 2026-01-03
```

`--kind` is `like`, `repost`, `quote`, or `reply`. The command prints one JSON summary. It does not change the JSON file. Running it again on the same file keeps the same row counts. A page does not remove rows that are absent from that file. Each reply and each quote keeps its own post id, so two from the same account on one post are two rows. Likes and reposts stay one row per account per post.

A plain repost in the owner's posts page is stored with `is_repost` set to 1. It is not an original post for scoring. An engagement page whose post is not already in the store is refused, and no engagement row is written.

`skills/ingest-posts/SKILL.md` says when a bot should run the tool and what the JSON means.

## Check the database

```bash
python tools/db_lint.py --db xtend.db
```

The command reads the SQLite file and `schema.sql`. It prints one line per problem and does not change the file. Exit code 0 means the checks are clean.

`skills/db-lint/SKILL.md` says when a bot should run the tool.
