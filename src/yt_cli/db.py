"""SQLite storage. Every write is an upsert keyed on natural keys, so re-running
a sync over the same window converges to the same rows."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    name TEXT PRIMARY KEY,
    channel_id TEXT UNIQUE NOT NULL,
    title TEXT,
    uploads_playlist TEXT,
    added_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    title TEXT,
    published_at TEXT,
    duration_s INTEGER,
    content_type TEXT,          -- SHORTS / VIDEO_ON_DEMAND / LIVE_STREAM ...
    privacy TEXT,
    view_count INTEGER,
    like_count INTEGER,
    comment_count INTEGER,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS video_daily (
    video_id TEXT NOT NULL,
    day TEXT NOT NULL,
    views INTEGER, watch_minutes REAL, avg_view_duration_s REAL, avg_view_pct REAL,
    subs_gained INTEGER, subs_lost INTEGER, likes INTEGER, comments INTEGER, shares INTEGER,
    PRIMARY KEY (video_id, day)
);
CREATE TABLE IF NOT EXISTS video_retention (
    video_id TEXT NOT NULL,
    elapsed_ratio REAL NOT NULL,
    audience_watch_ratio REAL,
    relative_retention REAL,
    PRIMARY KEY (video_id, elapsed_ratio)
);
-- source_detail = '' for the type-level row.
CREATE TABLE IF NOT EXISTS video_traffic (
    video_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_detail TEXT NOT NULL DEFAULT '',
    views INTEGER, watch_minutes REAL,
    PRIMARY KEY (video_id, source_type, source_detail)
);
-- dimension in (subscribedStatus, country, ageGender, deviceType); lifetime totals.
CREATE TABLE IF NOT EXISTS video_breakdown (
    video_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    value TEXT NOT NULL,
    views INTEGER, watch_minutes REAL, viewer_pct REAL,
    PRIMARY KEY (video_id, dimension, value)
);
CREATE TABLE IF NOT EXISTS channel_daily_content_type (
    channel_id TEXT NOT NULL,
    day TEXT NOT NULL,
    content_type TEXT NOT NULL,
    views INTEGER, watch_minutes REAL, subs_gained INTEGER, subs_lost INTEGER,
    PRIMARY KEY (channel_id, day, content_type)
);
CREATE TABLE IF NOT EXISTS reporting_jobs (
    job_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    report_type TEXT NOT NULL,
    create_time TEXT
);
CREATE TABLE IF NOT EXISTS report_files (
    report_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    report_type TEXT,
    start_time TEXT, end_time TEXT, create_time TEXT,
    rows INTEGER,
    downloaded_at TEXT DEFAULT (datetime('now'))
);
-- Aggregated per (day, video) from channel_reach_basic_a1. day is a Pacific date.
CREATE TABLE IF NOT EXISTS reach_daily (
    channel_id TEXT NOT NULL,
    day TEXT NOT NULL,
    video_id TEXT NOT NULL,
    impressions INTEGER,
    clicks REAL,               -- sum(impressions * ctr); ctr = clicks / impressions
    source_create_time TEXT,   -- create_time of the report these rows came from
    PRIMARY KEY (channel_id, day, video_id)
);
CREATE TABLE IF NOT EXISTS comments (
    comment_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    video_id TEXT,
    parent_id TEXT,
    author TEXT,
    author_channel_id TEXT,
    text TEXT,
    like_count INTEGER,
    published_at TEXT,
    updated_at TEXT,
    flagged_ai INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS comments_video ON comments(video_id);
CREATE INDEX IF NOT EXISTS videos_channel ON videos(channel_id, published_at);
CREATE INDEX IF NOT EXISTS reach_video ON reach_daily(video_id, day);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.db_path()
    on_disk = str(path) != ":memory:"
    if on_disk:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if on_disk:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def upsert(conn: sqlite3.Connection, table: str, rows: Iterable[Mapping], keys: list[str]) -> int:
    """INSERT ... ON CONFLICT(keys) DO UPDATE for each row. Every row must have
    the same columns as the first. Returns row count."""
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0])
    updates = [c for c in cols if c not in keys]
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        f" ON CONFLICT({', '.join(keys)}) DO "
        + (f"UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in updates)}" if updates else "NOTHING")
    )
    conn.executemany(sql, ([row[c] for c in cols] for row in rows))
    return len(rows)


def channel_id_for(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute("SELECT channel_id FROM channels WHERE name=?", (name,)).fetchone()
    if not row:
        raise SystemExit(f"Unknown channel '{name}'. Run: yt auth {name}")
    return row["channel_id"]
