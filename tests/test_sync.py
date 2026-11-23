from datetime import date

from conftest import FIX, FakeHttp, fixture, services

from yt_cli import sync

ANALYTICS_BY_DIMS = {
    "day,creatorContentType": "an_day_content_type.json",
    "day": "an_video_daily.json",
    "elapsedVideoTimeRatio": "an_retention.json",
    "insightTrafficSourceType": "an_traffic_type.json",
    "insightTrafficSourceDetail": "an_traffic_detail.json",
    "subscribedStatus": "an_subscribed.json",
    "country": "an_country.json",
    "ageGroup,gender": "an_age_gender.json",
    "deviceType": "an_device.json",
}


def analytics(q, body):
    if q["dimensions"] == "creatorContentType":
        return 200, fixture("an_content_type_short.json" if "vidShort001" in q["filters"]
                            else "an_content_type_long.json")
    return 200, fixture(ANALYTICS_BY_DIMS[q["dimensions"]])


def make_http(jobs_exist=False, fail_dims=()):
    state = {"jobs": [fixture("rep_job_created.json")] if jobs_exist else []}

    def jobs_list(q, body):
        return 200, {"jobs": state["jobs"]} if state["jobs"] else {}

    def jobs_create(q, body):
        state["jobs"].append(fixture("rep_job_created.json"))
        return 200, fixture("rep_job_created.json")

    return FakeHttp([
        ("GET", "/youtube/v3/channels", lambda q, b: (200, fixture("channels_mine.json"))),
        ("GET", "/youtube/v3/playlistItems", lambda q, b: (200, fixture(
            "playlist_items_p2.json" if q.get("pageToken") == "P2" else "playlist_items_p1.json"))),
        ("GET", "/youtube/v3/videos", lambda q, b: (200, fixture("videos_list.json"))),
        ("GET", "/v2/reports", lambda q, b: (500, {"error": {"code": 500, "message": "internal"}})
         if q.get("dimensions") in fail_dims else analytics(q, b)),
        ("GET", "/v1/jobs/job-reach-1/reports", lambda q, b: (200, fixture("rep_reports.json"))),
        ("GET", "/v1/jobs", jobs_list),
        ("POST", "/v1/jobs", jobs_create),
        ("GET", "/v1/media/", lambda q, b: (200, (FIX / "reach_20261120.csv").read_bytes())),
    ])


def counts(conn):
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in (
        "channels", "videos", "video_daily", "video_retention", "video_traffic", "video_breakdown",
        "channel_daily_content_type", "reporting_jobs", "report_files", "reach_daily")}


def test_full_sync_populates_every_table(conn):
    http = make_http()
    res = sync.run(services(http), conn, "test", today=date(2026, 11, 22))
    assert res.warnings == []
    assert res.videos == 3
    assert res.jobs_created == ["channel_reach_basic_a1"]
    c = counts(conn)
    assert c["videos"] == 3
    # private video with no views is skipped: 2 videos x 2 days
    assert c["video_daily"] == 4
    assert c["video_retention"] == 10
    # 4 type rows + 2 detail rows for each of YT_SEARCH, SUBSCRIBER, RELATED_VIDEO, per video
    assert c["video_traffic"] == 2 * (4 + 3 * 2)
    assert c["reach_daily"] == 2
    types = dict(conn.execute("SELECT video_id, content_type FROM videos"))
    assert types["vidShort001"] == "SHORTS" and types["vidLong0002"] == "VIDEO_ON_DEMAND"
    dur = dict(conn.execute("SELECT video_id, duration_s FROM videos"))
    assert dur["vidLong0002"] == 495


def test_resync_is_idempotent(conn):
    sync.run(services(make_http()), conn, "test", today=date(2026, 11, 22))
    first = counts(conn)
    http = make_http(jobs_exist=True)
    res = sync.run(services(http), conn, "test", today=date(2026, 11, 22))
    assert counts(conn) == first
    assert res.jobs_created == []
    assert res.reports_downloaded == 0  # already have rep-1
    assert not any(m == "POST" for m, _, _ in http.calls)


def test_incremental_daily_window(conn):
    sync.run(services(make_http()), conn, "test", today=date(2026, 11, 22))
    http = make_http(jobs_exist=True)
    sync.run(services(http), conn, "test", today=date(2026, 11, 30))
    daily = [q for _, p, q in http.calls if p.endswith("/reports") and q.get("dimensions") == "day"]
    # last stored day 2026-11-11 minus REFRESH_DAYS, but never before publish day
    assert {q["startDate"] for q in daily} == {"2026-11-10", "2026-11-04"}
    assert all(q["endDate"] == "2026-11-30" for q in daily)


def test_reach_aggregation_and_newer_report_wins(conn):
    text = (FIX / "reach_20261120.csv").read_text()
    sync.ingest_reach_csv(conn, "UC", text, "2026-11-22T10:00:00Z")
    imp, clicks = conn.execute(
        "SELECT impressions, clicks FROM reach_daily WHERE video_id='vidShort001'").fetchone()
    assert imp == 4000 and abs(clicks - (1000 * 0.05 + 3000 * 0.03)) < 1e-9

    # an older re-issue never overwrites
    sync.ingest_reach_csv(conn, "UC", text.replace(",1000,", ",1,"), "2026-11-21T10:00:00Z")
    assert conn.execute("SELECT impressions FROM reach_daily WHERE video_id='vidShort001'").fetchone()[0] == 4000
    # a newer one replaces the whole day
    sync.ingest_reach_csv(conn, "UC", text.replace(",1000,", ",1,"), "2026-11-23T10:00:00Z")
    assert conn.execute("SELECT impressions FROM reach_daily WHERE video_id='vidShort001'").fetchone()[0] == 3001


def test_iso_duration():
    assert sync.iso_duration_seconds("PT48S") == 48
    assert sync.iso_duration_seconds("PT1H2M3S") == 3723
    assert sync.iso_duration_seconds("P1DT1S") == 86401
    assert sync.iso_duration_seconds("P0D") == 0


def test_published_day_is_pacific():
    # 03:00 UTC on Nov 11 is still Nov 10 in Los Angeles
    assert sync.published_day("2026-11-11T03:00:00Z") == date(2026, 11, 10)


def test_channel_content_types_normalized(conn):
    sync.run(services(make_http()), conn, "test", today=date(2026, 11, 22))
    assert {r[0] for r in conn.execute("SELECT content_type FROM channel_daily_content_type")} == {
        "SHORTS", "VIDEO_ON_DEMAND"}


def test_server_error_breakdown_is_skipped_after_first_failure(conn):
    http = make_http(fail_dims=("subscribedStatus",))
    res = sync.run(services(http), conn, "test", today=date(2026, 11, 22))
    sub_calls = [q for _, _, q in http.calls if q.get("dimensions") == "subscribedStatus"]
    assert len(sub_calls) == 1 and res.skipped == ["subscribedStatus"]
    assert len(res.warnings) == 1
    # the other breakdowns still ran for both videos
    assert conn.execute("SELECT count(DISTINCT video_id) FROM video_breakdown WHERE dimension='country'"
                        ).fetchone()[0] == 2
