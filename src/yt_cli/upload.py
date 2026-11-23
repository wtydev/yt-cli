"""`yt upload`: resumable videos.insert with retries, then optional thumbnail
and playlist. Nothing is sent without --yes; the plan is printed first."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from googleapiclient.errors import HttpError, ResumableUploadError
from googleapiclient.http import MediaFileUpload

RETRIABLE_STATUS = {500, 502, 503, 504}
MAX_RETRIES = 8
CHUNK = 8 * 1024 * 1024

CATEGORIES = {
    "film": "1", "film & animation": "1", "autos": "2", "music": "10", "pets": "15",
    "sports": "17", "travel": "19", "gaming": "20", "people": "22", "people & blogs": "22",
    "comedy": "23", "entertainment": "24", "news": "25", "howto": "26", "howto & style": "26",
    "education": "27", "science": "28", "science & technology": "28", "nonprofits": "29",
}
PRIVACY = {"private", "unlisted", "public"}


@dataclass
class UploadSpec:
    file: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    category: str = "27"
    privacy: str = "private"
    publish_at: str | None = None
    thumbnail: str | None = None
    playlist: str | None = None
    made_for_kids: bool = False
    synthetic_media: bool | None = None  # status.containsSyntheticMedia (AI disclosure)

    KEYS = {"file", "title", "description", "tags", "category", "privacy", "publish_at",
            "thumbnail", "playlist", "made_for_kids", "synthetic_media"}

    @classmethod
    def build(cls, meta_file: str | None, overrides: dict[str, Any]) -> "UploadSpec":
        data: dict[str, Any] = {}
        base = Path(".")
        if meta_file:
            p = Path(meta_file)
            data = json.loads(p.read_text())
            base = p.parent
            unknown = set(data) - cls.KEYS
            if unknown:
                raise SystemExit(f"Unknown keys in {meta_file}: {', '.join(sorted(unknown))}")
            for k in ("file", "thumbnail"):  # paths in the JSON are relative to it
                if data.get(k) and not Path(data[k]).is_absolute():
                    data[k] = str(base / data[k])
        data.update({k: v for k, v in overrides.items() if v is not None})
        if isinstance(data.get("tags"), str):
            data["tags"] = [t.strip() for t in data["tags"].split(",") if t.strip()]
        for req in ("file", "title"):
            if not data.get(req):
                raise SystemExit(f"Missing required '{req}' (flag or metadata file).")
        spec = cls(**data)
        spec.validate()
        return spec

    def validate(self) -> None:
        self.category = CATEGORIES.get(str(self.category).lower(), str(self.category))
        if not self.category.isdigit():
            raise SystemExit(f"Unknown category '{self.category}'. Use an ID or one of: "
                             + ", ".join(sorted(CATEGORIES)))
        if self.privacy not in PRIVACY:
            raise SystemExit(f"privacy must be one of {sorted(PRIVACY)}")
        if not Path(self.file).is_file():
            raise SystemExit(f"Video file not found: {self.file}")
        if self.thumbnail and not Path(self.thumbnail).is_file():
            raise SystemExit(f"Thumbnail not found: {self.thumbnail}")
        if len(self.title) > 100:
            raise SystemExit(f"Title is {len(self.title)} chars; YouTube allows 100.")
        if len(self.description.encode()) > 5000:
            raise SystemExit("Description exceeds 5000 bytes.")
        if self.publish_at:
            try:
                dt = datetime.fromisoformat(self.publish_at.replace("Z", "+00:00"))
            except ValueError:
                raise SystemExit("publish_at must be ISO 8601, e.g. 2026-12-01T17:00:00Z")
            if dt.tzinfo is None:
                raise SystemExit("publish_at needs a timezone, e.g. 2026-12-01T17:00:00Z")
            if self.privacy != "private":
                raise SystemExit("publish_at requires privacy=private (YouTube publishes it at that time).")

    def body(self) -> dict:
        status: dict[str, Any] = {"privacyStatus": self.privacy,
                                  "selfDeclaredMadeForKids": self.made_for_kids}
        if self.publish_at:
            status["publishAt"] = self.publish_at
        if self.synthetic_media is not None:
            status["containsSyntheticMedia"] = self.synthetic_media
        return {
            "snippet": {"title": self.title, "description": self.description, "tags": self.tags,
                        "categoryId": self.category},
            "status": status,
        }

    def plan(self) -> dict:
        size = Path(self.file).stat().st_size
        return {
            "file": self.file, "size_mb": round(size / 1e6, 1), "title": self.title,
            "description": (self.description[:80] + "…") if len(self.description) > 80 else self.description,
            "tags": ", ".join(self.tags), "category": self.category, "privacy": self.privacy,
            "publish_at": self.publish_at, "made_for_kids": self.made_for_kids,
            "synthetic_media": self.synthetic_media, "thumbnail": self.thumbnail,
            "playlist": self.playlist,
        }


def _resumable(request, progress=lambda pct: None, sleep=time.sleep) -> dict:
    response, retry = None, 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                progress(int(status.progress() * 100))
            retry = 0
        except (HttpError, ResumableUploadError) as e:
            code = getattr(getattr(e, "resp", None), "status", None)
            if code is None or int(code) not in RETRIABLE_STATUS:
                raise
            retry = _backoff(retry, f"HTTP {code}", sleep)
        except (ConnectionError, TimeoutError, OSError) as e:
            retry = _backoff(retry, type(e).__name__, sleep)
    return response


def _backoff(retry: int, why: str, sleep) -> int:
    retry += 1
    if retry > MAX_RETRIES:
        raise SystemExit(f"Upload failed after {MAX_RETRIES} retries ({why}).")
    delay = random.uniform(0, 2 ** retry)
    print(f"  {why}; retry {retry}/{MAX_RETRIES} in {delay:.1f}s")
    sleep(delay)
    return retry


def run(youtube, spec: UploadSpec, progress=lambda pct: None, sleep=time.sleep) -> dict:
    media = MediaFileUpload(spec.file, chunksize=CHUNK, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=spec.body(), media_body=media,
                                  notifySubscribers=False if spec.privacy == "private" else True)
    video = _resumable(req, progress, sleep)
    out = {"video_id": video["id"], "privacy": video.get("status", {}).get("privacyStatus"),
           "url": f"https://youtu.be/{video['id']}", "thumbnail_set": False, "playlist_item": None}
    if spec.thumbnail:
        youtube.thumbnails().set(videoId=video["id"], media_body=MediaFileUpload(spec.thumbnail)).execute()
        out["thumbnail_set"] = True
    if spec.playlist:
        item = youtube.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": spec.playlist, "resourceId": {"kind": "youtube#video", "videoId": video["id"]}}}).execute()
        out["playlist_item"] = item["id"]
    return out
