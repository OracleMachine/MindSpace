import json
import os
import yaml
from enum import Enum

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Config resolution order (first hit wins):
#   1. MINDSPACE_CONFIG — absolute path, wins unconditionally (escape hatch for
#      tests / one-off overrides).
#   2. MINDSPACE_PROFILE — profile name; loads profiles/<name>.yaml at repo root.
#   3. profiles/default.yaml — the committed default profile.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_PROFILES_DIR = os.path.join(_REPO_ROOT, "profiles")
_PROFILE = os.environ.get("MINDSPACE_PROFILE", "default")
_CONFIG_PATH = os.environ.get(
    "MINDSPACE_CONFIG",
    os.path.join(_PROFILES_DIR, f"{_PROFILE}.yaml"),
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

class Credentials:
    # Each profile is self-contained: credentials live inline under `credentials:`.
    # No env-var fallback — a profile that omits a token is an error, not a
    # "use the ambient shell's token instead" situation. This avoids the
    # footgun where a misconfigured profile silently inherits the previous
    # agent's credentials.
    _creds = _cfg.get("credentials", {})
    DISCORD_TOKEN = _creds.get("discord_token")
    GEMINI_API_KEY = _creds.get("gemini_api_key")


class Storage:
    _storage = _cfg.get("storage", {})
    # `~` is expanded here so profiles (and the default) can use the portable
    # form; downstream `Paths.*` all concatenate on this value, so they
    # inherit the expansion without each needing to call expanduser.
    BASE_PATH = os.path.expanduser(_storage.get("base_path", "~/repos/Thought"))
    VIKING_URI_PREFIX = _storage.get("openviking_uri_prefix", "viking://")
    IGNORED_EXTENSIONS = _storage.get("ignored_extensions", ["pdf"])

class Brains:
    _brains = _cfg.get("brains", {})
    DIALOGUE_TYPE = _brains.get("dialogue_type", BrainType.GOOGLE_SDK)
    COMMAND_TYPE = _brains.get("command_type", BrainType.GEMINI_CLI)
    
    ENABLE_GOOGLE_SEARCH = _brains.get("enable_google_search")
    if ENABLE_GOOGLE_SEARCH is None:
        raise ValueError(f"Configuration error: 'brains.enable_google_search' must be explicitly set to true or false in {_CONFIG_PATH}.")
        
    GEMINI_SDK_MODEL = _brains.get("gemini_sdk_model")
    if GEMINI_SDK_MODEL is None:
        raise ValueError(f"Configuration error: 'brains.gemini_sdk_model' must be explicitly set in {_CONFIG_PATH}.")
        
    GEMINI_CLI_MODEL = _brains.get("gemini_cli_model")
    if GEMINI_CLI_MODEL is None:
        raise ValueError(f"Configuration error: 'brains.gemini_cli_model' must be explicitly set in {_CONFIG_PATH} (e.g., 'auto-gemini-3').")

class Conversation:
    _conv = _cfg.get("conversation", {})
    HISTORY_MAX_CHARS = _conv.get("history_max_chars", 8000)

class MCP:
    _mcp = _cfg.get("mcp", {})
    SERVERS: dict = _expand_env(_mcp.get("servers", {}))

class Paths:
    CHANNELS = os.path.join(Storage.BASE_PATH, "Channels")
    # Gemini CLI reads $GEMINI_CLI_HOME/.gemini/, so setting this to the KB
    # root means the bot and a human running `gemini` inside the same
    # directory share one isolated home at `<KB>/.gemini/`. Previously the
    # bot used a separate `<KB>/bot-home/` which created a parallel, less
    # discoverable Gemini home alongside the human-visible one.
    GEMINI_CLI_HOME = Storage.BASE_PATH
    # The two Viking paths below are assigned post-class from the per-profile
    # cache dir (`~/.cache/mindspace/<profile>/`). Vector DB and rendered
    # config live alongside the profile's other runtime state — off the KB
    # so the knowledge directory holds only knowledge.
    VIKING_DATA: str
    VIKING_CONF: str

# Finalize Log Path (relative paths resolve under Storage.BASE_PATH)
if not os.path.isabs(Log.FILE_PATH):
    Log.FILE_PATH = os.path.join(Storage.BASE_PATH, Log.FILE_PATH)


# --- Per-profile runtime cache: vector DB + rendered OpenViking config ------
# Everything under `~/.cache/mindspace/<profile>/` is per-agent runtime state
# that MindSpace owns:
#   - `openviking/` — OpenViking's vector DB, rebuilt from `Channels/` when
#     missing; churns on every run, so it does not belong inside the KB git
#     repo where it would bloat history with compaction noise.
#   - `ov.conf` — JSON rendered from the profile's inline `openviking:`
#     section. The SDK reads it via OPENVIKING_CONFIG_FILE (set in main.py).
#     `${VAR}` tokens inside the section are substituted from os.environ by
#     `_expand_env` — same behavior as MCP headers.
_openviking_cfg = _expand_env(_cfg.get("openviking", {}))
if not _openviking_cfg:
    raise ValueError(
        f"Configuration error: 'openviking' section is required in {_CONFIG_PATH}. "
        "Move your OpenViking config inline into the profile YAML (the bot no "
        "longer reads a separate ov.conf file from the knowledge base directory)."
    )
_profile_stem = os.path.splitext(os.path.basename(_CONFIG_PATH))[0]
_profile_cache_dir = os.path.expanduser(f"~/.cache/mindspace/{_profile_stem}")
os.makedirs(_profile_cache_dir, exist_ok=True)
Paths.VIKING_DATA = os.path.join(_profile_cache_dir, "openviking")
Paths.VIKING_CONF = os.path.join(_profile_cache_dir, "ov.conf")
with open(Paths.VIKING_CONF, "w") as _f:
    json.dump(_openviking_cfg, _f, indent=2)
del _openviking_cfg, _profile_stem, _profile_cache_dir, _f

# (End of config)
