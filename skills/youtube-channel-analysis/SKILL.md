---
name: youtube-channel-analysis
description: Analyze the performance of the user's own YouTube channel or a single video with the yt CLI or its MCP server — views, retention, traffic sources, search terms, subscribers, impressions and CTR. Use when the user asks how their channel or videos are doing, what is working, why a video under- or over-performed, or what to change next.
---

# YouTube channel analysis with yt

`yt` keeps a local SQLite history of the user's own channels, filled from Google's
YouTube Data, Analytics and Reporting APIs. Use either interface; they return the same data:

| Step | CLI (add `--json` to parse) | MCP tool |
|---|---|---|
| Which channels exist | `yt channels` | `yt_channels` |
| Refresh data | `yt sync <channel>` | `yt_sync` |
| Channel overview | `yt scorecard <channel> [--type shorts\|long]` | `yt_scorecard` |
| One video | `yt video <id>` / `yt video --best <channel>` | `yt_video` |
| Anything else | `yt query "<SELECT ...>"` / `yt query --schema` | `yt_query` / `yt_schema` |

## Procedure

1. **List channels**, and use the local name (e.g. `mychannel`) in later calls. If none
   exist, the user must run `yt auth <name>` in a terminal themselves (browser sign-in).
2. **Sync** before analyzing unless the user just did. It is idempotent and re-pulls
   the last 7 days. Report its `warnings` only if they affect the answer. If
   `jobs_missing` is non-empty, tell the user impressions/CTR need a one-time
   `yt sync <channel> --yes` — do not run `--yes` yourself.
3. **Scorecard** for the overview: the last 4 public videos with Analytics data vs the
   previous 4, the median, and outliers (≥5× median views). Videos still waiting for
   Analytics are listed under `pending` and left out of the averages.
4. **Drill into videos** that stand out (best, worst, outliers, the newest with data)
   with `yt video`. Look at the retention curve, traffic sources and search terms.
5. **Answer specific questions with SQL** (`yt query`). Recipes in
   [references/queries.md](references/queries.md).
6. **Report**: lead with 3–6 findings, each tied to numbers, then concrete next
   experiments. Say what data is missing and when it will arrive.

## Read the numbers correctly

- **Lag.** Analytics data trails by 1–2 days. Videos from the last ~2 days have only the
  public `view_count`. The scorecard leaves them out of its windows (see `pending`); do the
  same in your own SQL, or flag them.
- **Pacific days.** Every `day` column is a Pacific-time date, as YouTube reports it.
- **Retention.** `audience_watch_ratio` is a fraction of starting viewers (1.0 = 100%);
  values above 1.0 at the start mean rewatches/loops (common on Shorts).
  `relative_retention` compares with similar videos on YouTube (0.5 = typical).
- **Two retention measures.** `hook_3s_pct` is the share still watching at 3 s (did the
  opening hold?); `ret_half_pct` is the share at the midpoint. Both compare across
  lengths. For other points, use the same *fraction* of the video (`elapsed_ratio`), and
  group videos of similar length: a longer video naturally loses more people by its middle.
- **Traffic.** `SHORTS` = the Shorts feed; `YT_SEARCH` = YouTube search (details hold
  the search terms); `RELATED_VIDEO` = suggested; `SUBSCRIBER` = browse/home;
  `YT_CHANNEL` = channel page; `YT_OTHER_PAGE` = other YouTube pages.
- **Impressions/CTR** come only from the Reporting API's daily files, which start about
  48 h after the job is created. Empty `impressions` means "not yet", not zero.
- **Small numbers.** With a few hundred views, differences of a few points are noise.
  Say so rather than over-claim; prefer patterns that repeat across several videos.
- **Privacy.** Only public videos count in the scorecard. Private test uploads appear in
  `videos` but should be ignored in analysis.

## Good findings look like

(Made-up numbers, for shape only.)

- "Videos under 45 s average 1.8× the views of longer ones (5 vs 4 videos)."
- "Retention halves between 10% and 30% of the video: the hook works, the middle loses people."
- "One video gets 40% of its views from search ('<search term>'); the rest get under 5%.
  Search keeps bringing views after the feed moves on."
- "About 1 subscriber per 1,000 views and almost no comments: try an explicit ask."

Keep channel names, IDs and data between the user, this tool and Google's APIs: don't
send them to other services.
