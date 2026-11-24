"""OAuth: one token file per channel, refreshed automatically."""

from __future__ import annotations

import os
from functools import cached_property

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from . import config


def _save(creds: Credentials, channel: str) -> None:
    path = config.token_path(channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())


def login(channel: str, monetary: bool = False) -> Credentials:
    """Browser sign-in. Pick the channel (brand account) on Google's chooser."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret = config.client_secret_path()
    if not secret.exists():
        raise SystemExit(
            f"Missing {secret}. Create a Desktop OAuth client in your own Google Cloud "
            "project and save its JSON there (see README)."
        )
    scopes = config.SCOPES + ([config.MONETARY_SCOPE] if monetary else [])
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), scopes=scopes)
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent select_account",
        authorization_prompt_message="Opening your browser to sign in. If it doesn't open, visit:\n{url}\n",
        success_message="yt-cli is authorized. You can close this tab.",
    )
    _save(creds, channel)
    return creds


def load(channel: str) -> Credentials:
    path = config.token_path(channel)
    if not path.exists():
        raise SystemExit(f"No token for '{channel}'. Run: yt auth {channel}")
    creds = Credentials.from_authorized_user_file(str(path))
    if not creds.valid:
        if creds.refresh_token:
            creds.refresh(Request())
            _save(creds, channel)
        else:
            raise SystemExit(f"Token for '{channel}' expired. Run: yt auth {channel}")
    return creds


class Services:
    """The three API clients, each built on first use from the same `build()`
    keyword arguments. Tests pass a fake `http` instead of credentials."""

    def __init__(self, **build_kw):
        self._kw = build_kw

    @cached_property
    def youtube(self):
        return build("youtube", "v3", **self._kw)

    @cached_property
    def analytics(self):
        return build("youtubeAnalytics", "v2", **self._kw)

    @cached_property
    def reporting(self):
        return build("youtubereporting", "v1", **self._kw)

    @classmethod
    def for_channel(cls, channel: str) -> Services:
        return cls(credentials=load(channel), cache_discovery=False)
