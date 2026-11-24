"""`yt comments`: pull comments into SQLite and flag AI/slop mentions.

The YouTube Data API has no endpoint for pinning a comment (checked against
the live discovery document, revision 20261123), so pinning stays in Studio."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from itertools import islice

from .api import pages
from .db import upsert

AI_PATTERN = re.compile(
    r"""(?ix)
    \b(
        a\.?i\.?(?:[\s-]*(?:generated|made|voice|slop|music|art|song|vocals?))? |
        made\s+(?:with|by)\s+a\.?i |
        artificial\s+intelligence |
        (?:ai)?slop |
        chat\s?gpt | gpt-?\d* |
        suno | udio | elevenlabs | midjourney | sora | veo\s?\d* |
        robot(?:ic)?\s+voice | text[\s-]to[\s-]speech | tts |
        synthetic\s+voice | soulless | low[\s-]effort
    )\b
    """
)


def is_ai_mention(text: str) -> bool:
    return bool(AI_PATTERN.search(text or ""))


@dataclass
class CommentsResult:
    fetched: int = 0
    new: int = 0
    new_flagged: list[dict] = field(default_factory=list)


def _row(c: dict, channel_id: str, video_id: str | None, parent_id: str | None) -> dict:
    s = c["snippet"]
    text = s.get("textOriginal") or s.get("textDisplay") or ""
    return {
        "comment_id": c["id"], "channel_id": channel_id, "video_id": video_id or s.get("videoId"),
        "parent_id": parent_id, "author": s.get("authorDisplayName"),
        "author_channel_id": (s.get("authorChannelId") or {}).get("value"),
        "text": text, "like_count": s.get("likeCount"), "published_at": s.get("publishedAt"),
        "updated_at": s.get("updatedAt"), "flagged_ai": int(is_ai_mention(text)),
    }


def sync(youtube, conn: sqlite3.Connection, channel_id: str, *, full: bool = False,
         max_pages: int | None = 50) -> CommentsResult:
    """Walk threads newest-first. Incremental by default: stop at the first page
    whose threads are all already stored and unchanged (at most max_pages).
    full=True walks every page."""
    res = CommentsResult()
    known = {r[0]: r[1] for r in conn.execute(
        "SELECT comment_id, updated_at FROM comments WHERE channel_id=?", (channel_id,))}
    threads = pages(youtube.commentThreads(), part="snippet,replies", allThreadsRelatedToChannelId=channel_id,
                    maxResults=100, order="time", textFormat="plainText")
    for resp in islice(threads, None if full else max_pages):
        rows: list[dict] = []
        page_all_known = True
        for th in resp.get("items", []):
            top = th["snippet"]["topLevelComment"]
            vid = th["snippet"].get("videoId")
            rows.append(_row(top, channel_id, vid, None))
            replies = th.get("replies", {}).get("comments", [])
            if th["snippet"].get("totalReplyCount", 0) > len(replies):
                replies = _all_replies(youtube, top["id"])
            rows += [_row(r, channel_id, vid, top["id"]) for r in replies]
        for r in rows:
            if r["comment_id"] not in known:
                res.new += 1
                page_all_known = False
                if r["flagged_ai"]:
                    res.new_flagged.append(r)
            elif known[r["comment_id"]] != r["updated_at"]:
                page_all_known = False
        res.fetched += len(rows)
        upsert(conn, "comments", rows, ["comment_id"])
        conn.commit()
        if page_all_known and not full:
            break
    return res


def _all_replies(youtube, parent_id: str) -> list[dict]:
    return [c for resp in pages(youtube.comments(), part="snippet", parentId=parent_id, maxResults=100,
                                textFormat="plainText")
            for c in resp.get("items", [])]


def pin_unsupported_message(conn: sqlite3.Connection, comment_id: str) -> str:
    row = conn.execute("SELECT video_id, author, text FROM comments WHERE comment_id=?",
                       (comment_id,)).fetchone()
    where = (f"https://studio.youtube.com/video/{row['video_id']}/comments" if row
             else "YouTube Studio > Comments")
    return ("The YouTube Data API has no endpoint for pinning comments, so yt cannot pin.\n"
            f"Pin it in Studio: {where}"
            + (f"\nComment by {row['author']}: {row['text'][:120]}" if row else ""))
