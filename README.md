# yt

A command-line tool, MCP server and set of agent skills for **your own** YouTube channels:

- **Analytics history** in a local SQLite database: views, watch time, retention curves,
  traffic sources and search terms, audience, subscribers, impressions and CTR.
- **A scorecard**: your last 4 videos with Analytics data against the 4 before them, plus outliers.
- **Uploads** that show their plan first and are sent only with `--yes`.
- **Comment triage**, including comments that call a video AI-generated or "slop".
- **AI agent support**: an MCP server (`yt-mcp`) and agent-agnostic skills in [`skills/`](skills/).

Everything runs on your machine and talks only to Google's APIs. There is no yt server,
and yt ships no credentials: you use your own Google Cloud project.
Privacy policy and terms: <https://wtydev.github.io/yt-cli/>.

**Contents**

1. [Quick start](#quick-start)
2. [Setup](#setup)
3. [Everyday use](#everyday-use)
4. [Command reference](#command-reference)
5. [Reading the numbers](#reading-the-numbers)
6. [Using yt with AI agents](#using-yt-with-ai-agents): [MCP server](#mcp-server), [skills](#skills)
7. [Files and privacy](#files-and-privacy)
8. [Troubleshooting](#troubleshooting)
9. [Development](#development)

---

## Quick start

```sh
uv tool install "yt-cli[mcp] @ git+https://github.com/wtydev/yt-cli"
# one-time: put your OAuth client at ~/.config/yt-cli/client_secret.json (see Setup)
yt auth mychannel              # browser sign-in; pick the channel on Google's chooser
yt sync mychannel --yes        # first sync; --yes creates the impressions/CTR report job
yt scorecard mychannel         # how the latest videos compare
yt video --best mychannel      # the most-viewed video in detail
```

`mychannel` is a local nickname you choose. It names the token file and is how every
other command refers to the channel.

---

## Setup

### 1. Install

yt needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
# CLI and MCP server
uv tool install "yt-cli[mcp] @ git+https://github.com/wtydev/yt-cli"

# CLI only
uv tool install git+https://github.com/wtydev/yt-cli

# Or from a checkout: uv sync --all-extras, then prefix commands with `uv run`
```

This installs two commands: `yt` and `yt-mcp`. Upgrade later with `uv tool upgrade yt-cli`.

### 2. Create your Google Cloud OAuth client (about 10 minutes, once)

1. In the [Google Cloud console](https://console.cloud.google.com/), create or pick a project.
2. **APIs & Services → Library**: enable **YouTube Data API v3**, **YouTube Analytics API**
   and **YouTube Reporting API**.
3. **Google Auth Platform → Branding**: set an app name and your support email.
   **Audience**: choose **External**. Don't upload a logo: that triggers Google's
   verification review.
4. **Audience → Publish app** so its status is **In production**. In *Testing*, refresh tokens
   expire after 7 days and you would have to sign in again every week. Unverified is fine
   for personal use: you click past "Google hasn't verified this app" once per sign-in.
5. **Clients → Create client → Desktop app**. Download the JSON and save it:

   ```sh
   mkdir -p ~/.config/yt-cli
   mv ~/Downloads/client_secret_*.json ~/.config/yt-cli/client_secret.json
   chmod 600 ~/.config/yt-cli/client_secret.json
   ```

### 3. Authorize each channel

```sh
yt auth mychannel
```

The first time, yt shows the [terms](https://wtydev.github.io/yt-cli/terms.html) and
[privacy policy](https://wtydev.github.io/yt-cli/privacy.html) and asks you to agree (pass
`--agree` to skip the prompt). Then a browser opens. Sign in, and if your account has several channels (brand accounts),
**pick the channel itself** on Google's chooser, not just the Google account. yt then shows
which channel it got. If that channel is already authorized under another nickname, nothing
is saved.

- Analytics needs the channel **owner**. Managers and editors can't read it through the API.
- `yt auth mychannel --monetary` also asks for revenue analytics (only for monetized channels).
- Repeat for every channel: `yt auth secondchannel`. `yt channels` lists them.

### 4. First sync

```sh
yt sync mychannel --yes
```

This pulls the history of every video into the database. The `--yes` is needed **once per
channel**: it creates a YouTube Reporting API job (`channel_reach_basic_a1`), which is the only
source of impressions and click-through rate. YouTube produces the first daily file about
48 hours later. Until then, impressions and CTR are empty. After that, plain `yt sync mychannel`
is enough.

Without `--yes`, sync tells you which job it would create and creates nothing.

---

## Everyday use

**A quick check:**

```sh
yt sync mychannel          # a few seconds to a minute; re-pulls the last 7 days
yt scorecard mychannel
```

**After publishing a video:** analytics arrive 1–2 days late, so check a new video on day 2
or 3: `yt sync mychannel && yt video <video_id>`.

**Weekly:** `yt comments mychannel`, to read new comments and see AI/slop mentions.

**Shorts and long-form separately:** `yt scorecard mychannel --type shorts` or `--type long`.

**Ask your own questions** with SQL: `yt query "SELECT ..."` (see [`yt query`](#yt-query)).

**Script it:** add `--json` to any command and pipe it into `jq`, a notebook or an agent:

```sh
yt scorecard mychannel --json | jq '.trend[] | {metric, change_pct}'
```

---

## Command reference

Every command accepts `--json` (before or after the subcommand) for machine-readable output.

| Command | What it does | Writes to YouTube? |
|---|---|---|
| `yt auth <channel>` | Browser sign-in; saves a token for that channel | no |
| `yt channels` | List authorized channels | no |
| `yt rename <old> <new>` | Change a channel's local name in yt; synced data is kept | no |
| `yt sync <channel>` | Pull analytics and reach reports into the database | only with `--yes` (creates a report job) |
| `yt scorecard <channel>` | Last 4 vs previous 4, median, outliers | no |
| `yt video <id>` | One video in detail | no |
| `yt comments <channel>` | Pull comments; flag AI/slop mentions | no |
| `yt upload <channel> ...` | Plan an upload; send it with `--yes` | only with `--yes` |
| `yt thumbnail <channel> <video_id> <file>` | Set a video's thumbnail | only with `--yes` |
| `yt query "<sql>"` | Read-only SQL on the local database | no |
| `yt revoke <channel>` | Revoke yt's access at Google; delete the channel's token and local data | no (needs `--yes`) |

No command deletes anything on YouTube.

### `yt sync`

```
yt sync <channel> [--video ID ...] [--yes]
```

For every video on the channel it stores:

- **Video details**: title, publish time, duration, privacy, public counters, and whether it's
  a Short.
- **Daily metrics**: views, watch minutes, average view %, likes, comments, shares,
  subscribers gained and lost.
- **The retention curve** at 1% steps, including how it compares with similar videos.
- **Traffic sources**, plus their details: search terms, suggesting videos and external sites.
- **Audience**: countries, age and gender, devices.
- **Impressions and clicks** per day, from the Reporting API.

It also stores a channel-level daily split between Shorts and long-form.

| Option | Meaning |
|---|---|
| `--video ID` | Only these videos (repeatable). Faster when you care about one upload. |
| `--yes` | Create missing Reporting API jobs. Needed once per channel. |

Syncs are idempotent: running one twice gives the same rows. Each run re-pulls the last 7 days,
because YouTube revises recent numbers. If one kind of data fails (for example YouTube
returns an error for one breakdown), sync records a warning and keeps going. A server error
skips that kind of data for the rest of the run.

### `yt scorecard`

```
yt scorecard <channel> [--type shorts|long]
```

Looks at public videos only, newest first:

- **Rolling average**: the last 4 videos with Analytics data against the previous 4, with %
  change, for impressions, CTR, retention at 3 s (`hook_3s_pct`) and at the midpoint
  (`ret_half_pct`), average viewed %, subscribers per 1,000 views, the share of views from
  browse and suggested, and AI/slop comments.
- **The last 4 videos**, one row each, with views and the share from the Shorts feed too.
- **Videos not in Analytics yet** (published in the last day or two) are listed on their own
  line and left out of the averages.
- **The median views** and **outliers**: videos with at least 5× the median.

Views use Analytics data when available, and the public counter otherwise.

### `yt video`

```
yt video <video_id>
yt video --best <channel>     # the channel's most-viewed video
```

It shows these sections:

- **Summary**: the scorecard metrics for this video.
- **Retention curve**, as a text chart, with the comparison to similar videos.
- **Traffic sources**, and the top details within each: search terms, suggesting videos.
- **Subscribed vs not subscribed** viewers (when YouTube returns it).
- **Top countries**, **age/gender**, **devices**.
- **Day by day** (Pacific dates).
- **Impressions and CTR by day**, once reach reports have arrived.

### `yt comments`

```
yt comments <channel> [--flagged] [--no-fetch] [--full] [--limit N] [--pin COMMENT_ID]
```

It pulls comment threads and replies, newest first, and lists the latest `N` (default 30).
Comments that mention AI are flagged, for example "AI-generated", "slop", "ChatGPT", "Suno",
"TTS", "robotic voice" or "soulless".

| Option | Meaning |
|---|---|
| `--flagged` | List only flagged comments |
| `--no-fetch` | List from the database without calling the API |
| `--full` | Walk every thread. By default it stops at the first page where nothing is new. |
| `--limit N` | How many to list |
| `--pin ID` | The API can't pin comments, so this prints the Studio link to pin it by hand |

yt doesn't post, edit or delete comments.

### `yt upload`

```
yt upload <channel> --meta upload.json            # show the plan, send nothing
yt upload <channel> --meta upload.json --yes      # upload
yt upload <channel> --file v.mp4 --title "..." --privacy private [--yes]
```

Put the metadata in a JSON file. Paths in it are relative to the JSON file, and command-line
flags override its keys:

```json
{
  "file": "final.mp4",
  "title": "Sourdough Starter in 60 Seconds",
  "description": "…",
  "tags": ["sourdough", "baking"],
  "category": "howto",
  "privacy": "private",
  "publish_at": "2026-12-01T17:00:00Z",
  "thumbnail": "thumb.png",
  "playlist": "PL…",
  "made_for_kids": false,
  "synthetic_media": true
}
```

| Key / flag | Notes |
|---|---|
| `file` / `--file` | Required. Resumable upload; transient errors are retried with backoff. |
| `title` / `--title` | Required. Up to 100 characters. |
| `description` / `--description` | Up to 5,000 bytes. |
| `tags` / `--tags a,b,c` | A list in JSON; comma-separated on the command line. |
| `category` / `--category` | An ID (`27`) or a name: `education`, `science`, `entertainment`, `gaming`, `music`, `howto`, `comedy`, `people`, `news`, … |
| `privacy` / `--privacy` | `private` (default), `unlisted`, `public`. |
| `publish_at` / `--publish-at` | Scheduled publish time, ISO 8601 with timezone. Requires `private`. |
| `thumbnail` / `--thumbnail` | PNG or JPEG, up to 50 MB, at least 640 px on the long side. 16:9 for videos, 9:16 for Shorts. Set once YouTube has processed the video. |
| `playlist` / `--playlist` | Playlist ID to add the video to. |
| `made_for_kids` / `--[no-]made-for-kids` | YouTube's audience declaration (COPPA). |
| `synthetic_media` / `--[no-]synthetic-media` | YouTube's AI disclosure for realistic altered or synthetic content. |

Without `--yes`, the command validates everything and prints the plan. With `--yes`, it
uploads and prints the video ID and Studio link. If the thumbnail or playlist step fails,
the video is still uploaded: yt prints a warning so you can fix that part in Studio.

With a thumbnail, yt waits for YouTube to finish processing the video before setting it (up to
20 minutes), because YouTube can silently drop a thumbnail set on a Short that is still
processing. `thumbnail_set` is true only when YouTube accepted the image for a processed
video. To set or replace a thumbnail later, for any video on the channel:

```sh
yt thumbnail mychannel <video_id> cover.png         # checks the image, sends nothing
yt thumbnail mychannel <video_id> cover.png --yes   # sets it
```

Notes:

- **YouTube locks uploads to private** for API projects that haven't passed its compliance
  audit, whatever privacy you request. The plan warns you about this. Make the video public
  in Studio, or apply for the audit.
- Subscribers aren't notified about private uploads.
- **Custom thumbnails need a verified channel** (<https://www.youtube.com/verify>). For Shorts
  the feature is still rolling out, so YouTube may refuse it with a 403. A Short's custom
  thumbnail shows in search, on the channel page and in subscriptions, **never in the Shorts
  feed**, which shows a frame from the video. It can take a while to appear.
- yt can't delete videos. Delete test uploads in Studio.

### `yt query`

```
yt query "<one SQL statement>" [--limit N]
yt query --schema
```

Runs SQL on a **read-only** connection to the local database, so a statement that writes
fails. Examples:

```sh
# daily views across the channel
yt query "SELECT day, sum(views) AS views FROM video_daily GROUP BY day ORDER BY day"

# which search terms find you
yt query "SELECT source_detail AS term, sum(views) AS views FROM video_traffic
          WHERE source_type='YT_SEARCH' AND source_detail!='' GROUP BY 1 ORDER BY 2 DESC"
```

More recipes are in [skills/youtube-channel-analysis/references/queries.md](skills/youtube-channel-analysis/references/queries.md).

| Table | One row per |
|---|---|
| `channels` | Authorized channel (`name` = your nickname) |
| `videos` | Video: title, `published_at` (UTC), `duration_s`, `privacy`, `content_type` (`SHORTS` / `VIDEO_ON_DEMAND` / `LIVE_STREAM`), public counters |
| `video_daily` | Video × Pacific day: views, watch minutes, average view %, likes, comments, shares, subscribers |
| `video_retention` | Video × `elapsed_ratio` (0.01–1.00): `audience_watch_ratio`, `relative_retention` |
| `video_traffic` | Video × source type × detail (`''` = the type's total) |
| `video_breakdown` | Video × dimension (`country`, `ageGender`, `deviceType`, `subscribedStatus`) × value |
| `channel_daily_content_type` | Channel × day × Shorts/long-form |
| `reach_daily` | Video × day: impressions and clicks |
| `comments` | Comment or reply, with `flagged_ai` |

---

## Reading the numbers

- **Analytics data is 1–2 days behind.** The newest videos show only their public view counter.
  The scorecard leaves them out of its averages and lists them separately (`pending` in
  `--json`).
- **All days are Pacific time**, as YouTube reports them. Publish times in `videos` are UTC.
- **Retention** is a share of the viewers who started (1.0 = 100%). Above 100% at the start
  means people rewatched, which is common on looping Shorts. `relative_retention` ranks
  the video against similar ones (0.5 = typical).
- **Retention: hook and midpoint.** `hook_3s_pct` is the share still watching at 3 s, which
  tests the opening whatever the length. `ret_half_pct` is the share at the halfway point.
  Longer videos naturally lose more people by then, so compare it among videos of similar
  length. For other points, use the same fraction of the video (`elapsed_ratio`).
- **Traffic sources**:

  | Source | Meaning |
  |---|---|
  | `SHORTS` | the Shorts feed |
  | `YT_SEARCH` | YouTube search; the details are the search terms |
  | `RELATED_VIDEO` | suggested videos |
  | `SUBSCRIBER` | browse features and home |
  | `YT_CHANNEL` | your channel page |
  | `YT_OTHER_PAGE` | other YouTube pages |
  | `EXT_URL` | external sites |
  | `NOTIFICATION` | notifications |

- **Empty impressions mean "not yet", not zero.** They start about 48 hours after the report
  job exists, and YouTube sometimes reissues a day's file later. yt keeps the newest version.
- **Small channels have small numbers.** A difference of a few points over a few hundred
  views is noise. Look for patterns that repeat across videos.
- **Some breakdowns are withheld.** YouTube hides small country and age groups for privacy,
  and sometimes returns server errors for `subscribedStatus`. sync records a warning and
  moves on.

---

## Using yt with AI agents

Agents can use yt in three ways. All three give the same data:

1. **Through the MCP server**, the best option when the agent supports MCP.
2. **Through the CLI with `--json`**, for any agent that can run shell commands.
3. **With the [skills](#skills)**: task guides that teach the agent the workflow and how to read the numbers.

[`AGENTS.md`](AGENTS.md) sums up the rules for agents, for tools that read that file.

### MCP server

`yt-mcp` is a stdio MCP server. It's installed with the `mcp` extra (see [Install](#1-install)).
Run `yt auth` in a terminal first: the server uses the same tokens and database as the CLI.

**It never writes to YouTube.** It can pull data from Google into your local database and read
that database. It can't upload, create report jobs or delete anything. To upload, the agent
calls `yt_upload_plan`, shows you the plan, and gives you the exact `yt upload ... --yes`
command to run yourself.

| Tool | Equivalent | What it does |
|---|---|---|
| `yt_channels` | `yt channels` | List authorized channels |
| `yt_rename` | `yt rename` | Change a channel's local name; keeps its data |
| `yt_sync` | `yt sync` (never `--yes`) | Pull fresh data into the database |
| `yt_scorecard` | `yt scorecard` | Last 4 vs previous 4, outliers |
| `yt_video` | `yt video` | One video in detail, or the channel's best |
| `yt_comments` | `yt comments` | Pull and list comments, AI/slop flags |
| `yt_upload_plan` | `yt upload` without `--yes` | Check an upload and return the plan and the command to run |
| `yt_query` | `yt query` | Read-only SQL |
| `yt_schema` | `yt query --schema` | Table definitions |

Configure your client with the command `yt-mcp` and no arguments. If the client doesn't find
it on `PATH`, use the full path from `which yt-mcp` (usually `~/.local/bin/yt-mcp`).

**Claude Code**

```sh
claude mcp add --scope user yt -- yt-mcp
```

**Codex CLI** (`~/.codex/config.toml`)

```toml
[mcp_servers.yt]
command = "yt-mcp"
```

**Claude Desktop, Cursor, Windsurf, VS Code and other JSON-configured clients**

```json
{
  "mcpServers": {
    "yt": { "command": "/Users/you/.local/bin/yt-mcp" }
  }
}
```

(VS Code's `mcp.json` uses the key `"servers"` instead of `"mcpServers"`.)

**From a checkout, without installing**

```json
{ "mcpServers": { "yt": { "command": "uv", "args": ["run", "--directory", "/path/to/yt-cli", "--extra", "mcp", "yt-mcp"] } } }
```

If you keep the database or tokens somewhere else, set `YT_CLI_CONFIG_DIR` and
`YT_CLI_DATA_DIR` in the server's `env` too.

Try asking: *"How is mychannel doing this week?"*, *"Why did my last Short do worse than the
one before?"*, *"Which search terms bring people to my channel?"*, *"Any comments calling
my videos AI?"*

### Skills

[`skills/`](skills/) holds three task guides in the open **Agent Skills** format: a folder with
a `SKILL.md` that has a `name` and a `description`. They work with the CLI, the MCP server, or both.

| Skill | Teaches the agent to |
|---|---|
| [`youtube-channel-analysis`](skills/youtube-channel-analysis/SKILL.md) | Sync, read the scorecard, drill into videos, read the metrics correctly, and report findings with next steps. Includes [SQL recipes](skills/youtube-channel-analysis/references/queries.md). |
| [`youtube-upload`](skills/youtube-upload/SKILL.md) | Collect metadata, dry-run, show the plan, and upload only after you confirm. |
| [`youtube-comment-triage`](skills/youtube-comment-triage/SKILL.md) | Sort comments into questions, corrections, AI accusations, requests and spam, and draft replies. |

To install them, copy or symlink the folders into your agent's skills directory. For Claude Code:

```sh
git clone https://github.com/wtydev/yt-cli ~/src/yt-cli
mkdir -p ~/.claude/skills
ln -s ~/src/yt-cli/skills/* ~/.claude/skills/
```

Other agents that support `SKILL.md` folders load them from their own skills directory; check
that agent's docs for the path. For an agent without skills support, point it at
`AGENTS.md` or paste the relevant `SKILL.md` into its instructions: they are plain Markdown.

---

## Files and privacy

| Path | What |
|---|---|
| `~/.config/yt-cli/client_secret.json` | Your OAuth client |
| `~/.config/yt-cli/tokens/<channel>.json` | One token per channel (mode 0600, folder 0700) |
| `~/.local/share/yt-cli/analytics.db` | The SQLite history |

Override these locations with `YT_CLI_CONFIG_DIR` and `YT_CLI_DATA_DIR`.

- yt talks only to Google's APIs. Nothing is sent anywhere else, and there's no telemetry.
- The OAuth scopes are YouTube read-only, upload, comment read (`force-ssl`) and Analytics
  read-only, plus monetary analytics if you ask for them.
- **Data is kept for at most 30 days without a refresh**, as YouTube's API policies require. Each
  sync re-fetches whatever is getting old, so a channel you sync at least every two weeks keeps
  its full history. Data that isn't refreshed within 30 days is deleted the next time yt runs.
  Impressions and CTR only go back as far as the Reporting API still keeps its daily files.
- To revoke access, run `yt revoke <channel> --yes`. It revokes the token at Google and deletes
  the channel's token and data. If you remove the app at <https://myaccount.google.com/permissions>
  instead, yt deletes that channel's token and data the next time it runs for that channel. To wipe
  everything, delete `analytics.db`.
- When an agent uses yt, your channel data goes to that agent's model provider like any
  other text in the conversation. Keep that in mind when choosing an agent.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Missing …/client_secret.json` | Do [step 2 of Setup](#2-create-your-google-cloud-oauth-client-about-10-minutes-once). |
| `No token for 'x'` | `yt auth x` |
| "Google no longer authorizes yt for 'x'" | Access was revoked or the grant expired, so yt deleted that channel's token and data. Run `yt auth x`, then `yt sync x`. If it keeps happening every 7 days, the app is still in *Testing*: publish it (Setup step 4). |
| `Unknown channel 'x'` | Use the nickname from `yt channels`, or run `yt auth x`. |
| "Google hasn't verified this app" | Expected for a personal unverified app: **Advanced → Go to (app name)**. |
| Analytics `403 Forbidden` | Sign in as the channel **owner**, and pick the channel (not just the account) on the chooser. |
| `quotaExceeded` | Your project used its daily YouTube Data API quota (10,000 units by default; uploads cost the most). It resets at midnight Pacific. |
| Impressions/CTR are empty | Run `yt sync <ch> --yes` once, then wait about 48 hours. |
| The newest video has no analytics | Normal: data arrives 1–2 days late. |
| A `subscribedStatus` warning | YouTube returns server errors for this breakdown on some channels. Everything else syncs. |
| Upload stays private | Your API project is unaudited; see [`yt upload`](#yt-upload). |
| An MCP client can't start `yt-mcp` | Use the absolute path from `which yt-mcp`; check that you installed the `mcp` extra. |

---

## Development

```sh
git clone https://github.com/wtydev/yt-cli && cd yt-cli
uv sync --all-extras
uv run pytest -q          # offline, no credentials needed
uv run yt --help
uv run yt-mcp             # stdio MCP server
```

Tests run offline against canned responses shaped like the real API responses, with synthetic
IDs only: this repo is public, so no real channel data goes in it. See [`AGENTS.md`](AGENTS.md)
for the code layout.
