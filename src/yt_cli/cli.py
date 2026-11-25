"""yt: analytics, uploads and comments for your own YouTube channels."""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from typing import Any

from . import config, db, output
from .output import kv, table


def _services(channel: str):
    from .auth import Services

    return Services.for_channel(channel)


def _progress(args):
    if args.json:
        return lambda msg: None
    return lambda msg: print(f"  … {msg}", file=sys.stderr)


# ---------------------------------------------------------------- commands

def cmd_auth(args) -> Any:
    from . import auth, sync

    config.token_path(args.channel)  # validate the name before asking anything
    auth.accept_policies(agree=args.agree)
    creds = auth.login(args.channel, monetary=args.monetary)
    ch = sync.fetch_channel(auth.Services(credentials=creds, cache_discovery=False).youtube)
    conn = db.connect()
    clash = conn.execute("SELECT name FROM channels WHERE channel_id=? AND name!=?",
                         (ch["channel_id"], args.channel)).fetchone()
    if clash:
        raise SystemExit(f"'{ch['title']}' is already authorized as '{clash[0]}'; nothing saved. "
                         f"Use 'yt auth {clash[0]}', or pick another channel on Google's chooser.")
    auth.save(creds, args.channel)
    db.upsert(conn, "channels", [{"name": args.channel, **ch}], ["name"])
    conn.commit()
    data = {"name": args.channel, **ch, "scopes": sorted(creds.scopes or [])}
    return data, lambda d: kv({"channel": d["name"], "title": d["title"], "channel_id": d["channel_id"],
                               "token": str(config.token_path(d["name"]))}, "Authorized")


def cmd_revoke(args) -> Any:
    from . import auth

    token = config.token_path(args.channel)
    conn = db.connect()
    known = conn.execute("SELECT title, channel_id FROM channels WHERE name=?", (args.channel,)).fetchone()
    conn.close()
    if not token.exists() and not known:
        raise SystemExit(f"Nothing stored for '{args.channel}'. `yt channels` lists your channels.")
    if not args.yes:
        plan = {"channel": args.channel, "title": known["title"] if known else None,
                "revoke_at_google": token.exists(), "delete_token": str(token) if token.exists() else None,
                "delete_stored_data": bool(known)}
        return {"dry_run": True, "plan": plan}, lambda d: "\n".join([
            kv(plan, f"Would revoke '{args.channel}':"),
            "\nThis signs yt out at Google and deletes the channel's local history. Nothing on YouTube changes.",
            "Nothing done. Re-run with --yes to revoke."])
    data = {"dry_run": False, **auth.revoke(args.channel)}
    return data, lambda d: kv({"channel": d["channel"], "revoked_at_google": d["revoked_at_google"],
                               "token_deleted": d["token_deleted"],
                               "rows_deleted": sum(d["rows_deleted"].values())}, "Revoked")


def cmd_rename(args) -> Any:
    old, new = config.token_path(args.old), config.token_path(args.new)  # validates both names
    conn = db.connect()
    try:
        known = conn.execute("SELECT 1 FROM channels WHERE name=?", (args.old,)).fetchone()
        if not old.exists() and not known:
            raise SystemExit(f"Nothing stored for '{args.old}'. `yt channels` lists your channels.")
        if args.new == args.old:
            raise SystemExit("The new name is the same as the old one.")
        if new.exists() or conn.execute("SELECT 1 FROM channels WHERE name=?", (args.new,)).fetchone():
            raise SystemExit(f"'{args.new}' is already in use; pick another name.")
        moved = old.exists()
        if moved:
            old.rename(new)
        try:
            conn.execute("UPDATE channels SET name=? WHERE name=?", (args.new, args.old))
            conn.commit()
        except Exception:
            if moved:
                new.rename(old)
            raise
    finally:
        conn.close()
    data = {"old": args.old, "new": args.new, "token_moved": moved, "database_updated": bool(known)}
    return data, lambda d: kv(d, "Renamed")


def cmd_channels(args) -> Any:
    conn = db.connect()
    rows = [dict(r) for r in conn.execute("SELECT name, title, channel_id FROM channels ORDER BY name")]
    return rows, lambda d: table(d, title="Channels")


