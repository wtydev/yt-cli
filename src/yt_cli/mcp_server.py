"""MCP server (stdio) for agents: the same data as `yt <command> --json`.

Read-only on purpose. It can pull from Google's APIs into the local database
(sync, comments) and read that database, but it never writes to YouTube:
no uploads, no Reporting API job creation, no deletes. Those stay in the CLI,
behind --yes, where a person runs them.

Run with `yt-mcp` (install with the `mcp` extra).
"""

from __future__ import annotations

import json
import shlex
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from . import cli, db

INSTRUCTIONS = """\
Analytics, comments and upload planning for the user's own YouTube channels, from a local
SQLite database that yt fills from Google's APIs.

Typical flow: yt_channels -> yt_sync(channel) -> yt_scorecard(channel) -> yt_video(...).
yt_query runs read-only SQL for anything the reports don't cover (see yt_schema).

Caveats: Analytics data lags 1-2 days, so the newest videos show only the public view
counter. Days are Pacific time. Impressions/CTR stay empty until the first Reporting API
file arrives (~48h after `yt sync <channel> --yes` is run once by the user).

This server never writes to YouTube. To upload, call yt_upload_plan, show the plan to
the user, and let them run the printed `yt upload ... --yes` command themselves.
"""

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# Pulls from Google into the local database: no YouTube writes, but it reaches out.
PULL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)

server = MCPServer("yt", instructions=INSTRUCTIONS)


def run(*argv: str) -> Any:
    """Run a yt command with --json and return its data (JSON-safe)."""
    args = cli.parser().parse_args([*argv, "--json"])
    try:
        data, _ = args.fn(args)
    except SystemExit as e:
        raise ToolError(str(e.code)) from None
    return json.loads(json.dumps(data, default=str))


def _flag(name: str, on: bool) -> list[str]:
    return [name] if on else []


@server.tool(annotations=READ)
def yt_channels() -> list[dict]:
    """List the channels authorized with `yt auth` (local name, title, channel ID)."""
    return run("channels")


@server.tool(annotations=PULL)
def yt_sync(channel: str, video_ids: list[str] | None = None) -> dict:
    """Pull analytics, retention, traffic, audience and reach reports for a channel into
    the local database. Safe to repeat; re-pulls the last 7 days. Can take a minute.
    Never creates Reporting API jobs: if `jobs_missing` is non-empty, tell the user to run
    `yt sync <channel> --yes` once."""
    return run("sync", channel, *(a for v in video_ids or () for a in ("--video", v)))


@server.tool(annotations=READ)
def yt_scorecard(channel: str, content_type: Literal["shorts", "long"] | None = None) -> dict:
    """Last 4 public videos with Analytics data vs the previous 4 (impressions, CTR,
    hook_3s_pct = retention at 3 s, ret_half_pct = retention at the midpoint, average
    viewed %, subs per 1k views, browse/suggested share, AI comments), each of the last 4
    with views, the median, and >=5x outliers. Videos not in Analytics yet (1-2 day lag)
    are left out of the averages and listed under `pending`. Reads the local database;
    run yt_sync first for fresh numbers."""
    return run("scorecard", channel, *(["--type", content_type] if content_type else []))


@server.tool(annotations=READ)
def yt_video(video_id: str | None = None, best_of_channel: str | None = None) -> dict:
    """One video in detail: summary metrics, retention curve, traffic sources and search
    terms, countries, age/gender, devices, day-by-day, impressions/CTR by day. Give a
    video_id, or best_of_channel to get that channel's most-viewed video."""
    if best_of_channel:
        return run("video", "--best", best_of_channel)
    if not video_id:
        raise ToolError("Give video_id or best_of_channel.")
    return run("video", video_id)


@server.tool(annotations=PULL)
def yt_comments(channel: str, fetch: bool = True, flagged_only: bool = False, full: bool = False,
                limit: int = 30) -> dict:
    """Pull new comments (unless fetch=False) and list the latest ones. Comments that
    mention AI/slop are flagged. full=True walks every thread instead of only new ones."""
    return run("comments", channel, "--limit", str(limit), *_flag("--no-fetch", not fetch),
               *_flag("--flagged", flagged_only), *_flag("--full", full))


@server.tool(annotations=READ)
def yt_upload_plan(channel: str, file: str | None = None, meta: str | None = None, title: str | None = None,
                   description: str | None = None, tags: list[str] | None = None, category: str | None = None,
                   privacy: Literal["private", "unlisted", "public"] | None = None,
                   publish_at: str | None = None, thumbnail: str | None = None, playlist: str | None = None,
                   made_for_kids: bool | None = None, synthetic_media: bool | None = None) -> dict:
    """Validate an upload and return the plan. Nothing is sent. Paths are absolute, or
    relative to the meta JSON file. Show the plan and `command` to the user; they run it."""
    argv = ["upload", channel]
    for flag, value in (("--meta", meta), ("--file", file), ("--title", title), ("--description", description),
                        ("--tags", ",".join(tags) if tags else None), ("--category", category),
                        ("--privacy", privacy), ("--publish-at", publish_at), ("--thumbnail", thumbnail),
                        ("--playlist", playlist)):
        if value is not None:
            argv += [flag, value]
    for flag, value in (("made-for-kids", made_for_kids), ("synthetic-media", synthetic_media)):
        if value is not None:
            argv.append(f"--{'' if value else 'no-'}{flag}")
    data = run(*argv)
    data["command"] = shlex.join(["yt", *argv, "--yes"])
    return data


@server.tool(annotations=READ)
def yt_query(sql: str, limit: int = 200) -> list[dict]:
    """Run one read-only SQL statement on the local database (SQLite). See yt_schema for
    tables. Days are Pacific dates (YYYY-MM-DD); retention ratios are fractions (1.0 = 100%)."""
    return run("query", sql, "--limit", str(limit))


@server.tool(annotations=READ)
def yt_schema() -> str:
    """The database's CREATE TABLE statements, for writing yt_query SQL."""
    return db.SCHEMA.strip()


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
