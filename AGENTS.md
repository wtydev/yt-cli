# Agent guide

Instructions for any coding or chat agent working with this repo or using `yt`.

## Using yt for a user

- Prefer the MCP server (`yt-mcp`) when it is connected; otherwise run `yt <command> --json`.
- Task guides live in [`skills/`](skills/), one folder per task, in the SKILL.md format:
  - [`youtube-channel-analysis`](skills/youtube-channel-analysis/SKILL.md): performance, retention, traffic, what to change
  - [`youtube-upload`](skills/youtube-upload/SKILL.md): metadata, dry run, confirmed upload
  - [`youtube-comment-triage`](skills/youtube-comment-triage/SKILL.md): comments and AI/slop mentions
- Read the matching SKILL.md before starting one of those tasks.

## Rules

- **Writes need the user's explicit go-ahead.** `yt upload ... --yes`, `yt thumbnail ... --yes` and
  `yt sync ... --yes` write to YouTube. Show the dry-run plan first; run `--yes` only after the user confirms
  that exact plan. `yt revoke <channel> --yes` signs yt out at Google and deletes the
  channel's local data, so it needs the same go-ahead. No command deletes anything on
  YouTube; don't work around that.
- **`yt auth` asks the user to agree to the terms and privacy policy.** Never pass `--agree`
  for them.
- **Never handle sign-in.** `yt auth <channel>` opens a browser; the user signs in.
- **Secrets never enter git or chat:** `client_secret.json`, `tokens/`, `.env`.
- **Channel data stays between the user, yt and Google's APIs.** Don't send channel
  names, IDs or analytics to any other service.

## Working on the code

```sh
uv sync --all-extras        # CLI + MCP server + test deps
uv run pytest -q            # offline; synthetic fixtures, no network
uv run yt --help
uv run yt-mcp               # stdio MCP server
```

- `src/yt_cli/cli.py`: commands. Each `cmd_*` returns `(data, render)`; `--json` prints `data`.
- `src/yt_cli/mcp_server.py`: wraps those commands. Keep it free of YouTube writes.
- `src/yt_cli/sync.py`: API → SQLite. `report.py`: read-only views. `db.py`: schema.
- Tests must stay offline, with synthetic IDs only: this repo is public.
