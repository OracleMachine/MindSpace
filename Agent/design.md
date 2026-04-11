# Project Specification: MindSpace Hierarchical Knowledge Agent

## 1. Project Goal & Philosophy

Create an AI agent that acts as a cognitive partner across three primary functions: **Thought Recording**, **Knowledge Base Management**, and **Research**.

**Philosophy:** "Discord as the Input Stream, Filesystem as the Source of Truth."

The system uses **VikingContextManager** (wrapping OpenViking) for context navigation and **PageIndexManager** (wrapping PageIndex) for deep PDF document reasoning, resulting in a human-readable, self-organizing filesystem backed by **Git** for full auditability.

---

## 2. Technical Stack

- **Dialogue Brain:** Google GenAI SDK (`google-genai`) — passive chat, URL/file analysis, commit messages (`DIALOGUE_BRAIN_TYPE = "GoogleGenAISdk"`); LiteLLM available as an alternative
- **Command Brain:** Gemini CLI (`gemini -y`) — agentic `!organize` / `!research` / `!omni` with web search, file I/O, multi-step loops (`COMMAND_BRAIN_TYPE = "gemini-cli"`)
- **Language:** Python 3.12+
- **Semantic Search:** [OpenViking](https://github.com/volcengine/OpenViking) — wrapped by `VikingContextManager` in `viking.py`; fully integrated
- **PDF Reasoning:** [PageIndex](https://github.com/VectifyAI/PageIndex) — wrapped by `PageIndexManager` in `pageindex_manager.py`; fully integrated
- **Front-end:** Discord API (`discord.py`), supporting both `!prefix` and `/slash` commands
- **Version Control:** Git (one repository per Discord Server, stored at `BASE_STORAGE_PATH`)

---

## 3. Architecture & Filesystem Mapping

### 3.1 The "One Server = One Repo" Rule (Single-Server Intent)

**Strict Constraint:** This bot is designed to serve exactly **one** Discord server.
- Each Discord Server maps to a single Git repository at `BASE_STORAGE_PATH` (default: `~/repos/Thought`).
- If the bot is invited to a second server, it will automatically log an error and leave the second server immediately.
- This ensures absolute data isolation and simplifies the filesystem mapping logic.

### 3.2 Hierarchy Logic (Macro-to-Micro)

#### Human-Controlled Zone (Rigid)
- Discord Channels map 1:1 to top-level folders (e.g., `#machine-learning` → `Channels/machine-learning/`).
- Every channel folder contains one core file, initialized automatically:
  - `stream_of_conscious.md` — running log of AI-extracted insights

#### AI-Controlled Autonomous Zone (Fluid)
- Inside each channel folder, the agent has freedom to create semantically nested sub-folders (e.g., `Machine_Learning/Neural_Networks/Transformers/`).

---

## 4. Module Responsibilities

| Module | Responsibility |
| :--- | :--- |
| `bot.py` | Discord event loop (`on_message`), command routing (prefix `!` and slash `/`), startup sync. Single `KnowledgeBaseManager` instance (`self.kb`) initialized in `on_ready`. `on_guild_join` enforces the single-server constraint by leaving immediately if `self.kb` is already set. |
| `agent.py` | LLM abstraction. `GoogleGenAIBrain` / `LiteLLMBrain` for dialogue (chat, URL/file analysis, commit messages). `GeminiCLIBrain` for commands — exposes `stream(prompt, cwd)` returning a `CliStream` async-iterable handle; env (`GEMINI_CLI_HOME`) and args (`-y`, `-m`) are always injected. |
| `manager.py` | Filesystem writes, Git commits with lazy re-indexing, per-channel conversation history, stream reads |
| `tools.py` | `MindSpaceTools`: closure-bound tool functions exposed to the LLM during passive dialogue (list files, search channel KB, search global KB) |
| `viking.py` | `VikingContextManager`: OpenViking wrapper; channel-scoped and global semantic search modes |
| `pageindex_manager.py` | `PageIndexManager`: PageIndex cloud API wrapper; PDF upload, async processing, channel-scoped deep Q&A |
| `config.py` | Centralized configuration for paths, models, brain type, history char limit |
| `logger.py` | Dual-output logger: console (all levels) + Discord `#system-log` (INFO and above, guild-scoped) |

---

## 5. Memory & Context Architecture

The agent maintains two layers of memory per channel:

| Layer | Storage | Scope | Reset on restart? |
| :--- | :--- | :--- | :--- |
| **Short-term** | In-memory bounded string (`CONVERSATION_HISTORY_MAX_CHARS = 8000` chars), trimmed at message boundaries | Current session | Yes (re-seeded from Discord history on startup) |
| **Long-term** | `stream_of_conscious.md` on disk | Persistent across restarts | No |

On every passive dialogue message, the agent receives:

```
[System Context]
  - Channel identity
  - Recent conversation history (char-bounded, oldest messages trimmed first)
  - stream_of_conscious.md (all extracted insights so far)
  - Tool-use instruction (search tools available for on-demand KB access)

[Current Message]
  - User's latest message
```

**Note:** The conversation history is embedded in the system context string and the brain's `chat()` call receives an empty turn list. Viking context is **not** pre-injected; instead, the agent calls tools (`search_channel_knowledge_base`, `search_global_knowledge_base`) autonomously when it determines they are needed.

After each reply, the turn is appended to the in-memory history string and trimmed if over the char limit. Extracted `THOUGHT:` blocks are appended to `stream_of_conscious.md` and committed to Git.

### 5.1 Startup Seeding

On `on_ready`, the bot:
1. Scans Discord channel history (last 50 messages per channel) and seeds the in-memory history cache (`_seed_channel_history`).
2. Creates Discord channels for any KB folders that don't have a matching Discord channel (`_sync_kb_channels`).
3. Runs `viking.rebuild_index()` and `pageindex.rebuild_index()` for a full initial sync.

---

## 6. VikingContextManager: Two-Mode Context

`viking.py` wraps OpenViking with two explicit modes:

| Mode | Method | Trigger | Scope |
| :--- | :--- | :--- | :--- |
| **Channel-scoped** | `get_channel_context(channel_name, query)` | All default operations | Current channel folder only |
| **Global** | `get_global_context(query)` | `!omni` and `search_global_knowledge_base` tool | All channel folders in the server |

**Indexing strategy — incremental, duplicate-free:**

OpenViking stores the vectors, but does not tell the bot which on-disk files it has already seen. To avoid re-indexing unchanged files on every restart (and, worse, accumulating duplicate vectors), `viking.py` maintains its own bookkeeping cache.

**The cache:** `BASE_STORAGE_PATH/.viking_index.json` (e.g. `Thought/.viking_index.json`) — a small JSON file owned by the bot, **not** by OpenViking. Structure:

```json
{
  "<rel_path_under_Channels>": {"mtime": <float>, "uri": "viking://..."}
}
```

Keys are file paths relative to `Channels/`. Values track the file's mtime at the time of indexing and the resource URI returned by `add_resource()`. Writes are atomic (`tmp + os.replace`) so a crash mid-write cannot corrupt it.

| | Cache (bot) | OpenViking store |
| :--- | :--- | :--- |
| Location | `Thought/.viking_index.json` | `Thought/openviking/` |
| Owner | `VikingContextManager` | OpenViking library |
| Contents | `{file → mtime, uri}` bookkeeping | Vectors + SQLite DB |
| Size | Kilobytes | Large (grows with KB) |

**`index_file(path, channel)` — idempotent:**
- Unchanged (`cached.mtime >= disk.mtime`) → no-op, returns True. Critical duplicate-prevention path.
- Modified → `client.rm(cached.uri)` to delete the stale vector, then `add_resource()`, update cache entry.
- New → `add_resource()`, write cache entry.
- Cache persisted on every successful add.

**`rebuild_index()` — sync, not rebuild:**
- **Cold start** (cache file missing or corrupted): `client.rm("viking://resources/", recursive=True)` wipes the store, then everything on disk is re-indexed from scratch. This is the self-healing path — also runs if the user manually deletes the cache to force a clean rebuild.
- **Warm start** (cache present): walks `Channels/*/**/*.md`, compares mtimes to the cache, and only touches the delta — new files added, modified files re-added after `rm`, deleted files purged via `rm` and dropped from the cache. Logs a summary: `N new, M modified, K removed, U unchanged, F failed`.

**Post-commit indexing:** After every `git_commit()`, `manager.py` calls `index_file()` on each staged file. Because `index_file()` is idempotent against the cache, this is safe and cheap — unchanged files short-circuit, modified files handle the rm+re-add internally.

**Invariant:** cache file present ⇔ OpenViking store matches cache. Deleting the cache is always safe; the store will self-heal on next startup.

---

## 7. PageIndexManager: PDF Deep Reasoning

`pageindex_manager.py` wraps the PageIndex cloud API:

- PDFs are submitted to a per-channel cloud folder and processed asynchronously (polled until ready).
- A local `.pageindex_index.json` persists `{file_path → doc_id}` and `{channel → folder_id}` mappings across restarts to avoid re-uploading.
- `query_channel()` runs deep Q&A against all indexed PDFs in a channel using `chat_completions`.
- `rebuild_index()` is called at startup to submit any untracked PDFs.

---

## 8. Core Workflows (Commands)

| Trigger | Action | Output file |
| :--- | :--- | :--- |
| `!organize` / `/organize` | Scans untracked files, runs semantic reasoning, git commit | — |
| `!consolidate` / `/consolidate` | Synthesizes `stream_of_conscious.md` into a permanent article, clears stream, git commit | `ARTICLE-<date>-<id>.md` |
| `!research [topic]` / `/research` | Deep-dive on topic using Viking + PageIndex context, git commit | `RESEARCH-<date>-<id>.md` |
| `!omni [query]` / `/omni` | Cross-KB synthesis across **all** channel folders (global Viking traversal), git commit | `OMNI-<date>-<id>.md` |
| URL in message | Fetches page, converts to Markdown snapshot, git commit | `WEBPAGE-<date>-<id>.md` |
| File attachment | Saves and semantically analyzes file (PDF via PageIndex, others via LLM), git commit | — |
| Plain text | Passive dialogue: replies + tool access to KB + silently extracts `THOUGHT:` block to `stream_of_conscious.md` | — |

All output `.md` files are sent back to the Discord channel as Discord file attachments immediately after creation.

---

## 9. Command Brain: Gemini CLI Execution

Active commands (`!organize`, `!research`, `!omni`) delegate to the Gemini CLI subprocess via `GeminiCLIBrain.stream(prompt, cwd)`. The brain owns all invocation details (spawn, env injection, stdin piping, ANSI stripping); `bot.py` stays agnostic.

### 9.1 Config Isolation — `GEMINI_CLI_HOME`

The CLI is invoked with `GEMINI_CLI_HOME=<BASE_STORAGE_PATH>/bot-home` set in the subprocess env. This reroots the CLI's user-scope config away from `~/.gemini/` to an isolated `Thought/bot-home/.gemini/`:

- `settings.json` — bot-specific config (hooks disabled, notifications off, telemetry off — no shared state with the user's interactive CLI)
- `oauth_creds.json`, `google_accounts.json` — symlinks back to `~/.gemini/` so auth still works
- `bot-home/` is gitignored

The user's interactive `gemini` sessions in a shell remain untouched.

### 9.2 Workspace Sandboxing — `cwd`

Each command sets `cwd` to the smallest directory the CLI needs. In YOLO mode the agent has unrestricted file access inside its workspace, so this is the primary sandbox boundary:

| Command | `cwd` | Scope rationale |
| :--- | :--- | :--- |
| `!organize` | `<channel_path>` | Reorganizes one channel — no cross-channel access needed |
| `!research` | `<channel_path>` | Report is written back into the same channel |
| `!omni` | `Channels/` | Cross-channel synthesis requires sibling reads; still sandboxed from `bot-home/`, `openviking/`, `ov.conf` |

**Config scope and workspace scope are independent.** Because the bot config is loaded as user-scope via `GEMINI_CLI_HOME` (not as workspace settings from `cwd/.gemini/`), `cwd` can be tightened to any subtree without losing the bot config. Headless-mode trust (stdin not a TTY) auto-trusts the workspace, so no folder-trust prompt appears.

### 9.3 Live Streaming

`GeminiCLIBrain.stream()` spawns the subprocess and returns a `CliStream` handle — an async-iterable that yields ANSI-stripped, non-empty lines as the CLI emits them, and exposes `.returncode` once iteration completes. Two consumption patterns:

- **Discord UI** (`!organize`, `!omni`): `bot._render_stream_to_channel(channel, header, handle)` edits a single live Discord message every ~2 seconds with the latest output tail, then marks it complete.
- **Console + interaction edit** (`!research`): the handler iterates `async for line in handle` directly — each line is logged to the server console, and the deferred `/research` interaction's "thinking..." message is updated in place (no follow-up spam).

Either way, the event loop stays responsive throughout the multi-minute CLI run.

---

## 10. Implementation Rules

- **One process per Discord Server.** `KnowledgeBaseManager` is lazy-loaded per guild in `bot.py`.
- **Every active command** (`!` / `/`) is followed by a `git commit` with an AI-generated message explaining intent.
- **Bot is quiet.** No unprompted messages, no pinned maps, no ASCII trees.
- **Instant file delivery.** Every new `.md` file created is sent back to Discord as an attachment.
- **File naming:** all output markdown files use lowercase `.md` extension with `TYPE-DATE-ID` format.
- **Preflight check on startup:** validates that PageIndex, OpenViking, and GitPython are installed and that API keys are functional before the Discord connection is established.
