# 🧠 MindSpace Bot — Usage Guide

**Philosophy:** Discord is the input stream; the filesystem is the source of truth. Every channel maps to a folder in the knowledge base. Anything you say or drop here can become durable, searchable, version-controlled content.

---

## Plain text messages

Every non-command text message in a non-silent channel goes to the **dialogue brain**. You do **not** need to @mention me — I respond to every message.

What happens on each turn:

1. I read your message plus the channel's recent history and the `view.md` chain for that folder.
2. I retrieve KB context on demand via tools — `search_channel_knowledge_base` for channel-scoped lookups, `search_global_knowledge_base` for cross-channel, `list_channel_files` to browse, `get_view_chain` to read the stance stack. Nothing is pre-loaded; I only fetch what I need.
3. I reply. A 🧠 **Thinking…** status message shows each tool call in real time, then disappears when I'm done.
4. If your message contains a valuable insight, I call **`record_thought`** to append a one-line entry to the channel's running `stream_of_conscious.md`. This happens quietly, no approval needed.
5. If you explicitly ask me to save / integrate / file something ("save this", "add to KB", "整合进知识库", "save your last reply", etc.) — **or** if I judge the content belongs in a structured KB file — I call **`propose_update`** and pop up a reviewed-proposal UI with a unified diff and Apply / Discard / Refine buttons. Nothing is written until you approve.

Rule of thumb: **ask for a save explicitly if you want a structured file.** Organic observations land in the stream; deliberate filings land in their own markdown.

---

## File drops

Drop a file into the channel. What happens next depends on one question: **did you @mention me on the drop?**

### No @mention → **Autoroute** (quiet, fire-and-forget)

Use this when you trust me to file it. I:

1. Read the file (plus a content snippet for text-ish types).
2. Walk the channel's folder tree, pick a semantically appropriate subfolder, and generate a content-based filename (`TYPE-YYYY-MM-DD-subject.md`).
3. Write the file — collisions get a `-2`, `-3`, … suffix automatically.
4. For text files: echo a one-line LLM summary back to the channel. For PDFs: save and continue (deep-document Q&A is currently disabled).
5. Commit to Git with a generated message.

You get one confirmation message with the final path. No approval needed.

### @mention + `.md` / `.markdown` / `.txt` → **Reviewed ingest** (slow path, proposal UI)

Use this when the file is a prose draft and you want me to think about *where* it fits. I:

1. Read your draft. Any text in your message (minus the mention) becomes **optional steering advice** — a bare `@bot` drop works fine.
2. Look at semantically similar KB files + the channel folder tree, then plan:
   - **`new`** — create a fresh KB entry at a content-derived path, or
   - **`update`** — merge your draft into an existing related file.
3. Generate the final markdown. `.txt` input is converted to markdown; the KB target is always `.md`.
4. Post a proposal in the channel: a **bullet-point rationale** (up to 5 reasons for the choice), a **unified diff**, and three buttons:
   - ✅ **Apply** — write, commit, re-index.
   - ❌ **Discard** — drop it.
   - ✏️ **Refine** — give me more instructions and I regenerate against the same target.
   The full diff is also attached as a `.diff` file in case the inline embed is hard to read.
5. The proposal lives **in memory only**. If I restart before you click, the buttons expire — drop the file again.

### @mention + other extension (`.pdf`, `.docx`, images, …) → Autoroute with advice

Only prose text files flow through the proposal editor (the UI relies on a readable text diff). @mentioning a non-prose file doesn't trigger review — it just threads your message text into the routing prompt as a hint. You still get the quiet autoroute confirmation, not the Apply/Discard/Refine UI.

### Edge cases
- **`.md` / `.markdown` / `.txt` drop, no @mention, extra text in the message** → Autoroute, not reviewed. The mention is the trigger, not the caption.
- **Empty draft with @mention** → Rejected with a warning.

---

## When should I @mention?

| You want to… | @mention? |
| :--- | :--- |
| Chat, ask a question, drop a thought | **No** — plain text works. |
| Save text content as a KB entry | **No** — say it in prose ("save this as a note on X") and I'll propose. |
| Drop a file and trust me to file it automatically | **No** — just drop it. |
| Drop a `.md` / `.txt` draft and review *where* it lands before writing | **Yes** — @mention on the drop. |
| Add routing advice for a non-prose file (PDF, image) | **Yes** — @mention with a text hint. |

If you're unsure: don't @mention. The quiet path does the right thing most of the time, and you can always refine afterward by asking me in chat.

---

## URLs

Paste a URL (`http://…` or `https://…`) and I'll reply asking you to paste the content manually. I don't fetch external pages — that's an intentional choice to keep the agent disciplined (you decide what's ingestable, not me).

---

## Commands

- `/consolidate` — synthesize `stream_of_conscious.md` into a structured dated article; clears the stream.
- `/research <topic>` — generate a cited research report from KB context plus web search. Saves and posts the file.
- `/omni <query>` — cross-KB synthesis across every channel folder.
- `/change_my_view [instruction]` — update or view the channel's root stance (`view.md`) via a reviewed proposal. Accepting also fires a downward consistency sweep that proposes updates for any subfolder view that now conflicts. Leave *instruction* blank to just read the current stance.
- `/view_down_check` — top-down sweep of the view tree: re-challenge every subfolder view against its evidence, then check each descendant against the channel-root stance. Run irregularly to catch drift the per-commit hook missed.
- `/sync` — manually rebuild the vector index for the current channel (picks up external filesystem edits).
- `/help` — post this guide to `#console`.

---

## The view tree

Every folder under a channel can hold its own `view.md` — a concise stance / opinion / conclusion distilled from the evidence files in that folder. The channel-root `view.md` is the **master view**; it's the only one you can *initiate* changes to (via `/change_my_view`). Subfolder views are **LLM-initiated** — only the agent's challenger and consistency checks propose updates to them.

Every view change, master or subfolder, still goes through the proposal UI for your approval — nothing about `view.md` ever changes silently. After any KB-mutating commit I (a) re-challenge the touched folder's local view against its new evidence and (b) walk upward to check every ancestor for consistency. New information always propagates upward. Each conflicting level surfaces its own proposal; the tree moves toward coherence one approval at a time.

While the cascade runs you'll see a single 🧭 status message in the channel that updates in place through each step (`[3/8] Upward-reconciling ancestors from \`Research/AI\`…`), then disappears when I'm done. Commands that touch many folders may take 20-60 seconds before reporting "done" — the status ticker is there so the wait isn't silent.

---

## Silent channels (output-only)

Three channels are **output-only**: I can post into them, but anything you type is ignored — no dialogue, no ingest, no commands.

- `#console` — bot-managed. All logger output and `/help` replies land here.
- `#general` — Discord auto-creates it on every server; I stay out by convention so the lobby stays a human space.
- `#notification` — legacy from an earlier version. No longer auto-created, but silenced here so I don't accidentally engage on servers that still have the channel from a prior deployment.

If you want to chat with me, use any other channel.
