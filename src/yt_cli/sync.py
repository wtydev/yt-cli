"""`yt sync`: pull Data API metadata, Analytics API reports and Reporting API
bulk files into SQLite. Safe to re-run: every table is upserted or replaced
per (video, report) inside a transaction."""

from __future__ import annotations

import csv
import gzip
import io
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from googleapiclient.errors import HttpError

from . import config
from .db import upsert

# Re-pull this many trailing days on every sync: Analytics data lands 1-2 days
# late and is revised for a few days after that.
REFRESH_DAYS = 7

DAILY_METRICS = [
    ("views", "views"),
    ("estimatedMinutesWatched", "watch_minutes"),
    ("averageViewDuration", "avg_view_duration_s"),
    ("averageViewPercentage", "avg_view_pct"),
    ("subscribersGained", "subs_gained"),
    ("subscribersLost", "subs_lost"),
    ("likes", "likes"),
    ("comments", "comments"),
    ("shares", "shares"),
]

# Traffic source types that support an insightTrafficSourceDetail drill-down.
DETAIL_TYPES = {
    "ADVERTISING", "CAMPAIGN_CARD", "END_SCREEN", "EXT_URL", "HASHTAGS",
    "RELATED_VIDEO", "SOUND_PAGE", "SUBSCRIBER", "YT_CHANNEL", "YT_OTHER_PAGE", "YT_SEARCH",
    "VIDEO_REMIXES", "SHORTS_CONTENT_LINKS",
}


@dataclass
class SyncResult:
    channel_id: str
    videos: int = 0
    daily_rows: int = 0
    retention_rows: int = 0
    traffic_rows: int = 0
    breakdown_rows: int = 0
    content_type_rows: int = 0
    jobs_created: list[str] = field(default_factory=list)
    reports_downloaded: int = 0
    reach_rows: int = 0
    warnings: list[str] = field(default_factory=list)
    # breakdowns that failed server-side this run; not retried for later videos
    skipped: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- helpers

def normalize_content_type(v: str | None) -> str | None:
    """The API returns e.g. 'shorts' / 'videoOnDemand'; store SHORTS / VIDEO_ON_DEMAND."""
    if not v:
        return v
    return re.sub(r"(?<=[a-z])(?=[A-Z])", "_", v).upper()


def iso_duration_seconds(s: str | None) -> int | None:
    if not s:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s)
    if not m:
        return None
    d, h, mi, se = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def rows_of(resp: dict) -> list[dict]:
    """Analytics API response -> list of {column: value}."""
    names = [h["name"] for h in resp.get("columnHeaders", [])]
    return [dict(zip(names, r)) for r in resp.get("rows", []) or []]


def query(analytics, start: date, end: date, metrics: str, **kw) -> list[dict]:
    resp = analytics.reports().query(
        ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
        metrics=metrics, **kw,
    ).execute()
    return rows_of(resp)


def _err(e: HttpError) -> str:
    try:
        return f"{e.resp.status} {e.error_details or e._get_reason()}"
    except Exception:  # pragma: no cover
        return str(e)


def published_day(published_at: str | None) -> date:
    """Publish timestamp (UTC) -> Pacific date, the day Analytics starts counting."""
    from datetime import datetime

    if not published_at:
        return date(2005, 4, 23)
    dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    return dt.astimezone(config.PACIFIC).date()


# ---------------------------------------------------------------- Data API

