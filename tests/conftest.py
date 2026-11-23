"""Offline API fakes. Responses are canned JSON shaped exactly like the real
YouTube Data / Analytics / Reporting responses (hand-built, synthetic IDs:
no real channel data lives in this public repo)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httplib2
import pytest
from googleapiclient.discovery import build

from yt_cli import db
from yt_cli.auth import Services

FIX = Path(__file__).parent / "fixtures"


def fixture(name: str):
    return json.loads((FIX / name).read_text())


class FakeHttp:
    """Routes each request to a handler by (method, path); records every call."""

    def __init__(self, routes):
        self.routes = routes  # list of (method, path_substring, fn(query, body) -> (status, obj|bytes))
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, uri, method="GET", body=None, headers=None, **kw):
        u = urlparse(uri)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.calls.append((method, u.path, q))
        for m, path, fn in self.routes:
            if m == method and path in u.path:
                status, obj = fn(q, body)
                data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
                return httplib2.Response({"status": status, "content-type": "application/json"}), data
        raise AssertionError(f"unrouted {method} {uri}")


def services(http: FakeHttp) -> Services:
    kw = {"http": http, "static_discovery": True}
    return Services(youtube=build("youtube", "v3", **kw),
                    analytics=build("youtubeAnalytics", "v2", **kw),
                    reporting=build("youtubereporting", "v1", **kw))


@pytest.fixture
def conn():
    c = db.connect(Path(":memory:"))
    yield c
    c.close()


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_CLI_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("YT_CLI_DATA_DIR", str(tmp_path / "data"))
