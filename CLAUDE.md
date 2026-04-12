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

- **`bot.py`** — `MindSpaceBot(discord.Client)`: The entry point. Handles `on_message` and routes to commands (`!organize`, `!consolidate`, `!research`, `!omni`), URL ingestion, file ingestion, or passive dialogue. Wraps tools with async progress decorators for Discord status updates.
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

| Command | Behavior |
|---|---|
| `!organize` | Scans untracked files, semantically reorganizes, git commits |
| `!consolidate` | Synthesizes `stream_of_conscious.md` into a dated article, clears stream, sends file to Discord |
| `!research [topic]` | Generates a cited research paper using KB context, saves and sends to Discord |
| `!omni [query]` | Cross-KB synthesis across all channel folders, saves and sends to Discord |
| URL in message | Fetches page, converts to Markdown snapshot, git commits |
| File attachment | Saves and analyzes file, git commits |
| Plain text | Passive dialogue: replies via tool-based KB retrieval + records insights via `record_thought` tool |

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
  gemini_sdk_model: gemini-3-flash-preview
  gemini_cli_model: auto-gemini-3

conversation:
  history_max_chars: 8000
```

## MCP Server Setup

MCP (Model Context Protocol) servers extend the bot with external tool capabilities (web search, database queries, document reasoning, etc.). Both brains consume configured MCP servers:

- **Dialogue brain**: live `ClientSession`s passed to AFC alongside local tools.
- **Command brain**: servers rendered into Gemini CLI's `settings.json` automatically.

### 1. Add server(s) to `config.yaml`

```yaml
mcp:
  servers:
    wisburg-mcp-server:
      url: https://mcp.wisburg.com/mcp
      headers:
        Authorization: Bearer ${WISBURG_MCP_TOKEN}

    another-server:
      url: https://example.com/mcp
      headers:
        X-Api-Key: ${ANOTHER_MCP_KEY}
```

Each server needs a `url` (streamable HTTP endpoint). `headers` is optional — used for auth tokens. `${ENV_VAR}` references are expanded from the environment at startup (secrets stay out of the checked-in YAML).

### 2. Export the secret(s)

```bash
# Add to ~/.zshrc
export WISBURG_MCP_TOKEN="your_token_here"
export ANOTHER_MCP_KEY="your_key_here"
```

### 3. Install the MCP package

```bash
pip install mcp
```

Required for the dialogue brain's live session pool. The command brain (Gemini CLI) handles MCP natively and doesn't need this package.

### 4. Start the bot

```bash
cd Agent
python3 bot.py
```

On startup you'll see:
- `MCP: synced N server(s) into .../bot-home/.gemini/settings.json` — command brain ready.
- `MCP: connected -> wisburg-mcp-server (...) -- 12 tool(s)` — dialogue brain sessions live.
- `Preflight: MCP -- wisburg-mcp-server exposes 12 tool(s)` — health check passed.

### 5. Verify

- **Preflight probe**: the bot connects to each configured server at startup and logs how many tools it exposes. Failures are warnings, not fatal — a transient MCP outage won't prevent the bot from starting.
- **Dialogue**: ask a question that would benefit from an MCP tool. The Discord status message will show `🌐 MCP ``server-name``: ``tool_name``` when an MCP tool is invoked.
- **Commands**: run `!research [topic]`. The Gemini CLI discovers MCP tools from `settings.json` and uses them in its agentic loop.

### How it works

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
