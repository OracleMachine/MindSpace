# MindSpace: Quick Start Guide

This guide will help you set up and run your Hierarchical Knowledge Agent using **Gemini** and **PageIndex**.

## 1. Prerequisites

Ensure you have the following installed and configured on your machine:
- **Python 3.12+**
- **Knowledge Frameworks:**
  ```bash
  pip install openviking pageindex GitPython discord.py python-dotenv beautifulsoup4 requests litellm google-generativeai
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
Create a `.env` file in the project root or add these to your `~/.zshrc`:
```bash
export DISCORD_TOKEN="your_discord_bot_token"
export GEMINI_API_KEY="your_gemini_api_key"
export PAGEINDEX_API_KEY="your_pageindex_api_key"
```

### Step 3.2: Storage Configuration
The agent stores all knowledge in a local Git repository. Update `Agent/config.py` if needed:
```python
BASE_STORAGE_PATH = "/home/yolo/repos/Thought" # Main KB repository
```

### Step 3.3: OpenViking Configuration
The bot expects `ov.conf` inside your `BASE_STORAGE_PATH`.
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

Start the bot:
```bash
python3 Agent/bot.py
```

## 5. Usage & Commands

The bot supports both **Slash Commands** (`/command`) and **Prefix Commands** (`!command`).

### Core Commands

| Command | Description | Output |
| :--- | :--- | :--- |
| `!organize` | Scans the current channel folder for untracked files. Uses Gemini CLI to autonomously move them into semantic subfolders (e.g., `Research/`, `Notes/`). | Git Commit + Report |
| `!consolidate` | Synthesizes the cumulative `stream_of_conscious.md` into a structured, permanent Markdown article. Resets the stream after completion. | `ARTICLE-YYYY-MM-DD.md` |
| `!research [topic]` | Performs a deep-dive on a topic by querying both **OpenViking** (vector search) and **PageIndex** (document analysis). Generates a cited research paper. | `RESEARCH-YYYY-MM-DD.md` |
| `!omni [query]` | Cross-references the **entire knowledge base** (all channels) to answer broad queries with citations. | `OMNI-YYYY-MM-DD.md` |

### Knowledge Ingestion Workflows

1. **Active Dialogue**: Every message you send is analyzed. Key insights and fleeting thoughts are automatically extracted to the channel's `stream_of_conscious.md`.
2. **URL Snapshots**: Paste a URL (HTTP/HTTPS) to have the bot fetch the page content, clean it into Markdown, and save it to the channel folder as a `WEBPAGE-*.md` file.
3. **File Indexing**: Upload a PDF or Image. The bot saves it locally and uses **PageIndex** to index its content for future `!research` or `!omni` queries.
4. **Git Versioning**: Every major action (ingestion, organization, consolidation) triggers an automatic Git commit with an LLM-generated message, ensuring your knowledge base has full history and lineage.

## 6. Project Structure

- `Agent/bot.py`: Main Discord client and command router.
- `Agent/agent.py`: LLM "Brains" (Gemini SDK for chat, Gemini CLI for agentic tasks).
- `Agent/manager.py`: Knowledge Base & Git operations.
- `Agent/viking.py`: OpenViking vector search integration.
- `Agent/pageindex_manager.py`: PageIndex document analysis integration.
