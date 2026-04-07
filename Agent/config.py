import os

from dotenv import load_dotenv
load_dotenv()

# --- 2. API Credentials ---
# These should be set in your environment (e.g., ~/.zshrc or .env)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- 3. Storage & Knowledge Base Configuration ---
# BASE_STORAGE_PATH: The root directory where individual server KBs (Git repos) are stored.
# By default, it uses 'Thought' as the storage name for the MindSpace system.
BASE_STORAGE_PATH = "/home/yolo/repos/Thought"

# OPENVIKING_URI_PREFIX: Protocol used for context mapping and citations (e.g., viking://Server/Channel/File)
OPENVIKING_URI_PREFIX = "viking://"

# --- 4. Agent & LLM (Brain) Configuration ---
# AGENT_BRAIN_TYPE: Choose how the agent processes information.
# - "cli": Use the standalone 'gemini' CLI.
# - "litellm": Use the LiteLLM library for seamless model swapping (default).
AGENT_BRAIN_TYPE = "litellm" 

# GEMINI_CLI_COMMAND: The command name/path for the Gemini CLI.
GEMINI_CLI_COMMAND = "gemini"

# GEMINI_CLI_HOME: Local configuration and state for Gemini CLI to isolate from global config.
# This will be used as the GEMINI_CLI_HOME environment variable.
# It resolves to .gemini_home in the project root.
GEMINI_CLI_HOME = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".gemini_home"))

# LITELLM_MODEL: The model identifier to use when AGENT_BRAIN_TYPE is "litellm".
LITELLM_MODEL = "gemini/gemini-3-flash"

# PAGEINDEX_MODEL: Specialized model for deep document parsing and indexing.
PAGEINDEX_MODEL = "gemini/gemini-3-flash"
