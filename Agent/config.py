import os

# Project Configuration
# In the 'One Server = One Repo' model, the root is determined by the Discord Server Name.
# This base directory will contain the individual server repositories.
BASE_STORAGE_PATH = "/home/yolo/repos/Thought"

# Discord Token & Gemini API Key (Loaded from ZSH environment)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Framework status (For prototype, we use subprocess to call CLI tools)
GEMINI_CLI_COMMAND = "gemini"
OPENVIKING_URI_PREFIX = "viking://"
PAGEINDEX_MODEL = "gemini/gemini-3-flash"
