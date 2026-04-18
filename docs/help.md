# 🧠 MindSpace Bot — Usage Guide

**Philosophy:** Discord is the input stream; the filesystem is the source of truth. Every channel maps to a folder in the Knowledge Base, and everything you say or drop here can become durable, searchable, version-controlled content.

## How messages are processed
- **Plain text** → Passive dialogue. The bot replies using tool-based KB retrieval, records insights silently via `record_thought`, and may proactively pop up a **reviewed proposal UI** via `propose_update` if it determines a structured file needs updating.
- **URL** → The bot will instruct you to paste the content manually for ingestion.
- **File drop** → Autoroute into a content-chosen subfolder, *or* a reviewed proposal when you @mention the bot on a `.md` file. See the next section for the full workflow.

## File drops in detail
The bot branches on a single question: **did you @mention me?** That's the explicit signal for the heavy, reviewed path. Otherwise, the drop is silent and fire-and-forget.

### Autoroute (no @mention)
1. The bot reads the file — and, for text-ish types (`.md`, `.txt`, `.json`, `.py`, etc.), a short content snippet.
2. An LLM sees the current channel's folder tree and picks a target **subfolder** + a content-based **filename**, preferring to reuse existing folders rather than inventing new ones. For binary files (PDFs, images) the LLM routes by filename alone.
3. The file is written to `<channel>/<subfolder>/<new-filename>`. Name collisions get a `-2`, `-3`, … suffix automatically.
4. PDFs are uploaded to **PageIndex** for deep document Q&A. Text files get a one-line LLM summary echoed back to the channel.
5. Everything is committed to Git with a generated message. The final path is posted in the source channel so you can see where it landed.

### Reviewed ingest (@mention + `.md`)
1. Any text in your message (minus the mention itself) becomes *advice* — optional steering for the bot. A bare `@bot` drop works fine; the bot will infer intent from the draft and the channel.
2. An LLM sees your draft, semantically-similar existing KB files, and the channel folder tree, then plans one of:
   - **`new`** — create a fresh KB entry at a content-derived path.
   - **`update`** — merge your draft into an existing file it believes is related.
3. A second LLM call produces the final markdown. If the planner picked a poor update target, the merger can reject it mid-flight and fall back to creating a new file.
4. The proposal is posted in the source channel as a **unified diff** with three buttons:
   - ✅ **Apply** — write the file, commit, and re-index.
   - ❌ **Discard** — drop the proposal with no changes.
   - ✏️ **Refine** — give the bot more instructions and regenerate against the same target.
5. Proposals live **in memory only**. If the bot restarts before you click, the buttons expire and you'll need to drop the file again.

### Edge cases
- **`.md` drop, no @mention** → Autoroute, even if your message has text.
- **Non-`.md` drop + @mention** → Autoroute, because only markdown flows through the proposal editor. Your message becomes a routing hint instead.
- **Empty `.md` drop with @mention** → Rejected with a warning — there's nothing to propose.

## Commands
- `/organize` — Scan untracked files in the current channel and semantically reorganize them. Commits the result.
- `/consolidate` — Synthesize `stream_of_conscious.md` into a structured dated article; clears the stream.
- `/research <topic>` — Generate a cited research report using KB context + web search. Saves and posts the file.
- `/change_my_view [instruction]` — Update or view the channel's root stance (`view.md`) via a reviewed proposal. Accepting a change also runs a consistency sweep across every subfolder view, emitting proposals for any child that drifts. Leave instruction blank to view the current mindset.
- `/walkthrough_views` — Re-challenge every subfolder view in the current channel against its evidence, plus a consistency sweep from the channel root. Run irregularly to catch drift the per-commit hook missed.
- `/omni <query>` — Cross-KB synthesis across all channel folders.
- `/sync` — Manually rebuild the vector index for the current channel (picks up external filesystem edits).
- `/help` — Post this guide to `#notification`.

## The view tree
Every folder under a channel can hold its own `view.md` — a concise stance / opinion / conclusion distilled from the evidence files in that folder. The channel-root `view.md` acts as the roll-up; subfolder views express the local position. After any KB-mutating commit, the agent re-challenges the touched folder's local view against its new evidence and, if drift is detected, surfaces a proposal. Accepting a view update then walks upward and emits a proposal for any ancestor whose stance is now inconsistent — so the tree stays coherent one approval at a time.

## Reserved channels
`#system-log` and `#notification` are managed by the bot. Messages posted in them are ignored. `/help` always routes its reply here to keep your working channels clean.
