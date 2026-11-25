import asyncio
import json

import pytest

pytest.importorskip("mcp")

from mcp.server.mcpserver.exceptions import ToolError

from yt_cli import cli, db, mcp_server
from yt_cli.db import upsert


@pytest.fixture
def seeded():
    conn = db.connect()
    upsert(conn, "channels", [{"name": "b", "channel_id": "UC1", "title": "Big"}], ["name"])
    upsert(conn, "videos", [{"video_id": "v1", "channel_id": "UC1", "title": "T", "privacy": "public",
                             "published_at": "2026-11-01T00:00:00Z", "view_count": 5, "duration_s": 40}],
           ["video_id"])
    conn.commit()
    conn.close()


def test_tools_return_the_cli_json(seeded):
    assert mcp_server.yt_channels()[0]["channel_id"] == "UC1"
    assert mcp_server.yt_scorecard("b")["videos_considered"] == 1
    assert mcp_server.yt_video(best_of_channel="b")["summary"]["video_id"] == "v1"
    assert mcp_server.yt_comments("b", fetch=False)["comments"] == []


def test_errors_become_tool_errors(seeded):
    with pytest.raises(ToolError, match="Unknown channel"):
        mcp_server.yt_scorecard("nope")
    with pytest.raises(ToolError, match="Give video_id"):
        mcp_server.yt_video()


def test_query_is_read_only(seeded):
    assert mcp_server.yt_query("SELECT video_id, view_count FROM videos") == [{"video_id": "v1", "view_count": 5}]
    with pytest.raises(ToolError, match="readonly"):
        mcp_server.yt_query("DELETE FROM videos")
    assert mcp_server.yt_query("SELECT count(*) AS n FROM videos")[0]["n"] == 1
    assert "CREATE TABLE IF NOT EXISTS videos" in mcp_server.yt_schema()


def test_upload_plan_never_sends(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_services", lambda ch: pytest.fail("the MCP server must not touch the API"))
    (tmp_path / "my video.mp4").write_bytes(b"\0" * 10)
    d = mcp_server.yt_upload_plan("b", file=str(tmp_path / "my video.mp4"), title="T", tags=["a", "b"],
                                  synthetic_media=True)
    assert d["dry_run"] is True and d["plan"]["title"] == "T"
    assert d["command"].endswith("--synthetic-media --yes") and "'" in d["command"]  # path with a space is quoted


def test_sync_never_creates_jobs(monkeypatch):
    seen = {}

    def fake_run(services, conn, name, **kw):
        seen.update(kw)
        raise SystemExit("stop")

    monkeypatch.setattr(cli, "_services", lambda ch: object())
    monkeypatch.setattr("yt_cli.sync.run", fake_run)
    with pytest.raises(ToolError):
        mcp_server.yt_sync("b", video_ids=["v1", "v2"])
    assert seen["create_jobs"] is False and seen["video_ids"] == ["v1", "v2"]


def test_no_write_tools_registered():
    tools = asyncio.run(mcp_server.server.list_tools())
    names = {t.name for t in tools}
    assert names == {"yt_channels", "yt_rename", "yt_sync", "yt_scorecard", "yt_video", "yt_comments",
                     "yt_upload_plan", "yt_query", "yt_schema"}
    assert all(t.annotations.destructive_hint is False for t in tools)


def test_rename_tool(seeded):
    assert mcp_server.yt_rename("b", "c")["database_updated"] is True
    assert mcp_server.yt_channels()[0]["name"] == "c"
    with pytest.raises(ToolError, match="Nothing stored"):
        mcp_server.yt_rename("b", "d")


def test_call_over_protocol_layer(seeded):
    res = asyncio.run(mcp_server.server.call_tool("yt_channels", {}))
    text = res.content[0].text
    assert "UC1" in text and json.loads(text)
