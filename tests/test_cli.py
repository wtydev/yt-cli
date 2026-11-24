import json

import pytest

from yt_cli import cli, db
from yt_cli.db import upsert


def test_upload_without_yes_is_a_dry_run(tmp_path, capsys, monkeypatch):
    (tmp_path / "v.mp4").write_bytes(b"\0" * 10)
    monkeypatch.setattr(cli, "_services", lambda ch: pytest.fail("must not touch the API without --yes"))
    cli.main(["upload", "mychannel", "--file", str(tmp_path / "v.mp4"), "--title", "T", "--privacy", "public"])
    out = capsys.readouterr().out
    assert "Would upload" in out and "Nothing sent" in out and "locked to private" in out


def test_json_flag_before_or_after_subcommand(capsys):
    conn = db.connect()
    upsert(conn, "channels", [{"name": "mychannel", "channel_id": "UC1", "title": "Big"}], ["name"])
    conn.commit()
    for argv in (["--json", "channels"], ["channels", "--json"]):
        cli.main(argv)
        assert json.loads(capsys.readouterr().out)[0]["channel_id"] == "UC1"


def test_scorecard_table_and_json(capsys):
    conn = db.connect()
    upsert(conn, "channels", [{"name": "b", "channel_id": "UC1"}], ["name"])
    upsert(conn, "videos", [{"video_id": "v", "channel_id": "UC1", "title": "T", "privacy": "public",
                             "published_at": "2026-11-01T00:00:00Z", "view_count": 5, "duration_s": 40}],
           ["video_id"])
    conn.commit()
    cli.main(["scorecard", "b"])
    out = capsys.readouterr().out
    assert "Rolling average" in out and "until the first Reporting API file" in out
    cli.main(["scorecard", "b", "--json"])
    assert json.loads(capsys.readouterr().out)["videos_considered"] == 1


def test_unknown_channel_message():
    with pytest.raises(SystemExit, match="yt auth nope"):
        cli.main(["scorecard", "nope"])


def test_channel_name_cannot_escape_tokens_dir():
    from yt_cli import config
    with pytest.raises(SystemExit, match="Invalid channel name"):
        config.token_path("../evil")
