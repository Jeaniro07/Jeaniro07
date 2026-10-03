---
name: descript-video-editor
description: Edit video and audio in Descript through its official MCP server — find projects, import media, prompt Underlord for edits (filler words, Studio Sound, captions, B-roll), publish and export.
version: 1.0.0
author: jeaniro07
license: MIT
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [descript, video, audio, podcast, editing, underlord, mcp]
    category: media
    related_skills: [studio-orchestrator, hellominds-mind]
---

# Descript Video Editor

Descript edits video and audio by editing the transcript. Its AI co-editor,
**Underlord**, performs edits from natural-language instructions. Hermes reaches
Descript through the `descript` MCP server (`https://api.descript.com/v2/mcp`,
OAuth), configured in `~/.hermes/config.yaml`.

## When to use

- The user wants to cut, clean up, caption, or repurpose a video/podcast.
- The user mentions Descript, Underlord, a Descript project, Drive, or composition.
- Another agent (e.g. a HelloMinds Mind) produced a script that should become media.

## Prerequisites

1. `descript` is listed by `hermes mcp list` and connected. If it isn't, tell the
   user to run `./install.sh` from this bundle, then start Hermes — a browser
   opens for the Descript OAuth login and Drive selection.
2. On a headless server, use the `mcp-oauth-remote-gateway` optional skill to
   complete OAuth manually.
3. Never add a second, hand-made Descript MCP server — duplicate connections
   cause conflicting sessions.

## Workflow

Tool names come from the server; discover them with the MCP tool list rather
than guessing. The server's capability groups are:

| Stage    | What it does                                                    |
|----------|-----------------------------------------------------------------|
| Discover | Search and browse projects and compositions in the Drive        |
| Import   | Bring media in via public URL or file upload                    |
| Edit     | Prompt the project agent (Underlord) in natural language        |
| Publish  | Publish a composition as a shareable link                       |
| Export   | Export transcripts and timelines                                |

1. **Confirm the connection** — call the drive-info tool first and tell the user
   which Drive you are operating in.
2. **Locate or create the project** — search before creating; ask when several
   projects match.
3. **Import** media from URLs the user gave you. Large files take time to
   transcribe; poll status instead of re-importing.
4. **Edit with Underlord** — send one clear instruction per call, e.g.
   - "Remove filler words and long pauses over 1.5s"
   - "Apply Studio Sound to all speakers"
   - "Add animated captions, 2 lines max, brand font"
   - "Create a 60-second vertical clip of the strongest moment"
   - "Add B-roll that matches what the speaker describes"
5. **Verify** — export the transcript or timeline and summarise what changed.
6. **Publish / export** only when the user asks; return the share link.

## Rules

- Edits change the user's real projects. Before destructive edits (deleting
  scenes, overwriting compositions) ask once, listing exactly what will change.
  Any affirmative reply ("ya", "ok", "lanjut", "gas", "yes") is the approval:
  proceed immediately and never ask again for the same action. Non-destructive
  steps (search, import, export, previews) need no confirmation.
- Prefer duplicating a composition before large rewrites.
- Report Underlord's output faithfully, including failures.
- Respect plan limits (media minutes, AI credits); surface the error text as-is.

## References

- Descript Help Center — API & MCP: https://help.descript.com/api-and-mcp/api
- Connect Descript to any AI assistant (custom MCP): https://help.descript.com/hc/en-us/articles/46351582343309
- Underlord: https://help.descript.com/getting-started/underlord-beta-your-ai-co-editor-in-descript
- Web app: https://web.descript.com/
