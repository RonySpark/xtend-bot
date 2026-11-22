# ingest_posts

Use this when saved raw X API v2 pages of the account owner's posts, and of likes, reposts, quotes, and replies on those posts, should update the shared SQLite store. Do not use it to call X.

Input:

    python tools/ingest_posts.py --db DB --file FILE --kind posts --owner X_USER_ID [--date YYYY-MM-DD]
    python tools/ingest_posts.py --db DB --file FILE --kind like|repost|quote|reply --post X_POST_ID [--date YYYY-MM-DD]

`FILE` is not modified. `--kind posts` reads one user-posts page: a `data` array of posts (`id`, `text`, `author_id`, `created_at`, `referenced_tweets`) and `includes.users` for `--owner`. `--kind like` and `--kind repost` read a user page (`id`, `username`). `--kind quote` and `--kind reply` read a post page whose authors are in `includes.users` and whose `referenced_tweets` point at `--post` with type `quoted` or `replied_to`. The date defaults to today, UTC, and is stored on accounts as `first_seen` and `last_seen`.

A plain repost is a post with `referenced_tweets` type `retweeted`. It is stored on the owner's account with `is_repost` 1. It is not an original post for scoring. Quotes and replies by the owner are stored with `is_repost` 0. The referenced post itself is not stored. A quote or reply page stores an engagement for its author and does not store that quote or reply as a post.

An engagement whose post is not already in the store is refused. The command exits 1, prints `post not in store: X_POST_ID`, and writes no engagement row.

Output is one JSON object. For posts: `snapshot_date`, `kind`, `upserted`, `reposts`, `posts`, and `accounts`. For an engagement: `snapshot_date`, `kind`, `x_post_id`, `upserted`, `engagements`, and `accounts`. `posts`, `engagements`, and `accounts` are table row counts. `reposts` counts plain reposts in this file. Exit code 0 means success.

The same file does not add rows. A page does not delete posts or engagements that are absent from the file. Accounts already in the store keep `follows_me` and `i_follow`. A page that omits `verified` leaves an existing account's `verified` unchanged. A new account from that page is stored with `verified` 0. A page that sets `verified` to true or false updates the stored value. A missing username is refused, so `handle` is always taken from the page.
