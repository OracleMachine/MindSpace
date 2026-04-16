import os
import yaml
from enum import Enum

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_CONFIG_PATH = os.environ.get(
    "MINDSPACE_CONFIG",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "config.yaml"),
)

with open(_CONFIG_PATH, "r") as f:
    _cfg = yaml.safe_load(f) or {}

def _expand_env(obj):
    """Recursively substitute ${VAR} / $VAR references in strings from os.environ."""
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    return obj

class BrainType(str, Enum):
    GOOGLE_SDK = "GoogleGenAISdk"
    LITELLM = "litellm"
    GEMINI_CLI = "gemini-cli"

class Log:
    _log = _cfg.get("log", {})
    STREAM_LEVEL = os.getenv("LOG_STREAM_LEVEL", _log.get("stream_level", "INFO")).upper()
    FILE_LEVEL = os.getenv("LOG_FILE_LEVEL", _log.get("file_level", "DEBUG")).upper()
    DISCORD_LEVEL = os.getenv("LOG_DISCORD_LEVEL", _log.get("discord_level", "INFO")).upper()
    FILE_PATH = os.path.expanduser(_log.get("file_path", "~/logs/MindSpace/mindspace.log"))

class Auth:
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    PAGEINDEX_API_KEY = os.getenv("PAGEINDEX_API_KEY")

class Storage:
    _storage = _cfg.get("storage", {})
    BASE_PATH = _storage.get("base_path", "/home/yolo/repos/Thought")
    VIKING_URI_PREFIX = _storage.get("openviking_uri_prefix", "viking://")
    IGNORED_EXTENSIONS = _storage.get("ignored_extensions", ["pdf"])

class Brains:
    _brains = _cfg.get("brains", {})
    DIALOGUE_TYPE = _brains.get("dialogue_type", BrainType.GOOGLE_SDK)
    COMMAND_TYPE = _brains.get("command_type", BrainType.GEMINI_CLI)
    GEMINI_SDK_MODEL = _brains.get("gemini_sdk_model", "gemini-3-flash-preview")
    GEMINI_CLI_MODEL = _brains.get("gemini_cli_model", "auto-gemini-3")

class Conversation:
    _conv = _cfg.get("conversation", {})
    HISTORY_MAX_CHARS = _conv.get("history_max_chars", 8000)

class MCP:
    _mcp = _cfg.get("mcp", {})
    SERVERS: dict = _expand_env(_mcp.get("servers", {}))

class Paths:
    VIKING_DATA = os.path.join(Storage.BASE_PATH, "openviking")
    CHANNELS = os.path.join(Storage.BASE_PATH, "Channels")
    VIKING_CONF = os.path.join(Storage.BASE_PATH, "ov.conf")
    GEMINI_CLI_HOME = os.path.join(Storage.BASE_PATH, "bot-home")

# Finalize Log Path (relative paths resolve under Storage.BASE_PATH)
if not os.path.isabs(Log.FILE_PATH):
    Log.FILE_PATH = os.path.join(Storage.BASE_PATH, Log.FILE_PATH)

# (End of config)
