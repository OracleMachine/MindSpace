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
```

## Architecture

MindSpace is a Discord bot that acts as a hierarchical knowledge agent. Philosophy: **Discord as the Input Stream, Filesystem as the Source of Truth.**

### Key Data Flow

1. Every Discord Server maps to a Git repo at `BASE_STORAGE_PATH` (`/home/yolo/repos/Thought`)
2. Each Discord channel maps to a folder inside that repo
3. Every channel folder always contains two core files:
   - `INDEX.MD` — high-level context map (passed to LLM for navigation)
   - `STREAM_OF_CONSCIOUS.MD` — running log of extracted thoughts

### Module Responsibilities

- **`bot.py`** — `MindSpaceBot(discord.Client)`: The entry point. Handles `on_message` and routes to commands (`!organize`, `!consolidate`, `!research`), URL ingestion, file ingestion, or passive dialogue.
- **`agent.py`** — `MindSpaceAgent`: Abstraction over LLM backends. Contains `GoogleGenAIBrain` (default, uses `google-genai` SDK) and `LiteLLMBrain`. Exposes `run_command()`, `engage_dialogue()`, `process_url()`, and `generate_commit_message()`.
- **`manager.py`** — `KnowledgeBaseManager`: All filesystem and Git operations. Creates per-server repos, manages channel folders, appends thoughts, and performs `git commit` after every active command.
- **`config.py`** — Centralized config. Key settings: `AGENT_BRAIN_TYPE` (`"sdk"` or `"litellm"`), `BASE_STORAGE_PATH`, `GEMINI_SDK_MODEL`.
- **`logger.py`** — `MindSpaceLogger`: Dual-output logger (console + Discord `#system-log` channel via async queue).

### LLM Brain Selection

Controlled by `config.AGENT_BRAIN_TYPE`:
- `"sdk"` (default): `GoogleGenAIBrain` — uses `google.genai` SDK directly
- `"litellm"`: `LiteLLMBrain` — uses LiteLLM for multi-provider support

### OpenViking & PageIndex (Planned)

Per `design.md`, these frameworks are **not yet integrated** but are specified for:
- **OpenViking**: Context mapping — manages the `viking://` URI space; used to provide L0/L1 overview layers to Gemini so it can navigate the KB tree without reading every file. URI prefix `viking://` is already reserved in `config.OPENVIKING_URI_PREFIX`.
- **PageIndex**: Deep document parsing — used during file ingestion to parse documents into reasoning trees and during `!research` to find specific facts across the KB.

Currently, both roles are fulfilled by passing raw file contents directly to the LLM. Install with: `pip install openviking pageindex`

### Discord Commands

| Command | Behavior |
|---|---|
| `!organize` | Scans untracked files, semantically reorganizes, git commits |
| `!consolidate` | Synthesizes `STREAM_OF_CONSCIOUS.MD` into a dated article, clears stream, sends file to Discord |
| `!research [topic]` | Generates a cited research paper using KB context, saves and sends to Discord |
| URL in message | Fetches page, converts to Markdown snapshot, git commits |
| File attachment | Saves and analyzes file, git commits |
| Plain text | Passive dialogue: replies + silently extracts `THOUGHT:` block to `STREAM_OF_CONSCIOUS.MD` |
