# yt

A command-line tool for **your own** YouTube channels: analytics history in a local SQLite
database, a scorecard, private-by-default uploads, and comment triage.

Everything runs on your machine and talks only to Google's APIs. There is no server.
Privacy policy and terms: <https://wtydev.github.io/yt-cli/>.

```
yt auth <channel>        browser sign-in, one token per channel
yt sync <channel>        Analytics API + Reporting API -> ~/.local/share/yt-cli/analytics.db
                         (add --yes once, to create the reach report job)
yt video <id>            one video: retention curve, traffic, audience, day by day
yt video --best <ch>     the same for the channel's most-viewed video
yt scorecard <channel>   last 4 vs previous 4, outliers (>=5x median), AI/slop comments
yt upload <channel> ...  resumable upload; prints the plan, sends only with --yes
yt comments <channel>    pull new comments, flag AI/slop mentions
yt channels              list authorized channels
```

Add `--json` to any command for machine-readable output.

## Setup (bring your own Google Cloud project)

yt ships no credentials. Each user creates their own OAuth client (about 10 minutes):

1. In the [Google Cloud console](https://console.cloud.google.com/), create or pick a project.
2. Enable **YouTube Data API v3**, **YouTube Analytics API** and **YouTube Reporting API**.
3. **Google Auth Platform → Branding**: set an app name and support email. Audience: **External**.
4. **Audience → Publish app** ("In production"). In *Testing* mode, refresh tokens expire after
   7 days. Unverified is fine for personal use: you click past "Google hasn't verified this app"
   once per sign-in. Don't upload a logo; that forces verification.
5. **Clients → Create client → Desktop app**. Download the JSON to
   `~/.config/yt-cli/client_secret.json` (`chmod 600`).

Then:

```sh
uv tool install git+https://github.com/wtydev/yt-cli   # or: uv run yt ... from a checkout
yt auth bigobars    # pick the right channel on Google's chooser
yt sync bigobars --yes   # --yes only the first time: creates the reach report job
```

Analytics data is only available to the channel **owner** (not a manager). For brand-account
channels, choose the channel on Google's account chooser during `yt auth`.

## Notes

- **Data lag.** Analytics data arrives 1–2 days late; each sync re-pulls the last 7 days.
  All days are Pacific time, as YouTube reports them.
- **Impressions and CTR** come only from the Reporting API (`channel_reach_basic_a1`).
  `yt sync <ch> --yes` creates the job once; YouTube produces the first daily file about 48 hours later.
- **Idempotent.** Syncing again converges to the same rows. Newer re-issued reach reports
  replace older ones for the same day.
- **Uploads** from a project that hasn't passed YouTube's API compliance audit are locked to
  private, whatever privacy you request. `--synthetic-media` sets YouTube's AI-content
  disclosure (`status.containsSyntheticMedia`).
- **Pinning comments** isn't possible: the YouTube Data API has no pin endpoint.
  `yt comments <ch> --pin <id>` prints the Studio link instead.
- **No delete commands.** Every write needs `--yes`.

### Upload metadata file

```json
{
  "file": "final.mp4",
  "title": "Binary Search (but it's a rap)",
  "description": "…",
  "tags": ["binary search", "cs"],
  "category": "education",
  "privacy": "private",
  "publish_at": "2026-12-01T17:00:00Z",
  "thumbnail": "thumb.png",
  "playlist": "PL…",
  "made_for_kids": false,
  "synthetic_media": true
}
```

`yt upload bigobars --meta upload.json` shows the plan; add `--yes` to send it. Paths are
relative to the JSON file. Flags override keys.

## Files

| Path | What |
|---|---|
| `~/.config/yt-cli/client_secret.json` | your OAuth client |
| `~/.config/yt-cli/tokens/<channel>.json` | per-channel tokens (0600) |
| `~/.local/share/yt-cli/analytics.db` | SQLite history |

Override with `YT_CLI_CONFIG_DIR` / `YT_CLI_DATA_DIR`.

## Development

```sh
uv run pytest -q
```

Tests run offline against canned responses shaped like the real API responses (synthetic IDs).
