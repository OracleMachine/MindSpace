import os
import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_CONFIG_PATH = os.environ.get(
    "MINDSPACE_CONFIG",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml"),
)

with open(_CONFIG_PATH, "r") as f:
    _cfg = yaml.safe_load(f) or {}

# --- Logging ---
_log = _cfg.get("log", {})
LOG_STREAM_LEVEL = os.getenv("LOG_STREAM_LEVEL", _log.get("stream_level", "INFO")).upper()
LOG_FILE_LEVEL = os.getenv("LOG_FILE_LEVEL", _log.get("file_level", "DEBUG")).upper()
LOG_DISCORD_LEVEL = os.getenv("LOG_DISCORD_LEVEL", _log.get("discord_level", "INFO")).upper()
LOG_FILE_PATH = os.path.expanduser(_log.get("file_path", "~/logs/MindSpace/mindspace.log"))

# --- API Credentials (env-only; never in YAML) ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PAGEINDEX_API_KEY = os.getenv("PAGEINDEX_API_KEY")

# --- Storage & Knowledge Base ---
_storage = _cfg.get("storage", {})
BASE_STORAGE_PATH = _storage.get("base_path", "/home/yolo/repos/Thought")
OPENVIKING_URI_PREFIX = _storage.get("openviking_uri_prefix", "viking://")
IGNORED_EXTENSIONS = _storage.get("ignored_extensions", [".pdf"])

# --- Brains ---
_brains = _cfg.get("brains", {})
DIALOGUE_BRAIN_TYPE = _brains.get("dialogue_type", "GoogleGenAISdk")
COMMAND_BRAIN_TYPE = _brains.get("command_type", "gemini-cli")
GEMINI_SDK_MODEL = _brains.get("gemini_sdk_model", "gemini-3-flash-preview")
GEMINI_CLI_MODEL = _brains.get("gemini_cli_model", "auto-gemini-3")

# --- Conversation ---
_conv = _cfg.get("conversation", {})
CONVERSATION_HISTORY_MAX_CHARS = _conv.get("history_max_chars", 8000)

# --- MCP ---
def _expand_env(obj):
    """Recursively substitute ${VAR} / $VAR references in strings from os.environ."""
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    return obj

_mcp = _cfg.get("mcp", {})
MCP_SERVERS: dict = _expand_env(_mcp.get("servers", {}))

# --- Derived paths ---
OPENVIKING_DATA_PATH = os.path.join(BASE_STORAGE_PATH, "openviking")
CHANNELS_PATH = os.path.join(BASE_STORAGE_PATH, "Channels")
OPENVIKING_CONF_PATH = os.path.join(BASE_STORAGE_PATH, "ov.conf")
GEMINI_CLI_HOME_DIR = os.path.join(BASE_STORAGE_PATH, "bot-home")

if not os.path.isabs(LOG_FILE_PATH):
    LOG_FILE_PATH = os.path.join(BASE_STORAGE_PATH, LOG_FILE_PATH)
