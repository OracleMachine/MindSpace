# Project Specification: MindSpace Hierarchical Knowledge Agent

## 1. Project Goal & Philosophy

Create an AI agent that acts as a cognitive partner across three primary functions: **Thought Recording**, **Knowledge Base Management**, and **Research**.

**Philosophy:** "Discord as the Input Stream, Filesystem as the Source of Truth."

The system uses **VikingContextManager** (wrapping OpenViking) for context navigation and **PageIndexManager** (wrapping PageIndex) for deep PDF document reasoning, resulting in a human-readable, self-organizing filesystem backed by **Git** for full auditability.

---

## 2. Technical Stack

- **Engine:** Google GenAI SDK (`google-genai`) — default brain (`AGENT_BRAIN_TYPE = "sdk"`)
- **Alternative Brain:** LiteLLM for multi-provider support (`AGENT_BRAIN_TYPE = "litellm"`)
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
| `agent.py` | LLM abstraction (`GoogleGenAIBrain`, `LiteLLMBrain`), dialogue, URL processing, file analysis |
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

**Indexing strategy:**
- `rebuild_index()` is called **once at startup** to do a full re-index of all `.md` files.
- After every `git_commit()`, only the files that were staged in that commit are lazily re-indexed (`index_file()`), avoiding a full rebuild on every write.

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

## 9. Implementation Rules

- **One process per Discord Server.** `KnowledgeBaseManager` is lazy-loaded per guild in `bot.py`.
- **Every active command** (`!` / `/`) is followed by a `git commit` with an AI-generated message explaining intent.
- **Bot is quiet.** No unprompted messages, no pinned maps, no ASCII trees.
- **Instant file delivery.** Every new `.md` file created is sent back to Discord as an attachment.
- **File naming:** all output markdown files use lowercase `.md` extension with `TYPE-DATE-ID` format.
- **Preflight check on startup:** validates that PageIndex, OpenViking, and GitPython are installed and that API keys are functional before the Discord connection is established.
