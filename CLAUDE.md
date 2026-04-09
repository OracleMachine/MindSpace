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
3. Every channel folder contains `stream_of_conscious.md` — running log of extracted thoughts

### Repo Layout

```
Thought/
├── Channels/              ← channel folders (one per Discord channel)
│   ├── general/
│   └── oil-war-research/
├── openviking/            ← OpenViking vector DB data
└── ov.conf                ← OpenViking config (uses ${GEMINI_API_KEY} env var)
```

### Module Responsibilities

- **`bot.py`** — `MindSpaceBot(discord.Client)`: The entry point. Handles `on_message` and routes to commands (`!organize`, `!consolidate`, `!research`), URL ingestion, file ingestion, or passive dialogue.
- **`agent.py`** — `MindSpaceAgent`: Abstraction over LLM backends. Contains `GoogleGenAIBrain` (default, uses `google-genai` SDK) and `LiteLLMBrain`. Exposes `run_command()`, `engage_dialogue()`, `process_url()`, and `generate_commit_message()`.
- **`manager.py`** — `KnowledgeBaseManager`: All filesystem and Git operations. Creates per-server repos, manages channel folders, appends thoughts, and performs `git commit` after every active command.
- **`config.py`** — Centralized config. Key settings: `AGENT_BRAIN_TYPE`, `BASE_STORAGE_PATH`, `CHANNELS_PATH`, `OPENVIKING_DATA_PATH`, `OPENVIKING_CONF_PATH`.
- **`logger.py`** — `MindSpaceLogger`: Dual-output logger (console + Discord `#system-log` channel via async queue).

### LLM Brain Selection

Controlled by `config.AGENT_BRAIN_TYPE`:
- `"sdk"` (default): `GoogleGenAIBrain` — uses `google.genai` SDK directly
- `"litellm"`: `LiteLLMBrain` — uses LiteLLM for multi-provider support

### OpenViking & PageIndex

- **OpenViking** (`viking.py`): Semantic vector search. Indexes all `.md` files in `Channels/` into a vector DB at `Thought/openviking/`. Config at `Thought/ov.conf` (loaded via `OPENVIKING_CONFIG_FILE` env var set in code). Provides channel-scoped context for passive dialogue and global context for `!omni`.
- **PageIndex** (`pageindex_manager.py`): Cloud service at `api.pageindex.ai`. Deep PDF document reasoning — uploads PDFs, builds tree structures, enables Q&A. Used during file ingestion (PDF) and `!research`. Requires `PAGEINDEX_API_KEY`.

Install: `pip install openviking pageindex`

### Discord Commands

| Command | Behavior |
|---|---|
| `!organize` | Scans untracked files, semantically reorganizes, git commits |
| `!consolidate` | Synthesizes `STREAM_OF_CONSCIOUS.MD` into a dated article, clears stream, sends file to Discord |
| `!research [topic]` | Generates a cited research paper using KB context, saves and sends to Discord |
| URL in message | Fetches page, converts to Markdown snapshot, git commits |
| File attachment | Saves and analyzes file, git commits |
| Plain text | Passive dialogue: replies + silently extracts `THOUGHT:` block to `STREAM_OF_CONSCIOUS.MD` |
