"""Paths and constants. Everything lives outside the repo."""

from __future__ import annotations

import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
MONETARY_SCOPE = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"

# Users accept these before `yt auth` opens Google's sign-in. Bump POLICY_VERSION
# (the privacy policy's effective date) whenever either page changes materially.
POLICY_VERSION = "2026-11-24"
PRIVACY_URL = "https://wtydev.github.io/yt-cli/privacy.html"
TERMS_URL = "https://wtydev.github.io/yt-cli/terms.html"
YOUTUBE_TERMS_URL = "https://www.youtube.com/t/terms"
GOOGLE_PRIVACY_URL = "https://policies.google.com/privacy"

# Reporting API jobs created on first sync. channel_reach_basic_a1 carries
# thumbnail impressions and CTR, which the Analytics API does not expose.
PRIVACY = ("private", "unlisted", "public")
DEFAULT_REPORT_TYPES = ["channel_reach_basic_a1"]


def config_dir() -> Path:
    return Path(os.environ.get("YT_CLI_CONFIG_DIR", Path.home() / ".config" / "yt-cli"))


def data_dir() -> Path:
    return Path(os.environ.get("YT_CLI_DATA_DIR", Path.home() / ".local" / "share" / "yt-cli"))


def acceptance_path() -> Path:
    return config_dir() / "accepted-policies.json"


def client_secret_path() -> Path:
    return config_dir() / "client_secret.json"


CHANNEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def token_path(channel: str) -> Path:
    if not CHANNEL_NAME.fullmatch(channel):
        raise SystemExit(f"Invalid channel name '{channel}': use letters, digits, '-' or '_'.")
    return config_dir() / "tokens" / f"{channel}.json"


def db_path() -> Path:
    return data_dir() / "analytics.db"


def today_pacific() -> date:
    """YouTube Analytics and Reporting days are Pacific-time days."""
    return datetime.now(PACIFIC).date()


def pacific_day(ts: str | None) -> date | None:
    """RFC 3339 timestamp -> the Pacific date YouTube files it under."""
    return datetime.fromisoformat(ts).astimezone(PACIFIC).date() if ts else None


def pacific_days(start: str | None, end: str | None) -> list[str]:
    """Pacific days in the half-open period [start, end), as ISO dates."""
    a, b = pacific_day(start), pacific_day(end)
    if not a or not b:
        return []
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days)]


def utc_iso(ts: str) -> str:
    """Normalize an RFC 3339 timestamp so string comparison orders by time."""
    return datetime.fromisoformat(ts).astimezone(UTC).isoformat(timespec="microseconds")
