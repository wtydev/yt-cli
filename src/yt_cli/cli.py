"""yt: analytics, uploads and comments for your own YouTube channels."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from . import db, output
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

    creds = auth.login(args.channel, monetary=args.monetary)
    from googleapiclient.discovery import build

    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    ch = sync.fetch_channel(yt)
    conn = db.connect()
    clash = conn.execute("SELECT name FROM channels WHERE channel_id=? AND name!=?",
                         (ch["channel_id"], args.channel)).fetchone()
    if clash:
        print(f"warning: this is the same channel as '{clash[0]}'. Did you pick the right "
              "channel on Google's account chooser?", file=sys.stderr)
    db.upsert(conn, "channels", [{"name": args.channel, **ch}], ["name"])
    conn.commit()
    data = {"name": args.channel, **ch, "scopes": sorted(creds.scopes or [])}
    return data, lambda d: kv({"channel": d["name"], "title": d["title"], "channel_id": d["channel_id"],
                               "token": f"~/.config/yt-cli/tokens/{d['name']}.json"}, "Authorized")


def cmd_channels(args) -> Any:
    conn = db.connect()
    rows = [dict(r) for r in conn.execute("SELECT name, title, channel_id FROM channels ORDER BY name")]
    return rows, lambda d: table(d, title="Channels")


def cmd_sync(args) -> Any:
    from . import sync

    conn = db.connect()
    res = sync.run(_services(args.channel), conn, args.channel, video_ids=args.video or None,
                   progress=_progress(args))
    data = sync.summary(res)

    def render(d):
        lines = [kv({k: v for k, v in d.items() if k not in ("warnings", "jobs_created")}, "Synced")]
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
        out.append("\n" + table(d["traffic"], title="Traffic sources"))
        if d["traffic_detail"]:
            out.append("\n" + table(d["traffic_detail"], title="Top traffic details"))
        out.append("\n" + table(d["subscribed"], title="Subscribed status"))
        out.append("\n" + table(d["countries"], title="Top countries"))
        out.append("\n" + table(d["age_gender"], title="Age / gender (% of viewers)"))
        out.append("\n" + table(d["devices"], title="Devices"))
        out.append("\n" + table(d["daily"], title="Day by day (Pacific dates)"))
        if d["reach_daily"]:
            out.append("\n" + table(d["reach_daily"], title="Impressions and CTR by day"))
        return "\n".join(out)

    return data, render


def cmd_scorecard(args) -> Any:
    from . import report

    conn = db.connect()
    ctype = {"shorts": "SHORTS", "long": "VIDEO_ON_DEMAND", None: None}[args.type]
    data = report.scorecard(conn, db.channel_id_for(conn, args.channel), ctype)

    def render(d):
        w = report.WINDOW
        out = [f"Scorecard: {args.channel} ({d['content_type']}, {d['videos_considered']} public videos,"
               f" median views {output.fmt(d['median_views'])})"]
        out.append("\n" + table(d["trend"], title=f"Rolling average: last {w} vs previous {w}"))
        out.append("\n" + table(d["last_videos"], ["published", "title", "type", "views", "impressions", "ctr_pct",
                                                   "ret_30s_pct", "avg_viewed_pct", "subs_per_1k",
                                                   "browse_sugg_pct", "shorts_feed_pct", "ai_comments"],
                                title=f"Last {w} videos", max_width=32))
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
    progress = (lambda p: None) if args.json else (lambda p: print(f"  … {p}%", file=sys.stderr))
    result = upload.run(services.youtube, spec, progress)
    return {"dry_run": False, "plan": plan, "result": result, "notes": notes}, \
        lambda d: "\n".join([kv(d["result"], "Uploaded"), *notes])


def cmd_comments(args) -> Any:
    from . import comments

    conn = db.connect()
    cid = db.channel_id_for(conn, args.channel)
    if args.pin:  # no API for this, so nothing is ever written
        msg = comments.pin_unsupported_message(conn, args.pin)
        return {"pin": args.pin, "pinned": False, "message": msg}, lambda d: d["message"]
    res = None
    if not args.no_fetch:
        res = comments.sync(_services(args.channel).youtube, conn, cid, full=args.full)
    flagged = " AND c.flagged_ai=1" if args.flagged else ""
    rows = [dict(r) for r in conn.execute(
        "SELECT c.published_at, c.video_id, v.title AS video, c.author, c.flagged_ai AS ai, c.text, c.comment_id"
        f" FROM comments c LEFT JOIN videos v USING(video_id) WHERE c.channel_id=?{flagged}"
        " ORDER BY c.published_at DESC LIMIT ?", (cid, args.limit))]
    data = {"fetched": res.fetched if res else 0, "new": res.new if res else 0,
            "new_flagged": [r["comment_id"] for r in res.new_flagged] if res else [],
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


# ---------------------------------------------------------------- parser

def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print JSON instead of tables")

    p = argparse.ArgumentParser(prog="yt", description=__doc__)
    p.add_argument("--json", action="store_true", help="print JSON instead of tables")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("auth", parents=[common], help="sign in for a channel (browser)")
    s.add_argument("channel", help="local name for the channel, e.g. bigobars")
    s.add_argument("--monetary", action="store_true", help="also request revenue (monetary) analytics")
    s.set_defaults(fn=cmd_auth)

    s = sub.add_parser("channels", parents=[common], help="list authorized channels")
    s.set_defaults(fn=cmd_channels)

    s = sub.add_parser("sync", parents=[common], help="pull analytics and reach reports into the database")
    s.add_argument("channel")
    s.add_argument("--video", action="append", help="only sync these video IDs (repeatable)")
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
    s.add_argument("--privacy", choices=["private", "unlisted", "public"])
    s.add_argument("--publish-at", help="ISO 8601 with timezone; requires private")
    s.add_argument("--thumbnail")
    s.add_argument("--playlist", help="playlist ID to add the video to")
    s.add_argument("--made-for-kids", action=argparse.BooleanOptionalAction, default=None)
    s.add_argument("--synthetic-media", action=argparse.BooleanOptionalAction, default=None,
                   help="AI disclosure: altered or synthetic content (status.containsSyntheticMedia)")
    s.add_argument("--yes", action="store_true", help="actually upload")
    s.set_defaults(fn=cmd_upload)

    s = sub.add_parser("comments", parents=[common], help="pull comments, flag AI/slop mentions")
    s.add_argument("channel")
    s.add_argument("--full", action="store_true", help="walk all threads, not just new ones")
    s.add_argument("--flagged", action="store_true", help="list only AI/slop mentions")
    s.add_argument("--no-fetch", action="store_true", help="list from the database only")
    s.add_argument("--limit", type=int, default=30)
    s.add_argument("--pin", metavar="COMMENT_ID", help="pin a comment (the API has no pin; prints the Studio link)")
    s.set_defaults(fn=cmd_comments)
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
