# score_neighbours

Use this when the shared SQLite store should be ranked by who engaged with the owner's original posts. Do not use it to load posts or to call X. The database is not changed.

Input:

    python tools/score_neighbours.py --db DB --since UTC --until UTC [--like-weight N] [--repost-weight N] [--quote-weight N] [--reply-weight N]

`--since` is inclusive and `--until` is exclusive. Both are ISO 8601 UTC. The window is the original post's `created_at`. Default weights follow X's home-mixer `param.rs`, not the older x-neighbours defaults: like 0.708, repost 1.0, quote 5.0, and reply 5.0. A printed score is rounded to 3 decimals. Rank uses the exact sum.

A plain repost (`is_repost` 1) adds nothing. The post's author does not score on their own post. Likes and reposts count once per account per post. Each distinct quote counts. Distinct replies from one account on one post count up to 3. Score is likes times like weight, plus reposts times repost weight, plus quotes times quote weight, plus counted replies times reply weight.

Output is one JSON object: `since`, `until`, `weights`, `reply_cap`, and `ranked`. Each ranked row has `rank`, `x_user_id`, `handle`, `score`, `likes`, `reposts`, `quotes`, and `replies`. `replies` is the counted number after the cap. Highest score is first. Equal scores put the larger reply-plus-quote count first, then the smaller `x_user_id`. Exit code 0 means success.