def fetch_channel(youtube) -> dict:
    resp = youtube.channels().list(part="snippet,contentDetails", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        raise SystemExit("This Google account has no YouTube channel.")
    ch = items[0]
    return {
        "channel_id": ch["id"],
        "title": ch["snippet"]["title"],
        "uploads_playlist": ch["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def fetch_videos(youtube, uploads_playlist: str) -> list[dict]:
    ids: list[str] = []
    token = None
    while True:
        resp = youtube.playlistItems().list(
            part="contentDetails", playlistId=uploads_playlist, maxResults=50, pageToken=token
        ).execute()
        ids += [it["contentDetails"]["videoId"] for it in resp.get("items", [])]
        token = resp.get("nextPageToken")
        if not token:
            break
    videos = []
    for i in range(0, len(ids), 50):
        resp = youtube.videos().list(
            part="snippet,contentDetails,status,statistics", id=",".join(ids[i:i + 50]), maxResults=50
        ).execute()
        for v in resp.get("items", []):
            st = v.get("statistics", {})
            videos.append({
                "video_id": v["id"],
                "channel_id": v["snippet"]["channelId"],
                "title": v["snippet"]["title"],
                "published_at": v["snippet"].get("publishedAt"),
                "duration_s": iso_duration_seconds(v["contentDetails"].get("duration")),
                "privacy": v["status"].get("privacyStatus"),
                "view_count": int(st["viewCount"]) if "viewCount" in st else None,
                "like_count": int(st["likeCount"]) if "likeCount" in st else None,
                "comment_count": int(st["commentCount"]) if "commentCount" in st else None,
            })
    return videos


# ---------------------------------------------------------------- Analytics API

def sync_channel_content_types(analytics, conn, res: SyncResult, start: date, end: date) -> None:
    """Shorts vs long form, channel-level daily split."""
    try:
        rows = query(analytics, start, end,
                     "views,estimatedMinutesWatched,subscribersGained,subscribersLost",
                     dimensions="day,creatorContentType", sort="day")
        res.content_type_rows += upsert(conn, "channel_daily_content_type", (
            {"channel_id": res.channel_id, "day": r["day"],
             "content_type": normalize_content_type(r["creatorContentType"]),
             "views": r["views"], "watch_minutes": r["estimatedMinutesWatched"],
             "subs_gained": r["subscribersGained"], "subs_lost": r["subscribersLost"]}
            for r in rows), ["channel_id", "day", "content_type"])
    except HttpError as e:
        res.warnings.append(f"channel content-type split: {_err(e)}")


def video_content_type(analytics, video_id: str, start: date, end: date) -> str | None:
    # dimensions=video,creatorContentType is not a supported query; filter per video instead.
    rows = query(analytics, start, end, "views", dimensions="creatorContentType", filters=f"video=={video_id}")
    rows.sort(key=lambda r: r.get("views") or 0, reverse=True)
    return normalize_content_type(rows[0]["creatorContentType"]) if rows else None


def sync_video_daily(analytics, conn, res: SyncResult, video_id: str, start: date, end: date) -> None:
    last = conn.execute("SELECT max(day) FROM video_daily WHERE video_id=?", (video_id,)).fetchone()[0]
    if last:
        start = max(start, date.fromisoformat(last) - timedelta(days=REFRESH_DAYS))
    if start > end:
        return
    rows = query(analytics, start, end, ",".join(m for m, _ in DAILY_METRICS),
                 dimensions="day", filters=f"video=={video_id}", sort="day")
    res.daily_rows += upsert(conn, "video_daily", (
        {"video_id": video_id, "day": r["day"], **{col: r.get(m) for m, col in DAILY_METRICS}}
        for r in rows), ["video_id", "day"])


def _replace(conn: sqlite3.Connection, table: str, where: dict, rows: list[dict], keys: list[str]) -> int:
    conn.execute(f"DELETE FROM {table} WHERE " + " AND ".join(f"{k}=?" for k in where), list(where.values()))
    return upsert(conn, table, rows, keys)


def sync_video_lifetime(analytics, conn, res: SyncResult, video_id: str, start: date, end: date) -> None:
    f = f"video=={video_id}"

    def safe(label, fn):
        if label in res.skipped:
            return
        try:
            fn()
        except HttpError as e:
            res.warnings.append(f"{video_id} {label}: {_err(e)}")
            if int(e.resp.status) >= 500:
                res.skipped.append(label)  # server-side failure: don't hammer it per video

    def retention():
        try:
            rows = query(analytics, start, end, "audienceWatchRatio,relativeRetentionPerformance",
                         dimensions="elapsedVideoTimeRatio", filters=f)
        except HttpError:
            rows = query(analytics, start, end, "audienceWatchRatio",
                         dimensions="elapsedVideoTimeRatio", filters=f)
        res.retention_rows += _replace(conn, "video_retention", {"video_id": video_id}, [
            {"video_id": video_id, "elapsed_ratio": r["elapsedVideoTimeRatio"],
             "audience_watch_ratio": r.get("audienceWatchRatio"),
             "relative_retention": r.get("relativeRetentionPerformance")} for r in rows],
            ["video_id", "elapsed_ratio"])

    def traffic():
        rows = query(analytics, start, end, "views,estimatedMinutesWatched",
                     dimensions="insightTrafficSourceType", filters=f, sort="-views")
        out = [{"video_id": video_id, "source_type": r["insightTrafficSourceType"], "source_detail": "",
                "views": r["views"], "watch_minutes": r["estimatedMinutesWatched"]} for r in rows]
        for r in rows:
            t = r["insightTrafficSourceType"]
            if t not in DETAIL_TYPES or not r["views"]:
                continue
            try:
                det = query(analytics, start, end, "views,estimatedMinutesWatched",
                            dimensions="insightTrafficSourceDetail",
                            filters=f"{f};insightTrafficSourceType=={t}", sort="-views", maxResults=25)
            except HttpError as e:
                res.warnings.append(f"{video_id} traffic detail {t}: {_err(e)}")
                continue
            out += [{"video_id": video_id, "source_type": t,
                     "source_detail": d["insightTrafficSourceDetail"] or "(unknown)",
                     "views": d["views"], "watch_minutes": d["estimatedMinutesWatched"]} for d in det]
        res.traffic_rows += _replace(conn, "video_traffic", {"video_id": video_id}, out,
                                     ["video_id", "source_type", "source_detail"])

    def breakdown(dimension: str, dims: str, metrics: str, value_fn, **kw):
        def run():
            rows = query(analytics, start, end, metrics, dimensions=dims, filters=f, **kw)
            res.breakdown_rows += _replace(
                conn, "video_breakdown", {"video_id": video_id, "dimension": dimension},
                [{"video_id": video_id, "dimension": dimension, "value": value_fn(r),
                  "views": r.get("views"), "watch_minutes": r.get("estimatedMinutesWatched"),
                  "viewer_pct": r.get("viewerPercentage")} for r in rows],
                ["video_id", "dimension", "value"])
        safe(dimension, run)

    safe("retention", retention)
    safe("traffic", traffic)
    vm = "views,estimatedMinutesWatched"
    breakdown("subscribedStatus", "subscribedStatus", vm, lambda r: r["subscribedStatus"])
    breakdown("country", "country", vm, lambda r: r["country"], sort="-views", maxResults=50)
    breakdown("ageGender", "ageGroup,gender", "viewerPercentage",
              lambda r: f"{r['ageGroup']}|{r['gender']}")
    breakdown("deviceType", "deviceType", vm, lambda r: r["deviceType"])


# ---------------------------------------------------------------- Reporting API

def ensure_jobs(reporting, conn, res: SyncResult, report_types: list[str]) -> list[dict]:
    existing: list[dict] = []
    token = None
    while True:
        resp = reporting.jobs().list(pageToken=token).execute()
        existing += resp.get("jobs", [])
        token = resp.get("nextPageToken")
        if not token:
            break
    have = {j["reportTypeId"] for j in existing}
    for t in report_types:
        if t in have:
            continue
        job = reporting.jobs().create(body={"reportTypeId": t, "name": f"yt-cli {t}"}).execute()
        existing.append(job)
        res.jobs_created.append(t)
    jobs = [j for j in existing if j["reportTypeId"] in report_types]
    upsert(conn, "reporting_jobs", ({"job_id": j["id"], "channel_id": res.channel_id,
                                     "report_type": j["reportTypeId"], "create_time": j.get("createTime")}
                                    for j in jobs), ["job_id"])
    return jobs


def _download(service, url: str) -> bytes:
    resp, content = service._http.request(url)
    if int(resp.status) != 200:
        raise RuntimeError(f"report download failed: HTTP {resp.status}")
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return content


def ingest_reach_csv(conn, channel_id: str, text: str, create_time: str) -> int:
    """Aggregate channel_reach_basic_a1 rows to (day, video). A newer report for
    the same day replaces the older one; an older one never overwrites."""
    agg: dict[tuple[str, str], list[float]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        d = row["date"]
        day = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
        imp = float(row.get("video_thumbnail_impressions") or 0)
        ctr = float(row.get("video_thumbnail_impressions_ctr") or 0)
        a = agg.setdefault((day, row["video_id"]), [0.0, 0.0])
        a[0] += imp
        a[1] += imp * ctr
    n = 0
    for day in sorted({d for d, _ in agg}):
        newer = conn.execute(
            "SELECT 1 FROM reach_daily WHERE channel_id=? AND day=? AND source_create_time>? LIMIT 1",
            (channel_id, day, create_time)).fetchone()
        if newer:
            continue
        conn.execute("DELETE FROM reach_daily WHERE channel_id=? AND day=?", (channel_id, day))
        n += upsert(conn, "reach_daily", (
            {"channel_id": channel_id, "day": d, "video_id": v, "impressions": int(a[0]),
             "clicks": a[1], "source_create_time": create_time}
            for (d, v), a in agg.items() if d == day), ["channel_id", "day", "video_id"])
    return n


def sync_reports(reporting, conn, res: SyncResult, jobs: list[dict]) -> None:
    for job in jobs:
        token = None
        while True:
            resp = reporting.jobs().reports().list(jobId=job["id"], pageToken=token).execute()
            for rep in resp.get("reports", []):
                if conn.execute("SELECT 1 FROM report_files WHERE report_id=?", (rep["id"],)).fetchone():
                    continue
                text = _download(reporting, rep["downloadUrl"]).decode("utf-8")
                n = 0
                if job["reportTypeId"].startswith("channel_reach"):
                    n = ingest_reach_csv(conn, res.channel_id, text, rep["createTime"])
                    res.reach_rows += n
                upsert(conn, "report_files", [{
                    "report_id": rep["id"], "job_id": job["id"], "report_type": job["reportTypeId"],
                    "start_time": rep.get("startTime"), "end_time": rep.get("endTime"),
                    "create_time": rep.get("createTime"), "rows": n}], ["report_id"])
                conn.commit()
                res.reports_downloaded += 1
            token = resp.get("nextPageToken")
            if not token:
                break


# ---------------------------------------------------------------- entry point

def run(services, conn: sqlite3.Connection, name: str, *, today: date | None = None,
        report_types: list[str] | None = None, video_ids: list[str] | None = None,
        progress=lambda msg: None) -> SyncResult:
    end = today or config.today_pacific()
    ch = fetch_channel(services.youtube)
    upsert(conn, "channels", [{"name": name, **ch}], ["name"])
    res = SyncResult(channel_id=ch["channel_id"])

    progress("videos")
    videos = fetch_videos(services.youtube, ch["uploads_playlist"])
    upsert(conn, "videos", videos, ["video_id"])
    res.videos = len(videos)
    conn.commit()

    earliest = min((published_day(v["published_at"]) for v in videos), default=end)
    sync_channel_content_types(services.analytics, conn, res, earliest, end)
    conn.commit()

    for i, v in enumerate(videos, 1):
        if video_ids and v["video_id"] not in video_ids:
            continue
        if v["privacy"] == "private" and not v["view_count"]:
            continue  # nothing to report yet (e.g. an unpublished upload)
        progress(f"video {i}/{len(videos)} {v['video_id']}")
        start = published_day(v["published_at"])
        try:
            ctype = video_content_type(services.analytics, v["video_id"], start, end)
            if ctype:
                conn.execute("UPDATE videos SET content_type=? WHERE video_id=?", (ctype, v["video_id"]))
        except HttpError as e:
            res.warnings.append(f"{v['video_id']} content type: {_err(e)}")
        try:
            sync_video_daily(services.analytics, conn, res, v["video_id"], start, end)
        except HttpError as e:
            res.warnings.append(f"{v['video_id']} daily: {_err(e)}")
        sync_video_lifetime(services.analytics, conn, res, v["video_id"], start, end)
        conn.commit()

    progress("reporting")
    try:
        jobs = ensure_jobs(services.reporting, conn, res, report_types or config.DEFAULT_REPORT_TYPES)
        conn.commit()
        sync_reports(services.reporting, conn, res, jobs)
    except HttpError as e:
        res.warnings.append(f"reporting: {_err(e)}")
    conn.commit()
    return res


def summary(res: SyncResult) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(res)
