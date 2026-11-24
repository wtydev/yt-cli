"""`yt upload`: resumable videos.insert with retries, then optional thumbnail
and playlist. Nothing is sent without --yes; the plan is printed first.

Thumbnails wait for processing: thumbnails.set returns 200 even when it is
called too early, and for Shorts YouTube may then drop the image silently
("call this method only after the video has finished processing ... the custom
thumbnail might not be applied", thumbnails.set reference)."""

from __future__ import annotations

import json
import random
import struct
import sys
import time
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

import httplib2
from googleapiclient.errors import HttpError, ResumableUploadError
from googleapiclient.http import MediaFileUpload

from .api import describe, status
from .config import PRIVACY

RETRIABLE_STATUS = {500, 502, 503, 504}
MAX_RETRIES = 8
CHUNK = 8 * 1024 * 1024

# thumbnails.set accepts image/jpeg and image/png up to 50 MB. YouTube Help asks
# for at least 640 px on the long side (16:9 for videos, 9:16 for Shorts).
THUMB_MAX_BYTES = 50 * 1024 * 1024
THUMB_MIN_PX = 640
# Wait this long for processing before setting a thumbnail; it's polled with
# videos.list (1 quota unit per poll).
PROCESS_POLL_S = 15
PROCESS_TIMEOUT_S = 20 * 60

CATEGORIES = {
    "film": "1", "film & animation": "1", "autos": "2", "music": "10", "pets": "15",
    "sports": "17", "travel": "19", "gaming": "20", "people": "22", "people & blogs": "22",
    "comedy": "23", "entertainment": "24", "news": "25", "howto": "26", "howto & style": "26",
    "education": "27", "science": "28", "science & technology": "28", "nonprofits": "29",
}


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

    @classmethod
    def build(cls, meta_file: str | None, overrides: dict[str, Any]) -> UploadSpec:
        data: dict[str, Any] = {}
        base = Path(".")
        if meta_file:
            p = Path(meta_file)
            data = json.loads(p.read_text())
            base = p.parent
            unknown = set(data) - {f.name for f in fields(cls)}
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
            raise SystemExit(f"privacy must be one of {list(PRIVACY)}")
        if not Path(self.file).is_file():
            raise SystemExit(f"Video file not found: {self.file}")
        if self.thumbnail:
            check_thumbnail(self.thumbnail)
        if len(self.title) > 100:
            raise SystemExit(f"Title is {len(self.title)} chars; YouTube allows 100.")
        if len(self.description.encode()) > 5000:
            raise SystemExit("Description exceeds 5000 bytes.")
        if self.publish_at:
            try:
                dt = datetime.fromisoformat(self.publish_at)
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
            "synthetic_media": self.synthetic_media,
            "thumbnail": self.thumbnail and "{} ({}, {}x{})".format(self.thumbnail, *check_thumbnail(self.thumbnail)),
            "playlist": self.playlist,
        }


