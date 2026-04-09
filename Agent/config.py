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
# AGENT_BRAIN_TYPE: 
# - "sdk": Use the new google-genai SDK (recommended)
# - "litellm": Use LiteLLM for multi-provider support
AGENT_BRAIN_TYPE = "sdk"

# GEMINI_SDK_MODEL: The model identifier for the new Google GenAI SDK.
# Use the official preview name for Gemini 3 Flash.
GEMINI_SDK_MODEL = "gemini-3-flash-preview"

# LITELLM_MODEL: The model identifier for LiteLLM.
LITELLM_MODEL = "gemini/gemini-3-flash-preview"

# CONVERSATION_HISTORY_LIMIT: Number of recent turns (user + assistant pairs) to keep in memory per channel.
CONVERSATION_HISTORY_MAX_CHARS = 8000  # max characters of history injected into context

# OPENVIKING_DATA_PATH: Where OpenViking stores its vector DB (separate from the KB itself).
OPENVIKING_DATA_PATH = "/home/yolo/repos/OpenVikingData"
