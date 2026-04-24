# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Working-style rules (commits, bumps, commit-message format) live in `AGENTS.md`** — shared across every agent CLI. Read it alongside this file.

@AGENTS.md

## Running the Bot

One agent per profile, one process per agent:

```bash
python run.py <profile>     # profile name, e.g. `default` → profiles/default.yaml
python run.py work.yaml     # filename also accepted
python run.py ./profiles/team-b.yaml   # explicit path also accepted
```

Each profile YAML is a self-contained bot config (`credentials:`, model
settings, MCP servers — everything). All profiles are gitignored (`profiles/*.yaml`):
the repo ships agent code, not configuration. No profile is tracked, so
there's no template-drift risk; the schema is documented in `QUICKSTART.md`.

The bot's display name is read from Discord at runtime (`self.user.display_name`)
and threaded into the dialogue system prompt, so the LLM's self-description
is always whatever the Discord application is named — no separate `agent.name`
field to keep in sync.

## Architecture

MindSpace is a Discord bot that acts as a hierarchical knowledge agent. Philosophy: **Discord as the Input Stream, Filesystem as the Source of Truth.**

### Key Data Flow

1. Every Discord Server maps to a Git repo at `BASE_STORAGE_PATH` (`/home/yolo/repos/Thought`)
2. Each Discord channel maps to a folder inside `Channels/` in that repo
3. Every channel folder contains `stream_of_conscious.md` — running log of extracted thoughts (recorded via `record_thought` tool call)

### Repo Layout

```
Thought/
├── Channels/              <- channel folders (one per Discord channel)
│   ├── general/
│   └── oil-war-research/
├── openviking/            <- OpenViking vector DB data
├── bot-home/              <- isolated Gemini CLI config (GEMINI_CLI_HOME)
└── ov.conf                <- OpenViking config (uses ${GEMINI_API_KEY} env var)
```

### Module Responsibilities

- **`bot.py`** — `MindSpaceBot(discord.Client)`: The entry point. Handles `on_message` and routes to commands (delegated to `services.py`), file ingestion, or passive dialogue. Wraps tools with async progress decorators for Discord status updates.
- **`services.py`** — Core business logic for active commands (`!organize`, `!consolidate`, `!research`, `!omni`, `!change_my_view`) and the view-tree challenger (`challenge_local_view`, `check_upward_consistency`, `check_downward_consistency`, `handle_view_down_check`).
- **`prompts.py`** — Centralized repository for all LLM prompt templates used by the agent and services.
- **`agent.py`** — `MindSpaceAgent`: Dual-brain LLM abstraction. `GoogleGenAIBrain` for dialogue (async chat via `achat`, file analysis, commit messages). `GeminiCLIBrain` for commands — owns the `gemini -y` subprocess, env (`GEMINI_CLI_HOME`) and args injection, and exposes `stream(prompt, cwd)` -> `CliStream` async-iterable handle. URLs in user messages are not scraped — the ingestion handler replies with a paste-content-manually hint.
- **`tools.py`** — `MindSpaceTools`: closure-bound tool functions exposed to the LLM during passive dialogue (`list_channel_files`, `search_channel_knowledge_base`, `search_global_knowledge_base`, `list_global_files`, `get_view_chain`, `record_thought`, `propose_update`). `propose_update` refuses any path whose basename is `view.md` at any depth — view edits only flow through the challenger / `/change_my_view`.
- **`manager.py`** — `KnowledgeBaseManager`: All filesystem and Git operations. Creates per-server repos, manages channel folders, appends thoughts, and performs `git commit` after every active command. `save_state` returns `{touched, sha}` so the bot can drive the view-tree challenger. Hierarchical view helpers: `read_view`, `write_view`, `get_view_chain`, `list_subfolders_with_content`, `read_folder_context`.
- **`mcp_bridge.py`** — MCP integration. `sync_cli_settings()` renders MCP servers into Gemini CLI's settings.json. `MCPSessionPool` manages live `ClientSession`s for the dialogue brain via AFC.
- **`config.py`** — Thin YAML loader. Reads `profiles/<name>.yaml` at repo root, where `<name>` is `$MINDSPACE_PROFILE` (defaults to `default`). `$MINDSPACE_CONFIG` overrides the path outright. Exposes all settings as module-level constants. Secrets are inline in the profile under `credentials:` (no env-var fallback).
- **`logger.py`** — `MindSpaceLogger`: Triple-output logger (stdout + file + Discord `#console` channel), each with independent configurable levels. `/help` output is also routed to `#console`.

### Design Principles

