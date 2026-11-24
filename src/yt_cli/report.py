"""Read-only views over the database: `yt video` and `yt scorecard`."""

from __future__ import annotations

import sqlite3
from statistics import mean, median
from typing import Any

BROWSE_SUGGESTED = ("SUBSCRIBER", "RELATED_VIDEO")  # "Browse features" + "Suggested videos"
OUTLIER_MULTIPLE = 5.0
WINDOW = 4


def retention_at(points: list[tuple[float, float]], ratio: float) -> float | None:
    """Linear interpolation of audienceWatchRatio at an elapsed-time ratio."""
    if not points or ratio < 0 or ratio > 1:
        return None
    points = sorted(points)
    if ratio <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= ratio <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (ratio - x0) / (x1 - x0)
    return points[-1][1]


def video_metrics(conn: sqlite3.Connection, video_id: str) -> dict[str, Any]:
    v = conn.execute("SELECT * FROM videos WHERE video_id=?", (video_id,)).fetchone()
    d = conn.execute(
        "SELECT sum(views) views, sum(watch_minutes) wm, sum(subs_gained) g, sum(subs_lost) l,"
        " sum(avg_view_pct*views) pct_w, sum(likes) likes, sum(comments) comments, sum(shares) shares"
        " FROM video_daily WHERE video_id=?", (video_id,)).fetchone()
    reach = conn.execute("SELECT sum(impressions) imp, sum(clicks) clicks FROM reach_daily WHERE video_id=?",
                         (video_id,)).fetchone()
    traffic = {r["source_type"]: r["views"] or 0 for r in conn.execute(
        "SELECT source_type, views FROM video_traffic WHERE video_id=? AND source_detail=''", (video_id,))}
    ret = [(r[0], r[1]) for r in conn.execute(
        "SELECT elapsed_ratio, audience_watch_ratio FROM video_retention WHERE video_id=?", (video_id,))
        if r[1] is not None]
    ai = conn.execute("SELECT count(*) FROM comments WHERE video_id=? AND flagged_ai=1", (video_id,)).fetchone()[0]

    views = d["views"] or 0
    dur = v["duration_s"]
    total_traffic = sum(traffic.values())
    imp = reach["imp"]
    return {
        "video_id": video_id,
        "title": v["title"],
        "published": (v["published_at"] or "")[:10],
        "type": v["content_type"],
        "duration_s": dur,
        "views": views if d["views"] is not None else v["view_count"],
        "impressions": imp,
        "ctr_pct": (reach["clicks"] / imp * 100) if imp else None,
        "ret_30s_pct": (retention_at(ret, 30 / dur) * 100) if dur and dur >= 30 and ret else None,
        "avg_viewed_pct": (d["pct_w"] / views) if views and d["pct_w"] is not None else None,
        "subs_per_1k": ((d["g"] or 0) - (d["l"] or 0)) / views * 1000 if views else None,
        "browse_sugg_pct": sum(traffic.get(t, 0) for t in BROWSE_SUGGESTED) / total_traffic * 100
        if total_traffic else None,
        "shorts_feed_pct": traffic.get("SHORTS", 0) / total_traffic * 100 if total_traffic else None,
        "ai_comments": ai,
    }


SCORE_METRICS = ["impressions", "ctr_pct", "ret_30s_pct", "avg_viewed_pct", "subs_per_1k",
                 "browse_sugg_pct", "ai_comments"]


