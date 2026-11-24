---
name: youtube-comment-triage
description: Pull and triage comments on the user's own YouTube channel with the yt CLI or MCP server — new comments, replies, and comments that call a video AI-generated or "slop". Use when the user asks about comments, audience feedback, criticism, questions to answer, or AI accusations.
---

# Comment triage with yt

| Task | CLI | MCP tool |
|---|---|---|
| Pull new comments and list the latest | `yt comments <channel>` | `yt_comments(channel)` |
| Only AI/slop mentions | `yt comments <channel> --flagged` | `yt_comments(channel, flagged_only=true)` |
| List from the database, no API calls | `yt comments <channel> --no-fetch` | `yt_comments(channel, fetch=false)` |
| Re-walk every thread (slower) | `yt comments <channel> --full` | `yt_comments(channel, full=true)` |
| More rows | `--limit 100` | `limit=100` |

Fetching is incremental: newest first, it stops at the first page of 100 threads that are all
already stored and unchanged, unless `--full`.

## Procedure

1. Pull and list comments. Note `new` and `new_flagged` in the result.
2. Group what you read:
   - **Questions** that deserve an answer (link the video and quote the comment).
   - **Corrections** (a factual error in the video) — these matter most.
   - **AI/slop accusations** (`ai` = flagged). The flag is a keyword match ("AI",
     "AI-generated", "slop", "ChatGPT", "Suno", "TTS", "robotic voice", "soulless", …), so read each one and drop false positives.
   - **Praise or requests** that suggest the next video.
   - **Spam.**
3. Report counts per group, the comments worth acting on, and suggested replies.
   Draft replies for the user to post. yt does not post, edit or delete comments.

## Pinning

The YouTube Data API has no endpoint for pinning. `yt comments <channel> --pin <comment_id>`
prints the YouTube Studio link where the user can pin it by hand.

## Per-video comment counts

```sql
SELECT v.title, count(*) AS comments, sum(c.flagged_ai) AS flagged
FROM comments c JOIN videos v USING(video_id)
GROUP BY c.video_id ORDER BY comments DESC;
```

Run with `yt query "<sql>"` or the `yt_query` MCP tool.