def cmd_sync(args) -> Any:
    from . import sync

    conn = db.connect()
    res = sync.run(_services(args.channel), conn, args.channel, video_ids=args.video or None,
                   progress=_progress(args), create_jobs=args.yes)
    data = asdict(res)

    def render(d):
        lines = [kv({k: v for k, v in d.items() if k not in ("warnings", "jobs_created", "jobs_missing")},
                    "Synced")]
        if d["jobs_missing"]:
            lines.append(f"\nWould create Reporting API job(s): {', '.join(d['jobs_missing'])} "
                         "(needed for impressions/CTR). Re-run with --yes to create them.")
        if d["jobs_created"]:
            lines.append(f"\nCreated Reporting API jobs: {', '.join(d['jobs_created'])}. "
                         "The first daily reports arrive within ~48 hours; run sync again then.")
        elif d["reports_downloaded"] == 0 and d["reach_rows"] == 0:
            n = conn.execute("SELECT count(*) FROM report_files rf JOIN reporting_jobs j USING(job_id)"
                             " WHERE j.channel_id=?", (d["channel_id"],)).fetchone()[0]
            if n == 0:
                lines.append("\nNo reach reports yet (impressions/CTR). They start ~48h after the job is created.")
        if d["warnings"]:
            lines.append(f"\n{len(d['warnings'])} warning(s):")
            lines += [f"  - {w}" for w in d["warnings"][:20]]
        return "\n".join(lines)

    return data, render


# (key in video_detail, table title, hide when empty)
VIDEO_SECTIONS = [
    ("traffic", "Traffic sources", False),
    ("traffic_detail", "Top traffic details", True),
    ("subscribed", "Subscribed status", False),
    ("countries", "Top countries", False),
    ("age_gender", "Age / gender (% of viewers)", False),
    ("devices", "Devices", False),
    ("daily", "Day by day (Pacific dates)", False),
    ("reach_daily", "Impressions and CTR by day", True),
]


