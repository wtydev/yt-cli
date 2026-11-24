# SQL recipes for `yt query`

All read-only. Run `yt query --schema` for every column. Replace `'mychannel'` with the
channel's local name.

## Views, watch time and subscribers per day (channel)

```sql
SELECT d.day, sum(d.views) AS views, round(sum(d.watch_minutes)) AS watch_min,
       sum(d.subs_gained) - sum(d.subs_lost) AS net_subs
FROM video_daily d JOIN videos v USING(video_id)
JOIN channels c ON c.channel_id = v.channel_id
WHERE c.name = 'mychannel'
GROUP BY d.day ORDER BY d.day;
```

## Every public video, with length and engagement

```sql
SELECT v.title, v.published_at,  -- UTC; `yt scorecard` shows Pacific dates
       v.duration_s, v.content_type,
       v.view_count AS public_views, sum(d.views) AS analytics_views,
       round(sum(d.avg_view_pct * d.views) / nullif(sum(d.views), 0), 1) AS avg_viewed_pct,
       sum(d.likes) AS likes, sum(d.shares) AS shares, sum(d.subs_gained) AS subs
FROM videos v LEFT JOIN video_daily d USING(video_id)
JOIN channels c ON c.channel_id = v.channel_id
WHERE c.name = 'mychannel' AND v.privacy = 'public'
GROUP BY v.video_id ORDER BY v.published_at;
```

`analytics_views` is NULL for videos newer than the 1–2 day Analytics lag.

## Retention at fixed fractions of the video

```sql
SELECT v.title, v.duration_s, r.elapsed_ratio, round(r.audience_watch_ratio * 100) AS pct_watching
FROM video_retention r JOIN videos v USING(video_id)
WHERE r.elapsed_ratio IN (0.01, 0.1, 0.25, 0.5, 0.75, 1.0)
ORDER BY v.published_at, r.elapsed_ratio;
```

Retention is stored at 1% steps, `elapsed_ratio` 0.01 to 1.00 (100 points per video).

## Traffic mix per video (share of views)

```sql
SELECT v.title, t.source_type, t.views,
       round(100.0 * t.views / sum(t.views) OVER (PARTITION BY t.video_id), 1) AS pct
FROM video_traffic t JOIN videos v USING(video_id)
WHERE t.source_detail = ''
ORDER BY v.published_at, t.views DESC;
```

## Search terms that found the channel

```sql
SELECT t.source_detail AS search_term, sum(t.views) AS views, group_concat(DISTINCT v.title) AS videos
FROM video_traffic t JOIN videos v USING(video_id)
WHERE t.source_type = 'YT_SEARCH' AND t.source_detail != ''
GROUP BY t.source_detail ORDER BY views DESC;
```

## Impressions and click-through rate (after reach reports arrive)

```sql
SELECT v.title, sum(r.impressions) AS impressions,
       round(100.0 * sum(r.clicks) / nullif(sum(r.impressions), 0), 2) AS ctr_pct
FROM reach_daily r JOIN videos v USING(video_id)
GROUP BY r.video_id ORDER BY impressions DESC;
```

## Devices and countries (lifetime)

```sql
SELECT v.title, b.dimension, b.value, b.views
FROM video_breakdown b JOIN videos v USING(video_id)
WHERE b.dimension IN ('deviceType', 'country')
ORDER BY v.title, b.dimension, b.views DESC;
```

Small countries are often withheld by YouTube for privacy, so totals can be lower than
the video's views.
