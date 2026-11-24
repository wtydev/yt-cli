"""OAuth: one token file per channel, refreshed automatically."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from functools import cached_property

import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from . import config, db

REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def save(creds: Credentials, channel: str) -> None:
    path = config.token_path(channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())


POLICY_NOTICE = f"""\
Before you sign in, read and agree to:
  yt-cli Terms of Service    {config.TERMS_URL}
  yt-cli Privacy Policy      {config.PRIVACY_URL}
  YouTube Terms of Service   {config.YOUTUBE_TERMS_URL}
  Google Privacy Policy      {config.GOOGLE_PRIVACY_URL}
yt-cli uses YouTube API Services. Your data stays on this computer, is refreshed or
deleted within 30 days, and you can revoke access any time with `yt revoke <channel>`.
"""


def policies_accepted() -> bool:
    try:
        return json.loads(config.acceptance_path().read_text()).get("version") == config.POLICY_VERSION
    except (OSError, ValueError):
        return False


def accept_policies(agree: bool = False, ask=input) -> None:
    """Show the terms and privacy policy and record the user's agreement.
    Asked once per POLICY_VERSION. `agree` is the --agree flag."""
    if policies_accepted():
        return
    print(POLICY_NOTICE, file=sys.stderr)
    if not agree:
        if not sys.stdin.isatty():
            raise SystemExit("Read the policies above, then re-run with --agree, or run this in a terminal.")
        if ask("Do you agree? [y/N] ").strip().lower() not in ("y", "yes"):
            raise SystemExit("Not signed in: you need to agree to the policies to use yt.")
    path = config.acceptance_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": config.POLICY_VERSION,
                                "accepted_at": datetime.now(UTC).isoformat(timespec="seconds")}))


def login(channel: str, monetary: bool = False) -> Credentials:
    """Browser sign-in. Pick the channel (brand account) on Google's chooser.
    The caller saves the token once it has checked which channel this is."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    config.token_path(channel)  # validate the name before the browser round trip
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
    return creds


def load(channel: str) -> Credentials:
    path = config.token_path(channel)
    if not path.exists():
        raise SystemExit(f"No token for '{channel}'. Run: yt auth {channel}")
    creds = Credentials.from_authorized_user_file(str(path))
    if not creds.valid:
        if not creds.refresh_token:
            raise SystemExit(f"Token for '{channel}' expired. Run: yt auth {channel}")
        try:
            creds.refresh(Request())
        except RefreshError as e:
            if "invalid_grant" not in str(e):
                raise
            # Access was revoked (or the grant expired): stop keeping its data.
            forget(channel)
            raise SystemExit(f"Google no longer authorizes yt for '{channel}', so its token and stored "
                             f"data were deleted. To use it again: yt auth {channel}") from None
        save(creds, channel)
    return creds


def forget(channel: str) -> dict[str, int]:
    """Delete a channel's token file and everything stored for it."""
    config.token_path(channel).unlink(missing_ok=True)
    conn = db.connect()
    try:
        return db.delete_channel(conn, channel)
    finally:
        conn.close()


def revoke(channel: str) -> dict:
    """Revoke the channel's grant at Google, then forget it locally.
    Revoking the refresh token ends every token from that grant."""
    path = config.token_path(channel)
    had_token = path.exists()
    revoked = None
    if had_token:
        tok = json.loads(path.read_text())
        token = tok.get("refresh_token") or tok.get("token")
        resp = requests.post(REVOKE_URL, data={"token": token}, timeout=30,
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
        if resp.status_code == 200:
            revoked = True
        elif resp.status_code == 400 and "invalid_token" in resp.text:
            revoked = False  # already revoked or expired at Google
        else:
            raise SystemExit(f"Google did not revoke the token (HTTP {resp.status_code}); nothing deleted. "
                             "Try again, or remove yt-cli at https://myaccount.google.com/permissions.")
    deleted = forget(channel)
    return {"channel": channel, "revoked_at_google": revoked, "token_deleted": had_token, "rows_deleted": deleted}


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
