# MindSpace: Quick Start Guide

This guide will help you set up and run your Hierarchical Knowledge Agent, powered by **Gemini**.

## 1. Prerequisites

Ensure you have the following installed and configured on your machine:
- **Python 3.12+**
- **Dependencies:**
  ```bash
  pip install discord.py google-genai openviking GitPython pyyaml python-dotenv
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

### Step 3.1: Create your profile
Every profile is self-contained: tokens, model settings, MCP servers — all inline. One profile per bot. All `profiles/*.yaml` are gitignored, so real tokens never get committed.

Create `profiles/my-agent.yaml` with at least the `credentials:` block:
```yaml
credentials:
  discord_token: "your_discord_bot_token"    # unique per agent
  gemini_api_key: "your_gemini_api_key"
```

There is **no** `agent.name` field. The bot's display name comes straight from Discord (`self.user.display_name` after login) and is injected into the dialogue prompt, so whatever you named the bot in the Discord Developer Portal is what the LLM calls itself — and it matches the author label Discord puts on the bot's own messages in channel history.

### Step 3.2: Profile config reference
The rest of the profile controls logging, storage, brains, and MCP. Full template:
```yaml
log:
  stream_level: DEBUG       # console — DEBUG | INFO | WARNING | ERROR
  file_level: DEBUG         # file — rotates daily, keeps 3 days
  discord_level: INFO       # Discord #console channel
  file_path: ~/logs/MindSpace/mindspace.log

storage:
  base_path: ~/repos/Thought    # where the knowledge base lives
  ignored_extensions: ["pdf", "jpg"]    # extensions to skip during OpenViking indexing

brains:
  dialogue_type: GoogleGenAISdk
  command_type: gemini-cli
  enable_google_search: true
  gemini_sdk_model: gemini-3.1-flash-lite-preview
  gemini_cli_model: auto-gemini-3

conversation:
  history_max_chars: 8000
```

### Step 3.3: OpenViking Configuration (inline in profile)
OpenViking config lives in the profile YAML under an `openviking:` section. At launch, `config.py` renders it to `~/.cache/mindspace/<profile>/ov.conf` and points `OPENVIKING_CONFIG_FILE` there — so the SDK still reads a file on disk, but the source of truth is your profile.

Use a YAML anchor on `credentials.gemini_api_key` to avoid duplicating the key:
```yaml
credentials:
  discord_token: "..."
  gemini_api_key: &gemini_api_key "..."

openviking:
  embedding:
    dense:
      provider: gemini
      model: models/gemini-embedding-2-preview
      api_key: *gemini_api_key
      dimension: 768
  vlm:
    provider: gemini
    model: gemini-3-flash-preview
    api_key: *gemini_api_key
```

Any `${VAR}` placeholder inside the `openviking:` section is expanded from the environment at load time, so you can use env-var references if you prefer.

## 4. Running the Agent

```bash
./profiles/run.sh my-agent              # loads profiles/my-agent.yaml
./profiles/run.sh my-agent.yaml         # filename form also works
./profiles/run.sh /abs/path/myagent.yaml  # explicit path also accepted
```

One process per agent. To run multiple bots concurrently, open multiple shells (or use your process manager of choice) and launch each with its own profile.

The launcher runs a preflight check (OpenViking, GitPython, credentials) before connecting to Discord. If anything is missing it will tell you.

## 5. Usage & Commands

The bot supports both **Slash Commands** (`/command`) and **Prefix Commands** (`!command`).

### Core Commands

| Command | Description | Output |
| :--- | :--- | :--- |
| `!consolidate` | Synthesizes the cumulative `stream_of_conscious.md` into a structured, permanent Markdown article. Resets the stream. | `ARTICLE-YYYY-MM-DD-subject.md` |
| `!research [topic]` | Deep-dive on a topic by querying **OpenViking** (vector search) plus web sources. Generates a cited research paper. (PDF deep-document Q&A is currently disabled.) | `RESEARCH-YYYY-MM-DD-subject.md` |
| `!omni [query]` | Cross-references the **entire knowledge base** (all channels) to answer broad queries with citations. | `OMNI-YYYY-MM-DD-subject.md` |
| `!change_my_view [instruction]` | Update the channel's root stance (`view.md`) via a reviewed proposal. Accepting also fires a downward consistency sweep that emits proposals for any subfolder view that now conflicts. | `view.md` |
| `!view_down_check` | Top-down sweep: re-challenge every subfolder's local view against its evidence, then check each descendant view against the channel-root stance. Catches drift the per-commit hook missed. | — |
| `!sync` | Manually rebuild the vector index for the current channel. | — |

### Knowledge Ingestion Workflows

1. **Active Dialogue (Tool-First)**: The dialogue brain has no pre-loaded KB context. When you ask a factual question, the model calls `search_channel_knowledge_base` to retrieve relevant data from OpenViking. Organic insights are recorded to the channel's stream-of-consciousness via `record_thought`. When you explicitly ask the bot to save / integrate / file something into the KB (e.g. "save your last reply", "整合进知识库"), or when it judges an insight belongs in a structured file, it calls `propose_update` and pops up a **reviewed proposal UI** (with a Git diff) right in the chat. Progress is shown live in a Discord status message.
2. **URL Ingestion**: Paste a URL (HTTP/HTTPS) and the bot will remind you to paste the content manually for ingestion.
3. **File Indexing**: Upload a PDF or other file. The bot saves it locally and indexes text-ish content into OpenViking for future `!research` or `!omni` queries. (PDF deep-document Q&A is currently disabled.)
4. **Git Versioning**: Every major action (ingestion, organization, consolidation) triggers an automatic Git commit with an LLM-generated message.

## 6. MCP Server Setup (Optional)

MCP (Model Context Protocol) extends the bot with external tool capabilities — web search, database queries, document reasoning, etc. Both brains consume configured MCP servers automatically.

### Step 6.1: Add servers to the active profile (`profiles/<name>.yaml`)

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
MCP: synced 2 server(s) into .../<KB>/.gemini/settings.json
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
profiles/<active>.yaml
  mcp.servers: {name: {url, headers}}
       |
       +---> config.py: MCP_SERVERS (env vars expanded)
       |
       +---> mcp_bridge.sync_cli_settings()
       |       \--> <KB>/.gemini/settings.json      --> Command brain (Gemini CLI)
       |
       \---> mcp_bridge.MCPSessionPool.connect()
               \--> live ClientSession per server   --> Dialogue brain (AFC)
```

## 7. Project Structure

For a complete breakdown of module responsibilities and system architecture, please refer to the `docs/design.md` file.
