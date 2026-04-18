# MindSpace: Quick Start Guide

This guide will help you set up and run your Hierarchical Knowledge Agent using **Gemini** and **PageIndex**.

## 1. Prerequisites

Ensure you have the following installed and configured on your machine:
- **Python 3.12+**
- **Dependencies:**
  ```bash
  pip install discord.py google-genai openviking pageindex GitPython httpx beautifulsoup4 pyyaml python-dotenv
  ```
- **Gemini CLI:** Installed and authenticated (`gemini login`).
- **Google AI Studio:** Obtain a Gemini API Key from [aistudio.google.com](https://aistudio.google.com/).
- **Git:** Configured with your user name and email.

## 2. Discord Bot Setup

### Step 2.1: Create Application
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** and name it (e.g., "MindSpace Agent").
3. In the **Bot** tab:
   - Enable **Message Content Intent**.
   - Click **Reset Token** to get your `DISCORD_TOKEN`.

### Step 2.2: OAuth2 URL Generator
1. Go to **OAuth2** -> **URL Generator**.
2. Select Scopes: `bot`, `applications.commands`.
3. Select Bot Permissions: `Send Messages`, `Attach Files`, `Read Message History`, `Manage Messages`.
4. Invite the bot to your dedicated Discord Server.

## 3. Local Configuration

### Step 3.1: Environment Variables
Add these to your `~/.zshrc` (or create a `.env` file in the project root):
```bash
export DISCORD_TOKEN="your_discord_bot_token"
export GEMINI_API_KEY="your_gemini_api_key"
export PAGEINDEX_API_KEY="your_pageindex_api_key"
```

### Step 3.2: `config.yaml`
All non-secret config lives in `config.yaml` at the repo root:
```yaml
log:
  stream_level: DEBUG       # console — DEBUG | INFO | WARNING | ERROR
  file_level: DEBUG         # file — rotates daily, keeps 3 days
  discord_level: INFO       # Discord #system-log channel
  file_path: ~/logs/MindSpace/mindspace.log

storage:
  base_path: /home/yolo/repos/Thought    # where the knowledge base lives
  ignored_extensions: ["pdf", "jpg"]    # extensions to skip during OpenViking indexing

brains:
  dialogue_type: GoogleGenAISdk
  command_type: gemini-cli
  gemini_sdk_model: gemini-3-flash-preview
  gemini_cli_model: auto-gemini-3

conversation:
  history_max_chars: 8000
```

### Step 3.3: OpenViking Configuration
The bot expects `ov.conf` inside your `storage.base_path` (e.g. `Thought/ov.conf`):
```json
{
  "embedding": {
    "dense": {
      "provider": "openai",
      "api_key": "your-key",
      "model": "text-embedding-3-large",
      "dimension": 1024
    }
  }
}
```

## 4. Running the Agent

```bash
cd Agent
python3 bot.py
```

The bot runs a preflight check (PageIndex, OpenViking, GitPython, API keys) before connecting to Discord. If anything is missing it will tell you.

## 5. Usage & Commands

The bot supports both **Slash Commands** (`/command`) and **Prefix Commands** (`!command`).

### Core Commands

| Command | Description | Output |
| :--- | :--- | :--- |
| `!organize` | Scans the current channel folder for untracked files. Uses Gemini CLI to autonomously reorganize them into semantic subfolders. | Git Commit + Report |
| `!consolidate` | Synthesizes the cumulative `stream_of_conscious.md` into a structured, permanent Markdown article. Resets the stream. | `ARTICLE-YYYY-MM-DD-subject.md` |
| `!research [topic]` | Deep-dive on a topic by querying **OpenViking** (vector search) and **PageIndex** (document analysis). Generates a cited research paper. | `RESEARCH-YYYY-MM-DD-subject.md` |
| `!omni [query]` | Cross-references the **entire knowledge base** (all channels) to answer broad queries with citations. | `OMNI-YYYY-MM-DD-subject.md` |

### Knowledge Ingestion Workflows

1. **Active Dialogue (Tool-First)**: The dialogue brain has no pre-loaded KB context. When you ask a factual question, the model calls `search_channel_knowledge_base` to retrieve relevant data from OpenViking. Insights are recorded via the `record_thought` tool call (not parsed from the reply). Progress is shown live in a Discord status message.
2. **URL Ingestion**: Paste a URL (HTTP/HTTPS) and the bot will remind you to paste the content manually for ingestion.
3. **File Indexing**: Upload a PDF or other file. The bot saves it locally and uses **PageIndex** to index its content for future `!research` or `!omni` queries.
4. **Git Versioning**: Every major action (ingestion, organization, consolidation) triggers an automatic Git commit with an LLM-generated message.

## 6. MCP Server Setup (Optional)

MCP (Model Context Protocol) extends the bot with external tool capabilities — web search, database queries, document reasoning, etc. Both brains consume configured MCP servers automatically.

### Step 6.1: Add servers to `config.yaml`

```yaml
mcp:
  servers:
    wisburg-mcp-server:
      url: https://mcp.wisburg.com/mcp
      headers:
        Authorization: Bearer ${WISBURG_MCP_TOKEN}

    # Add as many servers as needed:
    another-server:
      url: https://example.com/mcp
      headers:
        X-Api-Key: ${ANOTHER_MCP_KEY}
```

- `url` — the server's streamable HTTP endpoint (required).
- `headers` — optional, typically for auth tokens.
- `${ENV_VAR}` — expanded from the environment at startup, so secrets stay out of the checked-in YAML.

### Step 6.2: Export the secrets

```bash
# Add to ~/.zshrc
export WISBURG_MCP_TOKEN="your_token_here"
export ANOTHER_MCP_KEY="your_key_here"
```

### Step 6.3: Install the MCP package

```bash
pip install mcp
```

Required for the dialogue brain's live session pool. The command brain (Gemini CLI) handles MCP natively without this package.

### Step 6.4: Restart the bot

On startup you should see:

```
MCP: synced 2 server(s) into .../bot-home/.gemini/settings.json
MCP: connected -> wisburg-mcp-server (https://mcp.wisburg.com/mcp) -- 12 tool(s)
MCP: connected -> another-server (https://example.com/mcp) -- 5 tool(s)
Preflight: MCP -- wisburg-mcp-server exposes 12 tool(s)
Preflight: MCP -- another-server exposes 5 tool(s)
Preflight: MCP -- 2/2 servers reachable
```

### Step 6.5: Verify

- **Dialogue**: ask a question that would benefit from an MCP tool. The Discord status message shows `🌐 MCP server-name: tool_name` when an MCP tool is invoked.
- **Commands**: run `!research [topic]`. The Gemini CLI discovers MCP tools from its settings.json automatically.
- **Failures are non-fatal**: if an MCP server is unreachable, the bot logs a warning and continues without it.

### How MCP flows through the system

```
config.yaml
  mcp.servers: {name: {url, headers}}
       |
       +---> config.py: MCP_SERVERS (env vars expanded)
       |
       +---> mcp_bridge.sync_cli_settings()
       |       \--> bot-home/.gemini/settings.json  --> Command brain (Gemini CLI)
       |
       \---> mcp_bridge.MCPSessionPool.connect()
               \--> live ClientSession per server   --> Dialogue brain (AFC)
```

## 7. Project Structure

| File | Role |
| :--- | :--- |
| `config.yaml` | All non-secret configuration (log levels, storage path, brains, MCP servers) |
| `Agent/bot.py` | Main Discord client, command routing, tool wrapping with progress UI |
| `Agent/agent.py` | LLM brains — `GoogleGenAIBrain` (dialogue/AFC) + `GeminiCLIBrain` (commands) |
| `Agent/tools.py` | Tool functions for dialogue (search KB, list files, record thought) |
| `Agent/manager.py` | Knowledge Base filesystem + Git operations |
| `Agent/mcp_bridge.py` | MCP integration — CLI settings sync + live session pool |
| `Agent/viking.py` | OpenViking vector search integration |
| `Agent/pageindex_manager.py` | PageIndex document analysis integration |
| `Agent/config.py` | YAML loader, exposes settings as module-level constants |
| `Agent/logger.py` | Triple-output logger (console + file + Discord) |
| `Agent/design.md` | Full architecture specification |
