"""Data retention, revocation and policy acceptance (YouTube API Developer
Policies III.A.2, III.D.2, III.E.4)."""

import json
import sqlite3
from datetime import date

import pytest
from conftest import FakeHttp, fixture, services
from google.auth.exceptions import RefreshError
from test_sync import counts, make_http

from yt_cli import auth, cli, comments, config, db, sync


def age(conn, days, tables=None):
    """Pretend rows were last fetched `days` ago."""
    for t, c in db.FETCHED.items():
        if tables is None or t in tables:
            conn.execute(f"UPDATE {t} SET {c}=?", (db.ago(days),))
    conn.commit()


def synced(conn):
    sync.run(services(make_http()), conn, "mychannel", today=date(2026, 11, 22), create_jobs=True)
    comments.sync(services(comment_http()).youtube, conn, "UCtestChannel000000000000")


def comment_http():
    return FakeHttp([("GET", "/commentThreads", lambda q, b: (200, fixture(
        "comment_threads_p2.json" if q.get("pageToken") == "C2" else "comment_threads_p1.json")))])


# ---------------------------------------------------------------- storage

def test_every_row_from_google_is_stamped(conn):
    synced(conn)
    for t, c in db.FETCHED.items():
        assert conn.execute(f"SELECT count(*) FROM {t} WHERE {c} IS NULL").fetchone()[0] == 0, t
        assert conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] > 0, t


def test_rows_older_than_30_days_are_purged(conn):
    synced(conn)
    age(conn, 29)
    assert db.purge_stale(conn) == {}
    age(conn, 31)
    gone = db.purge_stale(conn)
    assert set(gone) == set(db.FETCHED)
    assert all(n == 0 for n in counts(conn).values())
    assert conn.execute("SELECT count(*) FROM comments").fetchone()[0] == 0


def test_connect_purges(tmp_path):
    path = tmp_path / "a.db"
    conn = db.connect(path)
    db.upsert(conn, "channels", [{"name": "mychannel", "channel_id": "UC1", "fetched_at": db.ago(40)}], ["name"])
    conn.commit()
    conn.close()
    assert db.connect(path).execute("SELECT count(*) FROM channels").fetchone()[0] == 0


