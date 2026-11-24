import json
import struct

import httplib2
import pytest
from googleapiclient.errors import HttpError

from yt_cli import upload


def png(w, h):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x08\x02\0\0\0"


def jpeg(w, h):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0\x01\x01\0\0\x01\0\x01\0\0"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, h, w, 3) + b"\0" * 9
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


@pytest.fixture
def media(tmp_path):
    (tmp_path / "v.mp4").write_bytes(b"\0" * 1024)
    (tmp_path / "t.png").write_bytes(png(1280, 720))
    (tmp_path / "short.png").write_bytes(png(1080, 1920))
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


class Exec:
    def __init__(self, fn):
        self.execute = fn


class FakeYT:
    """videos.insert/list and thumbnails.set; processing finishes after `polls_until_done` lists."""

    def __init__(self, polls_until_done=2, dims=(1080, 1920), duration="PT32S", set_resp=None,
                 set_error=None, final="succeeded"):
        self.polls_until_done, self.dims, self.duration = polls_until_done, dims, duration
        self.set_resp = set_resp if set_resp is not None else {"items": [{"maxres": {"url": "https://i.ytimg.com/x"}}]}
        self.set_error, self.final = set_error, final
        self.events = []

    def videos(self):
        return self

    def insert(self, **kw):
        return "req"

    def list(self, **kw):
        def ex():
            self.events.append("list")
            done = self.events.count("list") >= self.polls_until_done
            return {"items": [{
                "status": {"uploadStatus": "processed" if done else "uploaded"},
                "processingDetails": {"processingStatus": self.final if done else "processing"},
                "fileDetails": {"videoStreams": [{"widthPixels": self.dims[0], "heightPixels": self.dims[1]}]},
                "contentDetails": {"duration": self.duration}}]}
        return Exec(ex)

    def thumbnails(self):
        yt = self

        class T:
            def set(self, **kw):
                def ex():
                    yt.events.append("set")
                    if yt.set_error:
                        raise HttpError(httplib2.Response({"status": yt.set_error}), b"{}")
                    return yt.set_resp
                return Exec(ex)
        return T()


def run_upload(media, monkeypatch, yt, thumb="t.png"):
    monkeypatch.setattr(upload, "_resumable", lambda req, progress, sleep: {"id": "newVid"})
    spec = upload.UploadSpec.build(None, {"file": str(media / "v.mp4"), "title": "t",
                                          "thumbnail": str(media / thumb)})
    return upload.run(yt, spec, sleep=lambda s: None)


def test_failed_thumbnail_keeps_video_id(media, monkeypatch):
    out = run_upload(media, monkeypatch, FakeYT(set_error=400))
    assert out["video_id"] == "newVid" and not out["thumbnail_set"]
    assert out["errors"] and out["errors"][0].startswith("thumbnail: 400")


def test_thumbnail_waits_for_processing(media, monkeypatch):
    yt = FakeYT(polls_until_done=3)
    out = run_upload(media, monkeypatch, yt, "short.png")
    assert yt.events == ["list", "list", "list", "set"]  # never set while still processing
    assert out["thumbnail_set"] and out["thumbnail_url"] == "https://i.ytimg.com/x" and out["errors"] == []
    assert any("Shorts feed" in w for w in out["warnings"])


def test_thumbnail_not_set_when_processing_never_finishes(media, monkeypatch):
    yt = FakeYT(polls_until_done=10 ** 6)
    monkeypatch.setattr(upload, "PROCESS_TIMEOUT_S", 60)
    out = run_upload(media, monkeypatch, yt)
    assert "set" not in yt.events and not out["thumbnail_set"]
    assert "still processing" in out["errors"][0] and "yt thumbnail" in out["errors"][0]


def test_thumbnail_not_set_when_processing_fails(media, monkeypatch):
    yt = FakeYT(polls_until_done=1, final="failed")
    out = run_upload(media, monkeypatch, yt)
    assert "set" not in yt.events and not out["thumbnail_set"] and "did not process" in out["errors"][0]


def test_empty_set_response_is_not_success(media, monkeypatch):
    out = run_upload(media, monkeypatch, FakeYT(polls_until_done=1, set_resp={}))
    assert not out["thumbnail_set"] and "not applied" in out["errors"][0]


def test_forbidden_explains_verification(media, monkeypatch):
    out = run_upload(media, monkeypatch, FakeYT(polls_until_done=1, set_error=403))
    assert not out["thumbnail_set"] and "youtube.com/verify" in out["errors"][0]


def test_orientation_mismatch_warns(media, monkeypatch):
    out = run_upload(media, monkeypatch, FakeYT(polls_until_done=1, dims=(1080, 1920)), "t.png")
    assert out["thumbnail_set"] and any("9:16" in w for w in out["warnings"])


@pytest.mark.parametrize("data,err", [
    (b"GIF89a" + b"\0" * 20, "PNG or JPEG"),
    (b"\x89PNG", "PNG or JPEG"),
    (png(320, 180), "at least 640"),
])
def test_bad_thumbnail_rejected_before_upload(tmp_path, data, err):
    (tmp_path / "v.mp4").write_bytes(b"\0")
    (tmp_path / "t.img").write_bytes(data)
    with pytest.raises(SystemExit, match=err):
        upload.UploadSpec.build(None, {"file": str(tmp_path / "v.mp4"), "title": "t",
                                       "thumbnail": str(tmp_path / "t.img")})


def test_image_info_reads_png_and_jpeg():
    assert upload.image_info(png(1080, 1920)) == ("png", 1080, 1920)
    assert upload.image_info(jpeg(1280, 720)) == ("jpeg", 1280, 720)


def test_thumbnail_command_is_a_dry_run_without_yes(media, capsys, monkeypatch):
    from yt_cli import cli
    monkeypatch.setattr(cli, "_services", lambda ch: pytest.fail("must not touch the API without --yes"))
    cli.main(["thumbnail", "mychannel", "vid00000001", str(media / "short.png")])
    out = capsys.readouterr().out
    assert "Would set the thumbnail" in out and "png 1080x1920" in out