def image_info(data: bytes) -> tuple[str, int, int] | None:
    """(format, width, height) from a PNG or JPEG header, else None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return "png", w, h
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                return None
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # SOFn
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return "jpeg", w, h
            i += 2 + seg
    return None


def check_thumbnail(path: str) -> tuple[str, int, int]:
    """Validate a thumbnail file before anything is sent. Returns (format, w, h)."""
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"Thumbnail not found: {path}")
    size = p.stat().st_size
    if size > THUMB_MAX_BYTES:
        raise SystemExit(f"Thumbnail is {size / 1e6:.1f} MB; YouTube allows 50 MB.")
    with p.open("rb") as f:
        info = image_info(f.read(256 * 1024))
    if not info:
        raise SystemExit(f"Thumbnail must be a PNG or JPEG image: {path}")
    if max(info[1:]) < THUMB_MIN_PX:
        raise SystemExit(f"Thumbnail is {info[1]}x{info[2]}; YouTube needs at least {THUMB_MIN_PX} px "
                         "on the long side.")
    return info


def wait_processed(youtube, video_id: str, sleep=time.sleep, say=lambda msg: None,
                   timeout_s: float = PROCESS_TIMEOUT_S) -> dict:
    """Poll videos.list until YouTube has finished processing the video.
    Returns the video resource; raises ThumbnailError if processing failed
    or did not finish within timeout_s."""
    waited = 0.0
    while True:
        items = youtube.videos().list(part="status,processingDetails,fileDetails,contentDetails",
                                      id=video_id).execute().get("items", [])
        if not items:
            raise ThumbnailError(f"video {video_id} not found on this channel")
        v = items[0]
        state = v.get("processingDetails", {}).get("processingStatus")
        upload = v.get("status", {}).get("uploadStatus")
        if state == "succeeded" or (state is None and upload == "processed"):
            return v
        if state in ("failed", "terminated") or upload in ("failed", "rejected", "deleted"):
            raise ThumbnailError(f"YouTube did not process the video (processing {state}, upload {upload})")
        if waited >= timeout_s:
            raise ThumbnailError(f"video still processing after {timeout_s / 60:.0f} min; "
                                 f"set it later with: yt thumbnail <channel> {video_id} <file> --yes")
        say(f"waiting for YouTube to process the video ({state or upload})")
        sleep(PROCESS_POLL_S)
        waited += PROCESS_POLL_S


class ThumbnailError(Exception):
    pass


def _dims(video: dict) -> tuple[int, int] | None:
    streams = video.get("fileDetails", {}).get("videoStreams") or []
    if streams and streams[0].get("widthPixels") and streams[0].get("heightPixels"):
        return streams[0]["widthPixels"], streams[0]["heightPixels"]
    return None


def set_thumbnail(youtube, video_id: str, path: str, sleep=time.sleep, say=lambda msg: None,
                  timeout_s: float = PROCESS_TIMEOUT_S) -> dict:
    """Wait for processing, then thumbnails.set. Returns {"thumbnail_set",
    "thumbnail_url", "warnings", "errors"}; thumbnail_set is True only when
    YouTube accepted the image for a fully processed video."""
    fmt, w, h = check_thumbnail(path)
    out: dict[str, Any] = {"thumbnail_set": False, "thumbnail_url": None, "warnings": [], "errors": []}
    try:
        video = wait_processed(youtube, video_id, sleep, say, timeout_s)
    except ThumbnailError as e:
        out["errors"].append(f"thumbnail not set: {e}")
        return out
    except HttpError as e:
        out["errors"].append(f"thumbnail not set: checking processing failed: {describe(e)}")
        return out
    dims = _dims(video)
    vertical = bool(dims and dims[1] > dims[0])
    if dims and (h > w) != vertical:
        want = "9:16 (portrait)" if vertical else "16:9 (landscape)"
        out["warnings"].append(f"thumbnail is {w}x{h} but the video is {dims[0]}x{dims[1]}; "
                               f"YouTube recommends {want}, so it will be cropped or letterboxed")
    try:
        resp = youtube.thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(path, mimetype=f"image/{fmt}")).execute()
    except HttpError as e:
        hint = ""
        if status(e) == 403:
            hint = (" (custom thumbnails need a verified channel: https://www.youtube.com/verify;"
                    " for Shorts, the feature is still rolling out)")
        elif status(e) == 429:
            hint = " (too many thumbnail uploads recently; try again later)"
        out["errors"].append(f"thumbnail: {describe(e)}{hint}")
        return out
    items = resp.get("items") or []
    if not items:
        out["errors"].append("thumbnail: YouTube returned no thumbnail for the image; it was not applied")
        return out
    sizes = items[0]
    best = next((sizes[k] for k in ("maxres", "standard", "high", "medium", "default") if k in sizes), {})
    out["thumbnail_set"] = True
    out["thumbnail_url"] = best.get("url")
    if vertical and _seconds(video) <= 180:
        out["warnings"].append("this looks like a Short: its custom thumbnail shows in search, the channel "
                               "page and subscriptions, but never in the Shorts feed, which picks a frame")
    return out


def _seconds(video: dict) -> int:
    from .sync import iso_duration_seconds
    return iso_duration_seconds(video.get("contentDetails", {}).get("duration")) or 0


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
        except (ConnectionError, TimeoutError, OSError, httplib2.HttpLib2Error) as e:
            retry = _backoff(retry, type(e).__name__, sleep)
    return response


def _backoff(retry: int, why: str, sleep) -> int:
    retry += 1
    if retry > MAX_RETRIES:
        raise SystemExit(f"Upload failed after {MAX_RETRIES} retries ({why}).")
    delay = random.uniform(0, 2 ** retry)
    print(f"  {why}; retry {retry}/{MAX_RETRIES} in {delay:.1f}s", file=sys.stderr)
    sleep(delay)
    return retry


def run(youtube, spec: UploadSpec, progress=lambda pct: None, sleep=time.sleep,
        say=lambda msg: None) -> dict:
    media = MediaFileUpload(spec.file, chunksize=CHUNK, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=spec.body(), media_body=media,
                                  notifySubscribers=spec.privacy != "private")
    video = _resumable(req, progress, sleep)
    out = {"video_id": video["id"], "privacy": video.get("status", {}).get("privacyStatus"),
           "url": f"https://youtu.be/{video['id']}", "thumbnail_set": False, "thumbnail_url": None,
           "playlist_item": None, "warnings": [], "errors": []}
    # The video exists now. A failed follow-up step must not hide its ID, or a
    # retry of the whole command would upload a duplicate.
    try:
        if spec.playlist:
            item = youtube.playlistItems().insert(part="snippet", body={"snippet": {
                "playlistId": spec.playlist,
                "resourceId": {"kind": "youtube#video", "videoId": video["id"]}}}).execute()
            out["playlist_item"] = item["id"]
    except HttpError as e:
        out["errors"].append(f"playlist: {describe(e)}")
    if spec.thumbnail:  # last: it waits for processing
        t = set_thumbnail(youtube, video["id"], spec.thumbnail, sleep, say)
        out.update(thumbnail_set=t["thumbnail_set"], thumbnail_url=t["thumbnail_url"])
        out["warnings"] += t["warnings"]
        out["errors"] += t["errors"]
    return out
