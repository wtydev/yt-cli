---
name: youtube-upload
description: Prepare and check a video upload to the user's own YouTube channel with the yt CLI — title, description, tags, category, privacy, scheduled publish time, thumbnail, playlist, made-for-kids and AI (synthetic media) disclosure. Use when the user wants to upload, schedule or publish a video, or to write its metadata.
---

# Uploading a video with yt

An upload is a write to YouTube. **Never send one without the user's explicit go-ahead
for that exact plan.** The agent prepares and checks; the user decides.

## Procedure

1. **Collect the metadata.** Ask only for what is missing. Prefer a JSON file next to
   the video, which can be reviewed and reused:

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

   Paths are relative to the JSON file. Flags given on the command line override its keys.

2. **Dry run.** `yt upload <channel> --meta upload.json` (no `--yes`), or the MCP tool
   `yt_upload_plan`. This validates everything and prints the plan. Nothing is sent.
   Fix any error it reports and re-run.

3. **Show the plan** to the user in full — channel, file, title, privacy, schedule,
   disclosure — and ask them to confirm.

4. **Send only after confirmation**: `yt upload <channel> --meta upload.json --yes`.
   The MCP server cannot upload; give the user the `command` from `yt_upload_plan` to
   run. Uploads are resumable and retry transient errors on their own.

5. **Report** the video ID and Studio link it prints. If it lists `errors` (thumbnail or
   playlist failed), the video itself was uploaded: tell the user to fix those in Studio.

## Rules and gotchas

- **Default to `private`.** Use `unlisted`/`public` only when the user asks.
- **`publish_at` requires `private`** and an ISO 8601 time with a timezone. YouTube
  makes the video public at that time.
- **Unaudited API projects** have every upload locked to private by YouTube, whatever
  privacy is requested. The plan warns about this.
- **`synthetic_media`**: set `true` when realistic-looking content was altered or
  generated with AI (YouTube's disclosure, `status.containsSyntheticMedia`). Ask if unsure.
- **`made_for_kids`** is a legal declaration (COPPA). Ask; don't guess.
- **Category** accepts an ID or a name (`education`, `science`, `entertainment`, …).
- **Subscribers are not notified** for private uploads.
- yt has **no delete command**. If a test upload must go, the user deletes it in Studio.
- Titles up to 100 characters, descriptions up to 5,000 bytes (the dry run checks both).