- **Tool-first architecture**: all structured bot behaviors (KB retrieval, thought recording, side-effects) are expressed as typed tool calls, not in-band prompt conventions. See `docs/design.md` section 5.0 for rationale.
- **Tools-first dialogue**: the dialogue brain receives NO pre-loaded KB context (beyond the view chain). The model must call `search_channel_knowledge_base` to retrieve data. This keeps prompts lean and ensures tool progress UI is exercised.
- **View hierarchy**: every folder under a channel may hold its own `view.md` (stance/opinion/conclusion at that scope). The channel-root view rolls up the subtree. Governing rule: **users can only initiate master-view updates** (via `/change_my_view`); subfolder view updates are LLM-initiated only (via the challenger / consistency checks). But every view change — master or subfolder — still requires user approval through the proposal UI. An event-driven challenger wired into `save_state` re-distills the touched folder's view after each content commit AND walks upward to check every ancestor — new information always propagates up. `/change_my_view` additionally fires the downward cascade. See `docs/design.md` §5.6.
- **Agent Skills pattern deferred**: Evaluated Anthropic's [Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) (SKILL.md folders with YAML frontmatter, progressive disclosure) and deferred adoption. Rationale: narrow, stable command surface (~5 commands + 7 tools) for a single user, prompts already centralized in `prompts.py`, and MCP already handles dynamic external-tool discovery — the progressive-disclosure tax doesn't pay off. Revisit when `prompts.py` becomes unwieldy (~800+ lines) or a command needs bundled reference material large enough that the LLM should `read` it on demand rather than inline it.

### LLM Brain Selection

Two brains run in parallel, each specialized for its role:

- **Dialogue brain** — `GoogleGenAIBrain` (Google GenAI SDK). Passive chat, file analysis, commit messages. Uses AFC (Automatic Function Calling) for tool dispatch including MCP sessions.
- **Command brain** — `GeminiCLIBrain` (Gemini CLI `gemini -y`). `!organize`, `!research`, `!omni`. Web search, file I/O, multi-step agentic loops. Config isolated via `GEMINI_CLI_HOME=Thought/bot-home`; workspace sandboxed via `cwd`.

### OpenViking & PDF deep-reasoning

- **OpenViking** (`viking.py`): Semantic vector search. Indexes all `.md` files in `Channels/` into a vector DB at `Thought/openviking/`. Config at `Thought/ov.conf` (loaded via `OPENVIKING_CONFIG_FILE` env var set in code). Provides channel-scoped context for passive dialogue and global context for `!omni`.
- **`PageIndexManager`** (`knowledgebase/pageindex.py`): Reserved interface for PDF deep-document Q&A. No backend is currently wired — the class's public methods (`index_document`, `query_channel`, `get_tree`, `rebuild_index`, `validate`, etc.) are no-op stubs returning empty values of their advertised shapes. Every caller site stays untouched so a future PDF Q&A backend can replace just this file's bodies.

Install: `pip install openviking`

### Discord Commands

For a full list of commands (`!organize`, `!research`, `!change_my_view`, etc.) and detailed file ingestion workflows, refer to `docs/design.md` and `docs/help.md`.

## Configuration

Each agent has its own `profiles/<name>.yaml` at the repo root. The profile is a standalone config — `credentials:` (Discord + Gemini keys inline), `log:`, `storage:`, `brains:`, `conversation:`, `mcp:` — everything one agent needs, with no shared global state between agents.

Profile resolution order inside `config.py`:
1. `$MINDSPACE_CONFIG` — absolute path (what `run.py` sets).
2. `$MINDSPACE_PROFILE` — profile name → `profiles/<name>.yaml`.
3. Fallback → `profiles/default.yaml` (none tracked in git — `run.py` always sets #1, so this only fires for bare `python -m mindspace.main` invocations and errors out if the user hasn't created the file).

Bot identity (the value spliced into `ENGAGE_DIALOGUE_SYSTEM_PROMPT` as `{agent_name}`) comes from `self.user.display_name` after the bot logs in to Discord — the same value Discord already stamps on the bot's messages in channel history. Keeping the prompt's self-reference and the history author label identical avoids confusing the LLM when it reads prior turns.

```yaml
credentials:
  discord_token: "..."        # required — bot token for this agent
  gemini_api_key: "..."       # required

log:
  stream_level: DEBUG       # console — DEBUG | INFO | WARNING | ERROR
  file_level: DEBUG         # file — rotates daily, keeps 3 days
  discord_level: INFO       # Discord #console channel
  file_path: ~/logs/MindSpace/mindspace.log

storage:
  base_path: /home/yolo/repos/Thought

brains:
  dialogue_type: GoogleGenAISdk
  enable_google_search: true
  command_type: gemini-cli
  gemini_sdk_model: gemini-3.1-flash-lite-preview
  gemini_cli_model: auto-gemini-3

conversation:
  history_max_chars: 8000
```

## MCP Integration

MCP (Model Context Protocol) extends the bot with external tool capabilities. Both brains consume configured MCP servers automatically:
- **Dialogue brain**: live `ClientSession`s passed to AFC alongside local tools.
- **Command brain**: servers rendered into Gemini CLI's `settings.json` automatically.

For step-by-step setup instructions, please see `QUICKSTART.md`.

### How MCP flows through the system

```
profiles/<active>.yaml
  mcp.servers: {name: {url, headers}}
       │
       ├──> config.py: MCP_SERVERS (env vars expanded)
       │
       ├──> mcp_bridge.sync_cli_settings()
       │      └──> bot-home/.gemini/settings.json (command brain)
       │
       └──> mcp_bridge.MCPSessionPool.connect()
              └──> live ClientSession per server (dialogue brain)
                     └──> passed to GoogleGenAIBrain.achat(tools=[...sessions])
                            └──> AFC handles discovery + dispatch
```
