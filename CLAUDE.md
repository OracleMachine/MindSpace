# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Bot

```bash
cd Agent
python3 bot.py
```

Required environment variables (export in `~/.zshrc`):
```bash
export DISCORD_TOKEN="your_discord_bot_token"
export GEMINI_API_KEY="your_gemini_api_key"
export PAGEINDEX_API_KEY="your_pageindex_api_key"
```

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
- **`services.py`** — Contains the core business logic for all active commands (`!organize`, `!consolidate`, `!research`, `!omni`, `!change_my_view`).
- **`prompts.py`** — Centralized repository for all LLM prompt templates used by the agent and services.
- **`agent.py`** — `MindSpaceAgent`: Dual-brain LLM abstraction. `GoogleGenAIBrain` for dialogue (async chat via `achat`, URL/file analysis, commit messages). `GeminiCLIBrain` for commands — owns the `gemini -y` subprocess, env (`GEMINI_CLI_HOME`) and args injection, and exposes `stream(prompt, cwd)` -> `CliStream` async-iterable handle.
- **`tools.py`** — `MindSpaceTools`: closure-bound tool functions exposed to the LLM during passive dialogue (`list_channel_files`, `search_channel_knowledge_base`, `search_global_knowledge_base`, `list_global_files`, `record_thought`).
- **`manager.py`** — `KnowledgeBaseManager`: All filesystem and Git operations. Creates per-server repos, manages channel folders, appends thoughts, and performs `git commit` after every active command.
- **`mcp_bridge.py`** — MCP integration. `sync_cli_settings()` renders MCP servers into Gemini CLI's settings.json. `MCPSessionPool` manages live `ClientSession`s for the dialogue brain via AFC.
- **`config.py`** — Thin YAML loader (`config.yaml` at repo root). Exposes all settings as module-level constants. Secrets stay in env vars.
- **`logger.py`** — `MindSpaceLogger`: Triple-output logger (console + file + Discord `#system-log` channel), each with independent configurable levels.

### Design Principles

- **Tool-first architecture**: all structured bot behaviors (KB retrieval, thought recording, side-effects) are expressed as typed tool calls, not in-band prompt conventions. See `Agent/design.md` section 5.0 for rationale.
- **Tools-first dialogue**: the dialogue brain receives NO pre-loaded KB context. The model must call `search_channel_knowledge_base` to retrieve data. This keeps prompts lean and ensures tool progress UI is exercised.

### LLM Brain Selection

Two brains run in parallel, each specialized for its role:

- **Dialogue brain** — `GoogleGenAIBrain` (Google GenAI SDK). Passive chat, URL/file analysis, commit messages. Uses AFC (Automatic Function Calling) for tool dispatch including MCP sessions.
- **Command brain** — `GeminiCLIBrain` (Gemini CLI `gemini -y`). `!organize`, `!research`, `!omni`. Web search, file I/O, multi-step agentic loops. Config isolated via `GEMINI_CLI_HOME=Thought/bot-home`; workspace sandboxed via `cwd`.

### OpenViking & PageIndex

- **OpenViking** (`viking.py`): Semantic vector search. Indexes all `.md` files in `Channels/` into a vector DB at `Thought/openviking/`. Config at `Thought/ov.conf` (loaded via `OPENVIKING_CONFIG_FILE` env var set in code). Provides channel-scoped context for passive dialogue and global context for `!omni`.
- **PageIndex** (`pageindex_manager.py`): Cloud service at `api.pageindex.ai`. Deep PDF document reasoning — uploads PDFs, builds tree structures, enables Q&A. Used during file ingestion (PDF) and `!research`. Requires `PAGEINDEX_API_KEY`.

Install: `pip install openviking pageindex`

### Discord Commands

For a full list of commands (`!organize`, `!research`, `!change_my_view`, etc.) and detailed file ingestion workflows, refer to `docs/design.md` and `docs/help.md`.

## Configuration

All non-secret config lives in `config.yaml` at the repo root. Secrets stay in env vars.

```yaml
log:
  stream_level: DEBUG       # console — DEBUG | INFO | WARNING | ERROR
  file_level: DEBUG         # file — rotates daily, keeps 3 days
  discord_level: INFO       # Discord #system-log channel
  file_path: ~/logs/MindSpace/mindspace.log

storage:
  base_path: /home/yolo/repos/Thought

brains:
  dialogue_type: GoogleGenAISdk
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
config.yaml                    
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