def cmd_video(args) -> Any:
    from . import report

    conn = db.connect()
    vid = args.video_id
    if args.best:
        vid = report.best_video(conn, db.channel_id_for(conn, args.best))
        if not vid:
            raise SystemExit("No videos synced for that channel yet.")
    elif not vid:
        raise SystemExit("Give a video ID or --best <channel>.")
    data = report.video_detail(conn, vid)

    def render(d):
        s = d["summary"]
        out = [kv(s, f"Video {s['video_id']}")]
        if d["retention"]:
            pts = d["retention"][::max(1, len(d["retention"]) // 20)]
            peak = max((p["audience_watch_ratio"] or 0) for p in d["retention"]) or 1
            out.append("\n" + table([{"at": f"{p['elapsed_ratio'] * 100:.0f}%",
                                      "watching": (p["audience_watch_ratio"] or 0) * 100,
                                      "vs_similar": p["relative_retention"],
                                      "": output.bar(p["audience_watch_ratio"], peak, 30)} for p in pts],
                                    title="Retention (audience watch ratio, %)"))
        out += ["\n" + table(d[key], title=title) for key, title, optional in VIDEO_SECTIONS
                if d[key] or not optional]
        return "\n".join(out)

    return data, render


def cmd_scorecard(args) -> Any:
    from . import report

    conn = db.connect()
    ctype = {"shorts": "SHORTS", "long": "VIDEO_ON_DEMAND", None: None}[args.type]
    data = report.scorecard(conn, db.channel_id_for(conn, args.channel), ctype)

    def render(d):
        w = report.WINDOW
        out = [(f"Scorecard: {args.channel} ({d['content_type']}, {d['videos_considered']} public videos,"
                f" median views {output.fmt(d['median_views'])})")]
        out.append("\n" + table(d["trend"], title=f"Rolling average: last {w} vs previous {w}"))
        out.append("\n" + table(d["last_videos"], ["published", "title", "type", "views", "impressions", "ctr_pct",
                                                   "hook_3s_pct", "ret_half_pct", "avg_viewed_pct", "subs_per_1k",
                                                   "browse_sugg_pct", "shorts_feed_pct", "ai_comments"],
                                title=f"Last {w} videos", max_width=32))
        if d["pending"]:
            out.append("\nNot in Analytics yet (1-2 day lag), so left out of the averages: "
                       + ", ".join(f"{p['title']} ({p['published']}, {output.fmt(p['views'])} public views)"
                                   for p in d["pending"]))
        out.append("\n" + table(d["outliers"], title=f"Outliers (≥{report.OUTLIER_MULTIPLE:g}× median views)"))
        out.append(f"\nAI/slop comments: last {w} = {d['ai_comments_last']}, previous {w} = {d['ai_comments_prev']}")
        if not d["reach_days"]:
            out.append("Impressions/CTR are empty until the first Reporting API file arrives (~48h after first sync).")
        return "\n".join(out)

    return data, render


def cmd_upload(args) -> Any:
    from . import upload

    spec = upload.UploadSpec.build(args.meta, {
        "file": args.file, "title": args.title, "description": args.description, "tags": args.tags,
        "category": args.category, "privacy": args.privacy, "publish_at": args.publish_at,
        "thumbnail": args.thumbnail, "playlist": args.playlist, "made_for_kids": args.made_for_kids,
        "synthetic_media": args.synthetic_media,
    })
    plan = spec.plan()
    notes = []
    if spec.privacy != "private":
        notes.append("Note: uploads from an unaudited API project are locked to private by YouTube, "
                     "whatever privacy you request.")
    if not args.yes:
        data = {"dry_run": True, "channel": args.channel, "plan": plan, "notes": notes}
        return data, lambda d: "\n".join([kv(plan, f"Would upload to '{args.channel}':"), *notes,
                                          "\nNothing sent. Re-run with --yes to upload."])
    services = _services(args.channel)
    say = _progress(args)
    result = upload.run(services.youtube, spec, lambda pct: say(f"{pct}%"), say=say)
    return {"dry_run": False, "plan": plan, "result": result, "notes": notes}, \
        lambda d: "\n".join([kv({k: v for k, v in d["result"].items() if k not in ("errors", "warnings")},
                                "Uploaded"), *notes,
                             *(f"note: {w}" for w in d["result"]["warnings"]),
                             *(f"warning: {e} (the video was uploaded; fix this in Studio)"
                               for e in d["result"]["errors"])])


def cmd_thumbnail(args) -> Any:
    from . import upload

    fmt, w, h = upload.check_thumbnail(args.file)
    plan = {"channel": args.channel, "video_id": args.video_id, "file": args.file, "image": f"{fmt} {w}x{h}"}
    if not args.yes:
        return {"dry_run": True, "plan": plan}, lambda d: "\n".join([
            kv(plan, "Would set the thumbnail:"), "\nNothing sent. Re-run with --yes to set it."])
    result = upload.set_thumbnail(_services(args.channel).youtube, args.video_id, args.file,
                                  say=_progress(args))
    data = {"dry_run": False, "plan": plan, **result}
    return data, lambda d: "\n".join([
        kv({"video_id": args.video_id, "thumbnail_set": d["thumbnail_set"], "thumbnail_url": d["thumbnail_url"]},
           "Thumbnail"), *(f"note: {x}" for x in d["warnings"]), *(f"error: {x}" for x in d["errors"])])


def cmd_comments(args) -> Any:
    from . import comments

    conn = db.connect()
    cid = db.channel_id_for(conn, args.channel)
    if args.pin:  # no API for this, so nothing is ever written
        msg = comments.pin_unsupported_message(conn, args.pin)
        return {"pin": args.pin, "pinned": False, "message": msg}, lambda d: d["message"]
    res = (comments.CommentsResult() if args.no_fetch
           else comments.sync(_services(args.channel).youtube, conn, cid, full=args.full))
    flagged = " AND c.flagged_ai=1" if args.flagged else ""
    rows = [dict(r) for r in conn.execute(
        "SELECT c.published_at, c.video_id, v.title AS video, c.author, c.flagged_ai AS ai, c.text, c.comment_id"
        f" FROM comments c LEFT JOIN videos v USING(video_id) WHERE c.channel_id=?{flagged}"
        " ORDER BY c.published_at DESC LIMIT ?", (cid, args.limit))]
    data = {"fetched": res.fetched, "new": res.new,
            "new_flagged": [r["comment_id"] for r in res.new_flagged],
            "total_flagged": conn.execute("SELECT count(*) FROM comments WHERE channel_id=? AND flagged_ai=1",
                                          (cid,)).fetchone()[0],
            "comments": rows}

    def render(d):
        head = (f"Fetched {d['fetched']}, new {d['new']}, new AI/slop mentions {len(d['new_flagged'])},"
                f" total flagged {d['total_flagged']}")
        cols = ["published_at", "video", "author", "ai", "text", "comment_id"]
        return head + "\n\n" + table([{**r, "ai": bool(r["ai"]), "text": r["text"].replace("\n", " ")}
                                      for r in d["comments"]], cols, max_width=50)

    return data, render


def cmd_query(args) -> Any:
    if args.schema:
        return {"schema": db.SCHEMA}, lambda d: d["schema"].strip()
    if not args.sql:
        raise SystemExit("Give a SQL statement, or --schema to see the tables.")
    if config.db_path().exists():
        db.connect().close()  # apply the 30-day purge before reading
    rows = db.query_readonly(args.sql, args.limit)
    return rows, lambda d: table(d, max_width=60)


# ---------------------------------------------------------------- parser

def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print JSON instead of tables")

    p = argparse.ArgumentParser(prog="yt", description=__doc__)
    p.add_argument("--json", action="store_true", help="print JSON instead of tables")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("auth", parents=[common], help="sign in for a channel (browser)")
    s.add_argument("channel", help="local name for the channel, e.g. mychannel")
    s.add_argument("--monetary", action="store_true", help="also request revenue (monetary) analytics")
    s.add_argument("--agree", action="store_true",
                   help="agree to the yt-cli terms and privacy policy without the prompt")
    s.set_defaults(fn=cmd_auth)

    s = sub.add_parser("revoke", parents=[common],
                       help="sign out at Google and delete a channel's token and data (needs --yes)")
    s.add_argument("channel")
    s.add_argument("--yes", action="store_true", help="actually revoke and delete")
    s.set_defaults(fn=cmd_revoke)

    s = sub.add_parser("rename", parents=[common],
                       help="change a channel's local name in yt (not its YouTube title)")
    s.add_argument("old", help="current local name")
    s.add_argument("new", help="new local name")
    s.set_defaults(fn=cmd_rename)

    s = sub.add_parser("channels", parents=[common], help="list authorized channels")
    s.set_defaults(fn=cmd_channels)

    s = sub.add_parser("sync", parents=[common], help="pull analytics and reach reports into the database")
    s.add_argument("channel")
    s.add_argument("--video", action="append", help="only sync these video IDs (repeatable)")
    s.add_argument("--yes", action="store_true", help="create missing Reporting API jobs (a write)")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("video", parents=[common], help="one video in detail")
    s.add_argument("video_id", nargs="?")
    s.add_argument("--best", metavar="CHANNEL", help="show the channel's most-viewed video")
    s.set_defaults(fn=cmd_video)

    s = sub.add_parser("scorecard", parents=[common], help="rolling averages, trend, outliers")
    s.add_argument("channel")
    s.add_argument("--type", choices=["shorts", "long"], help="only Shorts or only long-form")
    s.set_defaults(fn=cmd_scorecard)

    s = sub.add_parser("upload", parents=[common], help="upload a video (needs --yes)")
    s.add_argument("channel")
    s.add_argument("--meta", help="JSON metadata file; flags override its keys")
    s.add_argument("--file")
    s.add_argument("--title")
    s.add_argument("--description")
    s.add_argument("--tags", help="comma-separated")
    s.add_argument("--category", help="ID or name, e.g. 27 or education")
    s.add_argument("--privacy", choices=config.PRIVACY)
    s.add_argument("--publish-at", help="ISO 8601 with timezone; requires private")
    s.add_argument("--thumbnail")
    s.add_argument("--playlist", help="playlist ID to add the video to")
    s.add_argument("--made-for-kids", action=argparse.BooleanOptionalAction, default=None)
    s.add_argument("--synthetic-media", action=argparse.BooleanOptionalAction, default=None,
                   help="AI disclosure: altered or synthetic content (status.containsSyntheticMedia)")
    s.add_argument("--yes", action="store_true", help="actually upload")
    s.set_defaults(fn=cmd_upload)

    s = sub.add_parser("thumbnail", parents=[common], help="set a video's thumbnail (needs --yes)")
    s.add_argument("channel")
    s.add_argument("video_id")
    s.add_argument("file", help="PNG or JPEG, up to 50 MB")
    s.add_argument("--yes", action="store_true", help="actually set it")
    s.set_defaults(fn=cmd_thumbnail)

    s = sub.add_parser("comments", parents=[common], help="pull comments, flag AI/slop mentions")
    s.add_argument("channel")
    s.add_argument("--full", action="store_true", help="walk all threads, not just new ones")
    s.add_argument("--flagged", action="store_true", help="list only AI/slop mentions")
    s.add_argument("--no-fetch", action="store_true", help="list from the database only")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--pin", metavar="COMMENT_ID", help="pin a comment (the API has no pin; prints the Studio link)")
    s.set_defaults(fn=cmd_comments)

    s = sub.add_parser("query", parents=[common], help="read-only SQL over the local database")
    s.add_argument("sql", nargs="?", help="one SELECT statement")
    s.add_argument("--schema", action="store_true", help="print the table definitions")
    s.add_argument("--limit", type=int, default=500, help="max rows (default 500)")
    s.set_defaults(fn=cmd_query)
    return p


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    data, render = args.fn(args)
    if args.json:
        output.emit_json(data)
    else:
        print(render(data))


if __name__ == "__main__":
    main()
