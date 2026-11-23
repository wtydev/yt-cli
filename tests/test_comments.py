import pytest
from conftest import FakeHttp, fixture, services

from yt_cli import comments


@pytest.mark.parametrize("text,flag", [
    ("this is AI slop lol", True),
    ("A.I. generated garbage", True),
    ("Is the voice made with Suno?", True),
    ("sounds like text to speech", True),
    ("did chatgpt write this", True),
    ("pure slop", True),
    ("Great explanation of binary search!", False),
    ("I said hi to my aunt in Hawaii", False),
    ("the beat is fire", False),
    ("maintain the pace", False),
])
def test_ai_flag(text, flag):
    assert comments.is_ai_mention(text) is flag


def http():
    return FakeHttp([
        ("GET", "/commentThreads", lambda q, b: (200, fixture(
            "comment_threads_p2.json" if q.get("pageToken") == "C2" else "comment_threads_p1.json"))),
    ])


def test_sync_flags_and_is_incremental(conn):
    h = http()
    res = comments.sync(services(h).youtube, conn, "UCtestChannel000000000000")
    assert res.fetched == 4 and res.new == 4
    assert sorted(r["comment_id"] for r in res.new_flagged) == ["Ugw1", "Ugw3"]
    assert conn.execute("SELECT parent_id FROM comments WHERE comment_id='Ugw1.r1'").fetchone()[0] == "Ugw1"

    h2 = http()
    res2 = comments.sync(services(h2).youtube, conn, "UCtestChannel000000000000")
    assert res2.new == 0
    assert len(h2.calls) == 1  # first page all known -> stop


def test_pin_is_reported_unsupported(conn):
    comments.sync(services(http()).youtube, conn, "UCtestChannel000000000000")
    msg = comments.pin_unsupported_message(conn, "Ugw2")
    assert "no endpoint" in msg and "studio.youtube.com/video/vidShort001" in msg