def test_old_database_is_migrated_and_dated_to_the_upgrade(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE channels (name TEXT PRIMARY KEY, channel_id TEXT UNIQUE NOT NULL, title TEXT,
                               uploads_playlist TEXT, added_at TEXT DEFAULT (datetime('now')));
        CREATE TABLE video_daily (video_id TEXT NOT NULL, day TEXT NOT NULL, views INTEGER,
                                  PRIMARY KEY (video_id, day));
        INSERT INTO channels (name, channel_id) VALUES ('mychannel', 'UC1');
        INSERT INTO video_daily VALUES ('v1', '2026-11-01', 5);
    """)
    old.commit()
    old.close()
    conn = db.connect(path)
    for t in ("channels", "video_daily"):
        (stamp,) = conn.execute(f"SELECT fetched_at FROM {t}").fetchone()
        assert stamp is not None and stamp >= db.ago(1)


def test_delete_channel_leaves_other_channels(conn):
    synced(conn)
    db.upsert(conn, "channels", [{"name": "other", "channel_id": "UCother"}], ["name"])
    db.upsert(conn, "videos", [{"video_id": "vOther", "channel_id": "UCother"}], ["video_id"])
    db.upsert(conn, "video_daily", [{"video_id": "vOther", "day": "2026-11-01", "views": 1}], ["video_id", "day"])
    gone = db.delete_channel(conn, "mychannel")
    assert gone["videos"] == 3 and gone["comments"] == 4 and gone["channels"] == 1
    assert [r[0] for r in conn.execute("SELECT name FROM channels")] == ["other"]
    assert [r[0] for r in conn.execute("SELECT video_id FROM video_daily")] == ["vOther"]
    assert conn.execute("SELECT count(*) FROM reach_daily").fetchone()[0] == 0


# ---------------------------------------------------------------- refresh

def test_sync_refetches_aging_history_from_the_start(conn):
    sync.run(services(make_http()), conn, "mychannel", today=date(2026, 11, 22))
    age(conn, db.FULL_REFRESH_DAYS + 1)
    http = make_http(jobs_exist=True)
    sync.run(services(http), conn, "mychannel", today=date(2026, 11, 30))
    daily = [q for _, p, q in http.calls if p.endswith("/reports") and q.get("dimensions") == "day"]
    # each video's publish day, not last stored day minus REFRESH_DAYS (11-10 and 11-04)
    assert {q["startDate"] for q in daily} == {"2026-11-10", "2026-11-01"}
    assert conn.execute("SELECT min(fetched_at) FROM video_daily").fetchone()[0] >= db.ago(1)


def test_sync_redownloads_aging_reports(conn):
    sync.run(services(make_http()), conn, "mychannel", today=date(2026, 11, 22), create_jobs=True)
    res = sync.run(services(make_http(jobs_exist=True)), conn, "mychannel", today=date(2026, 11, 22))
    assert res.reports_downloaded == 0
    age(conn, db.FULL_REFRESH_DAYS + 1, ["report_files", "reach_daily"])
    res = sync.run(services(make_http(jobs_exist=True)), conn, "mychannel", today=date(2026, 11, 22))
    assert res.reports_downloaded == 1
    assert conn.execute("SELECT min(fetched_at) FROM reach_daily").fetchone()[0] >= db.ago(1)


def test_comments_walk_everything_once_aging(conn):
    comments.sync(services(comment_http()).youtube, conn, "UCtestChannel000000000000")
    age(conn, db.FULL_REFRESH_DAYS + 1, ["comments"])
    h = comment_http()
    comments.sync(services(h).youtube, conn, "UCtestChannel000000000000")
    assert len(h.calls) == 2  # both pages, not stopping at the first known one
    assert conn.execute("SELECT min(fetched_at) FROM comments").fetchone()[0] >= db.ago(1)


# ---------------------------------------------------------------- policies

def test_policies_prompt_once(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with pytest.raises(SystemExit, match="need to agree"):
        auth.accept_policies(ask=lambda _: "n")
    assert not auth.policies_accepted()
    auth.accept_policies(ask=lambda _: "y")
    assert config.PRIVACY_URL in capsys.readouterr().err
    assert auth.policies_accepted()
    auth.accept_policies(ask=lambda _: pytest.fail("asked twice"))


def test_policies_need_agree_without_a_terminal(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit, match="--agree"):
        auth.accept_policies()
    auth.accept_policies(agree=True)
    assert json.loads(config.acceptance_path().read_text())["version"] == config.POLICY_VERSION


def test_new_policy_version_asks_again(monkeypatch):
    auth.accept_policies(agree=True)
    monkeypatch.setattr(config, "POLICY_VERSION", "2099-01-01")
    assert not auth.policies_accepted()


def test_auth_asks_before_opening_the_browser(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(auth, "login", lambda *a, **k: pytest.fail("browser opened before agreement"))
    with pytest.raises(SystemExit, match="--agree"):
        cli.main(["auth", "mychannel"])


# ---------------------------------------------------------------- revoke

def write_token(channel="mychannel"):
    path = config.token_path(channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": "at", "refresh_token": "rt", "client_id": "c", "client_secret": "s"}))
    return path


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


def test_revoke_without_yes_is_a_dry_run(monkeypatch, capsys):
    path = write_token()
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: pytest.fail("must not call Google without --yes"))
    cli.main(["revoke", "mychannel"])
    assert "Would revoke" in capsys.readouterr().out and path.exists()


def test_revoke_revokes_at_google_and_deletes_everything(monkeypatch, capsys):
    conn = db.connect()
    synced(conn)
    conn.close()
    path = write_token()
    posted = []
    monkeypatch.setattr(auth.requests, "post", lambda url, data, **k: posted.append((url, data)) or Resp(200))
    cli.main(["revoke", "mychannel", "--yes", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert posted == [(auth.REVOKE_URL, {"token": "rt"})]
    assert out["revoked_at_google"] is True and out["token_deleted"] is True
    assert not path.exists()
    conn = db.connect()
    assert all(n == 0 for n in counts(conn).values())
    assert conn.execute("SELECT count(*) FROM comments").fetchone()[0] == 0


def test_revoke_tolerates_an_already_revoked_token(monkeypatch):
    path = write_token()
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: Resp(400, '{"error": "invalid_token"}'))
    assert auth.revoke("mychannel")["revoked_at_google"] is False
    assert not path.exists()


def test_revoke_keeps_everything_when_google_fails(monkeypatch):
    path = write_token()
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: Resp(503))
    with pytest.raises(SystemExit, match="nothing deleted"):
        auth.revoke("mychannel")
    assert path.exists()


def test_revoked_grant_is_detected_and_data_deleted(monkeypatch):
    conn = db.connect()
    synced(conn)
    conn.close()
    path = write_token()

    def refresh(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")
    monkeypatch.setattr(auth.Credentials, "refresh", refresh)
    with pytest.raises(SystemExit, match="no longer authorizes"):
        auth.load("mychannel")
    assert not path.exists()
    assert db.connect().execute("SELECT count(*) FROM videos").fetchone()[0] == 0
