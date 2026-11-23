"""Paths and constants. Everything lives outside the repo."""

from __future__ import annotations

import os
from datetime import date, datetime
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

# Reporting API jobs created on first sync. channel_reach_basic_a1 carries
# thumbnail impressions and CTR, which the Analytics API does not expose.
DEFAULT_REPORT_TYPES = ["channel_reach_basic_a1"]


def config_dir() -> Path:
    return Path(os.environ.get("YT_CLI_CONFIG_DIR", Path.home() / ".config" / "yt-cli"))


def data_dir() -> Path:
    return Path(os.environ.get("YT_CLI_DATA_DIR", Path.home() / ".local" / "share" / "yt-cli"))


def client_secret_path() -> Path:
    return config_dir() / "client_secret.json"


def token_path(channel: str) -> Path:
    return config_dir() / "tokens" / f"{channel}.json"


def db_path() -> Path:
    return data_dir() / "analytics.db"


def today_pacific() -> date:
    """YouTube Analytics and Reporting days are Pacific-time days."""
    return datetime.now(PACIFIC).date()