def _avg(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return mean(vals) if vals else None


# Analytics views when synced, else the public counter (same rule as video_metrics).
VIEWS_SQL = ("SELECT v.video_id, v.title, substr(v.published_at, 1, 10) AS published,"
             " coalesce(sum(d.views), v.view_count) AS views"
             " FROM videos v LEFT JOIN video_daily d USING(video_id) WHERE v.channel_id=?")


def scorecard(conn: sqlite3.Connection, channel_id: str, content_type: str | None = None) -> dict[str, Any]:
    sql, args = VIEWS_SQL + " AND v.privacy='public'", [channel_id]
    if content_type:
        sql += " AND v.content_type=?"
        args.append(content_type)
    per_video = [dict(r) for r in conn.execute(sql + " GROUP BY v.video_id ORDER BY v.published_at DESC", args)]
    # Full metrics only for the two windows; the median and outliers need views alone.
    last = [video_metrics(conn, r["video_id"]) for r in per_video[:WINDOW]]
    prev = [video_metrics(conn, r["video_id"]) for r in per_video[WINDOW:2 * WINDOW]]

    trend = []
    for m in SCORE_METRICS:
        a, b = _avg(last, m), _avg(prev, m)
        change = ((a - b) / abs(b) * 100) if a is not None and b not in (None, 0) else None
        trend.append({"metric": m, f"last_{WINDOW}": a, f"prev_{WINDOW}": b, "change_pct": change})

    views = [v["views"] for v in per_video if v["views"] is not None]
    med = median(views) if views else None
    outliers = [
        {"video_id": v["video_id"], "title": v["title"], "published": v["published"], "views": v["views"],
         "x_median": v["views"] / med}
        for v in per_video if med and v["views"] is not None and v["views"] >= OUTLIER_MULTIPLE * med
    ]
    return {
        "channel_id": channel_id,
        "content_type": content_type or "all",
        "videos_considered": len(per_video),
        "median_views": med,
        "trend": trend,
        "last_videos": last,
        "outliers": outliers,
        "ai_comments_last": sum(v["ai_comments"] for v in last),
        "ai_comments_prev": sum(v["ai_comments"] for v in prev),
        "reach_days": conn.execute("SELECT count(DISTINCT day) FROM reach_daily WHERE channel_id=?",
                                   (channel_id,)).fetchone()[0],
    }


def video_detail(conn: sqlite3.Connection, video_id: str) -> dict[str, Any]:
    if not conn.execute("SELECT 1 FROM videos WHERE video_id=?", (video_id,)).fetchone():
        raise SystemExit(f"Video {video_id} is not in the database. Run: yt sync <channel>")
    q = lambda sql: [dict(r) for r in conn.execute(sql, (video_id,))]
    return {
        "summary": video_metrics(conn, video_id),
        "retention": q("SELECT elapsed_ratio, audience_watch_ratio, relative_retention"
                       " FROM video_retention WHERE video_id=? ORDER BY elapsed_ratio"),
        "traffic": q("SELECT source_type, views, watch_minutes FROM video_traffic"
                     " WHERE video_id=? AND source_detail='' ORDER BY views DESC"),
        "traffic_detail": q("SELECT source_type, source_detail, views FROM video_traffic"
                            " WHERE video_id=? AND source_detail!='' ORDER BY views DESC LIMIT 20"),
        "subscribed": q("SELECT value, views, watch_minutes FROM video_breakdown"
                        " WHERE video_id=? AND dimension='subscribedStatus' ORDER BY views DESC"),
        "countries": q("SELECT value AS country, views, watch_minutes FROM video_breakdown"
                       " WHERE video_id=? AND dimension='country' ORDER BY views DESC LIMIT 10"),
        "age_gender": q("SELECT value AS age_gender, viewer_pct FROM video_breakdown"
                        " WHERE video_id=? AND dimension='ageGender' AND viewer_pct>0 ORDER BY viewer_pct DESC"),
        "devices": q("SELECT value AS device, views, watch_minutes FROM video_breakdown"
                     " WHERE video_id=? AND dimension='deviceType' ORDER BY views DESC"),
        "daily": q("SELECT day, views, watch_minutes, avg_view_pct, subs_gained, subs_lost, likes, shares"
                   " FROM video_daily WHERE video_id=? ORDER BY day"),
        "reach_daily": q("SELECT day, impressions, CASE WHEN impressions>0 THEN clicks*100.0/impressions END"
                         " AS ctr_pct FROM reach_daily WHERE video_id=? ORDER BY day"),
    }


def best_video(conn: sqlite3.Connection, channel_id: str) -> str | None:
    # Prefer analytics views: the newest upload can lead on the public counter
    # while having no Analytics rows yet (1-2 day lag), which makes an empty report.
    r = conn.execute(VIEWS_SQL + " GROUP BY v.video_id ORDER BY coalesce(sum(d.views), 0) DESC,"
                     " v.view_count DESC LIMIT 1", (channel_id,)).fetchone()
    return r[0] if r else None
