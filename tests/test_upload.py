import json

import httplib2
import pytest
from googleapiclient.errors import HttpError

from yt_cli import upload


@pytest.fixture
def media(tmp_path):
    (tmp_path / "v.mp4").write_bytes(b"\0" * 1024)
    (tmp_path / "t.png").write_bytes(b"\x89PNG")
    return tmp_path


def test_build_from_json_with_flag_override(media):
    meta = media / "meta.json"
    meta.write_text(json.dumps({"file": "v.mp4", "title": "From JSON", "tags": "a, b", "category": "education",
                                "thumbnail": "t.png", "synthetic_media": True}))
    spec = upload.UploadSpec.build(str(meta), {"title": "Override", "privacy": None})
    assert spec.title == "Override"
    assert spec.file == str(media / "v.mp4")
    assert spec.tags == ["a", "b"] and spec.category == "27"
    b = spec.body()
    assert b["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                           "containsSyntheticMedia": True}
    assert b["snippet"]["categoryId"] == "27"


@pytest.mark.parametrize("over,err", [
    ({"title": None}, "Missing required 'title'"),
    ({"privacy": "public", "publish_at": "2026-12-01T17:00:00Z"}, "requires privacy=private"),
    ({"publish_at": "2026-12-01T17:00:00"}, "needs a timezone"),
    ({"category": "nope"}, "Unknown category"),
    ({"title": "x" * 101}, "allows 100"),
])
def test_validation(media, over, err):
    base = {"file": str(media / "v.mp4"), "title": "ok"}
    with pytest.raises(SystemExit, match=err):
        upload.UploadSpec.build(None, {**base, **over})


def test_unknown_json_key_rejected(media):
    meta = media / "m.json"
    meta.write_text(json.dumps({"file": "v.mp4", "title": "t", "privacyy": "public"}))
    with pytest.raises(SystemExit, match="privacyy"):
        upload.UploadSpec.build(str(meta), {})


class FlakyRequest:
    def __init__(self, failures):
        self.failures = list(failures)

    def next_chunk(self):
        if self.failures:
            code = self.failures.pop(0)
            raise HttpError(httplib2.Response({"status": code}), b"{}")
        return None, {"id": "newVid"}


def test_resumable_retries_5xx_then_succeeds():
    sleeps = []
    out = upload._resumable(FlakyRequest([503, 500]), sleep=sleeps.append)
    assert out == {"id": "newVid"} and len(sleeps) == 2


def test_resumable_does_not_retry_4xx():
    with pytest.raises(HttpError):
        upload._resumable(FlakyRequest([403]), sleep=lambda s: None)


def test_resumable_retries_transport_errors():
    class DnsFlaky:
        n = 0

        def next_chunk(self):
            self.n += 1
            if self.n == 1:
                raise httplib2.ServerNotFoundError("dns")
            return None, {"id": "newVid"}

    assert upload._resumable(DnsFlaky(), sleep=lambda s: None) == {"id": "newVid"}


def test_failed_thumbnail_keeps_video_id(media, monkeypatch):
    class Exec:
        def __init__(self, fn):
            self.execute = fn

    def boom():
        raise HttpError(httplib2.Response({"status": 400}), b"{}")

    class YT:
        def videos(self):
            return self

        def insert(self, **kw):
            return "req"

        def thumbnails(self):
            return type("T", (), {"set": lambda self, **kw: Exec(boom)})()

    monkeypatch.setattr(upload, "_resumable", lambda req, progress, sleep: {"id": "newVid"})
    spec = upload.UploadSpec.build(None, {"file": str(media / "v.mp4"), "title": "t",
                                          "thumbnail": str(media / "t.png")})
    out = upload.run(YT(), spec)
    assert out["video_id"] == "newVid" and not out["thumbnail_set"]
    assert out["errors"] and out["errors"][0].startswith("thumbnail: 400")
