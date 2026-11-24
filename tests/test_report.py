import pytest

from yt_cli import report
from yt_cli.db import upsert


def seed(conn, n=9, outlier_views=10_000):
    for i in range(n):
        vid = f"v{i}"
        views = outlier_views if i == 0 else 100 + i
        upsert(conn, "videos", [{"video_id": vid, "channel_id": "UC", "title": f"Video {i}",
                                 "published_at": f"2026-10-{20 - i:02d}T00:00:00Z", "duration_s": 60,
                                 "content_type": "SHORTS", "privacy": "public", "view_count": views}],
               ["video_id"])
        upsert(conn, "video_daily", [{"video_id": vid, "day": "2026-10-21", "views": views,
                                      "avg_view_pct": 50.0 + i, "subs_gained": 2, "subs_lost": 1}],
               ["video_id", "day"])
        upsert(conn, "reach_daily", [{"channel_id": "UC", "day": "2026-10-21", "video_id": vid,
                                      "impressions": 1000, "clicks": 40.0 + i}],
               ["channel_id", "day", "video_id"])
        upsert(conn, "video_retention", [
            {"video_id": vid, "elapsed_ratio": 0.0, "audience_watch_ratio": 1.0},
            {"video_id": vid, "elapsed_ratio": 1.0, "audience_watch_ratio": 0.5}], ["video_id", "elapsed_ratio"])
        upsert(conn, "video_traffic", [
            {"video_id": vid, "source_type": "SUBSCRIBER", "source_detail": "", "views": 10},
            {"video_id": vid, "source_type": "RELATED_VIDEO", "source_detail": "", "views": 30},
            {"video_id": vid, "source_type": "SHORTS", "source_detail": "", "views": 60}],
            ["video_id", "source_type", "source_detail"])
    upsert(conn, "comments", [{"comment_id": "c1", "channel_id": "UC", "video_id": "v1", "text": "ai slop",
                               "flagged_ai": 1}], ["comment_id"])
    # a private video must be ignored
    upsert(conn, "videos", [{"video_id": "priv", "channel_id": "UC", "privacy": "private",
                             "published_at": "2026-12-01T00:00:00Z", "view_count": 0}], ["video_id"])


def test_retention_interpolation():
    pts = [(0.0, 1.0), (0.5, 0.8), (1.0, 0.4)]
    assert report.retention_at(pts, 0.25) == pytest.approx(0.9)
    assert report.retention_at(pts, 0.75) == pytest.approx(0.6)
    assert report.retention_at(pts, 1.5) is None
    assert report.retention_at([], 0.5) is None


def test_video_metrics(conn):
    seed(conn)
    m = report.video_metrics(conn, "v1")
    assert m["ctr_pct"] == pytest.approx(4.1)
    assert m["hook_3s_pct"] == pytest.approx(97.5)  # 3s of 60s = 5% of the way from 1.0 to 0.5
    assert m["ret_half_pct"] == pytest.approx(75.0)
    assert m["subs_per_1k"] == pytest.approx(1 / 101 * 1000)
    assert m["browse_sugg_pct"] == pytest.approx(40.0)
    assert m["shorts_feed_pct"] == pytest.approx(60.0)
    assert m["ai_comments"] == 1


def test_retention_measures_scale_with_length(conn):
    seed(conn)
    conn.execute("UPDATE videos SET duration_s=12 WHERE video_id='v1'")
    m = report.video_metrics(conn, "v1")
    assert m["hook_3s_pct"] == pytest.approx(87.5)  # 3s is 25% of a 12s video
    assert m["ret_half_pct"] == pytest.approx(75.0)  # the middle does not depend on length
    conn.execute("UPDATE videos SET duration_s=2 WHERE video_id='v1'")
    assert report.video_metrics(conn, "v1")["hook_3s_pct"] == pytest.approx(50.0)  # capped at the end
    conn.execute("DELETE FROM video_retention WHERE video_id='v1'")
    m = report.video_metrics(conn, "v1")
    assert m["hook_3s_pct"] is None and m["ret_half_pct"] is None


def test_scorecard_windows_trend_and_outliers(conn):
    seed(conn)
    sc = report.scorecard(conn, "UC")
    assert sc["videos_considered"] == 9
    assert [v["video_id"] for v in sc["last_videos"]] == ["v0", "v1", "v2", "v3"]
    assert sc["pending"] == []
    ctr = next(t for t in sc["trend"] if t["metric"] == "ctr_pct")
    assert ctr["last_4"] == pytest.approx(4.15)  # clicks 40..43 per 1000
    assert ctr["prev_4"] == pytest.approx(4.55)  # 44..47
    assert ctr["change_pct"] == pytest.approx((4.15 - 4.55) / 4.55 * 100)
    assert [o["video_id"] for o in sc["outliers"]] == ["v0"]
    assert sc["ai_comments_last"] == 1 and sc["ai_comments_prev"] == 0


def test_scorecard_skips_videos_not_in_analytics_yet(conn):
    seed(conn)
    upsert(conn, "videos", [{"video_id": "new", "channel_id": "UC", "title": "Brand new", "privacy": "public",
                             "published_at": "2026-10-22T00:00:00Z", "duration_s": 60, "view_count": 30}],
           ["video_id"])
    sc = report.scorecard(conn, "UC")
    assert sc["videos_considered"] == 10
    assert [v["video_id"] for v in sc["last_videos"]] == ["v0", "v1", "v2", "v3"]
    assert sc["pending"] == [{"video_id": "new", "title": "Brand new", "published": "2026-10-21", "views": 30}]


def test_scorecard_type_filter(conn):
    seed(conn)
    assert report.scorecard(conn, "UC", "VIDEO_ON_DEMAND")["videos_considered"] == 0
