# MindSpace: Quick Start Guide

This guide will help you set up and run your Hierarchical Knowledge Agent using **Gemini** and **PageIndex**.

## 1. Prerequisites

Ensure you have the following installed and configured on your machine:
- **Python 3.12+**
- **Knowledge Frameworks:**
  ```bash
  pip install openviking pageindex
  ```
- **Gemini CLI:** Installed and authenticated (`gemini login`).
- **Google AI Studio:** Obtain a Gemini API Key from [aistudio.google.com](https://aistudio.google.com/).
- **Git:** Configured with your user name and email.

## 2. Discord Bot Setup

To interact with the agent, you must create a Discord Bot in the [Discord Developer Portal](https://discord.com/developers/applications).

### Step 2.1: Create Application
1. Click **New Application** and give it a name (e.g., "MindSpace Agent").
2. Go to the **Bot** tab.
3. **Crucial:** Enable **Message Content Intent** under the "Privileged Gateway Intents" section.
4. Click **Reset Token** to generate your `DISCORD_TOKEN`. Save this for later.

### Step 2.2: OAuth2 URL Generator
1. Go to **OAuth2** -> **URL Generator**.
2. Select Scopes: `bot`.
3. Select Bot Permissions: `Send Messages`, `Attach Files`, `Read Message History`, `Manage Messages`.
4. Invite the bot to your dedicated Discord Server.

## 3. Local Configuration

### Step 3.1: ZSH Environment Variables
Add the following to your `~/.zshrc` file:
```bash
export DISCORD_TOKEN="your_discord_bot_token"
export GEMINI_API_KEY="your_gemini_api_key_from_ai_studio"
export PAGEINDEX_API_KEY="your_pageindex_api_key"  # from pageindex.ai
```
Then, reload your shell:
```bash
source ~/.zshrc
```

### Step 3.2: Config.py
Verify the paths in `config.py`:
```python
BASE_STORAGE_PATH = "~/repos/Thought"       # your knowledge base
OPENVIKING_DATA_PATH = "~/repos/OpenVikingData"  # OpenViking vector DB
```

### Step 3.3: OpenViking Configuration
OpenViking requires its own config file with embedding and vision model credentials.

Create `~/.openviking/ov.conf`:
```json
{
  "embedding": {
    "dense": {
      "api_base": "<your-endpoint>",
      "api_key": "<your-key>",
      "provider": "openai",
      "dimension": 1024,
      "model": "<embedding-model-name>"
    }
  },
  "vlm": {
    "api_base": "<your-endpoint>",
    "api_key": "<your-key>",
    "provider": "openai",
    "model": "<vision-model-name>"
  }
}
```

Supports `volcengine`, `openai`, and OpenAI-compatible providers.

## 4. Running the Agent

Start the bot process:
```bash
python3 bot.py
```

Upon successful login, the bot will initialize a local Git repository for your Discord Server (e.g., `/Project_Alpha/`).

## 5. Basic Usage

- **Active Dialogue:** Chat naturally; the agent replies and extracts insights to `stream_of_conscious.md`.
- **File Ingestion:** Drop a PDF or image for semantic filing.
- **Link Ingestion:** Paste a URL to snapshot the webpage as Markdown.
- **`!organize`**: Sync files and optimize the tree.
- **`!consolidate`**: Synthesize chat thoughts into a permanent article.
- **`!research [topic]`**: Generate a cited deep-dive paper.
