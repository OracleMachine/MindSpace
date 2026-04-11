import os

# --- 1. Environment Loading ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- 2. API Credentials ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PAGEINDEX_API_KEY = os.getenv("PAGEINDEX_API_KEY")

# --- 3. Storage & Knowledge Base Configuration ---
BASE_STORAGE_PATH = "/home/yolo/repos/Thought"
OPENVIKING_URI_PREFIX = "viking://"

# --- 4. Agent & LLM (Brain) Configuration ---
# DIALOGUE_BRAIN_TYPE: brain for passive chat, URL/file analysis, commit messages
# Options: "GoogleGenAISdk" (recommended) | "litellm" (LiteLLMBrain)
DIALOGUE_BRAIN_TYPE = "GoogleGenAISdk"

# COMMAND_BRAIN_TYPE: brain for !organize, !consolidate, !research, !omni
# Gemini CLI adds web search, file I/O, and multi-step agentic loops beyond the API.
COMMAND_BRAIN_TYPE = "gemini-cli"

# GEMINI_SDK_MODEL: The model identifier for the new Google GenAI SDK.
# Use the official preview name for Gemini 3 Flash.
GEMINI_SDK_MODEL = "gemini-3-flash-preview"

# LITELLM_MODEL: The model identifier for LiteLLM.
LITELLM_MODEL = "gemini/gemini-3-flash-preview"

# GEMINI_CLI_MODEL: Model passed to `gemini -m` for !organize, !research, !omni.
# - "auto-gemini-3" → the CLI's "Auto (Gemini 3)" picker; lets Gemini CLI choose
#   the best Gemini 3 variant at runtime (recommended — avoids capacity 429s
#   by letting the CLI fall back across flash/pro variants internally).
# - Explicit names like "gemini-3-flash-preview" or "gemini-2.5-pro" also work.
# - None → no -m flag; CLI falls back to its hardcoded default.
GEMINI_CLI_MODEL = "auto-gemini-3"

# CONVERSATION_HISTORY_LIMIT: Number of recent turns (user + assistant pairs) to keep in memory per channel.
CONVERSATION_HISTORY_MAX_CHARS = 8000  # max characters of history injected into context

# OPENVIKING_DATA_PATH: Where OpenViking stores its vector DB (inside the KB repo).
OPENVIKING_DATA_PATH = os.path.join(BASE_STORAGE_PATH, "openviking")
CHANNELS_PATH = os.path.join(BASE_STORAGE_PATH, "Channels")
OPENVIKING_CONF_PATH = os.path.join(BASE_STORAGE_PATH, "ov.conf")

# GEMINI_CLI_HOME_DIR: Isolated home dir for the Gemini CLI subprocess.
# When passed as GEMINI_CLI_HOME env var, the CLI reroots ~/.gemini/ here
# instead of the real user home. This gives the bot its own settings file
# (no shared hooks/notifications/telemetry with the user's interactive CLI)
# while auth still works via symlinked oauth_creds.json. gitignored.
GEMINI_CLI_HOME_DIR = os.path.join(BASE_STORAGE_PATH, "bot-home")
