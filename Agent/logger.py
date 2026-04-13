import logging
import logging.handlers
import os

import config

# --- Standard Logging Setup ---
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR

_STREAM_LEVEL = getattr(logging, config.Log.STREAM_LEVEL, logging.INFO)
_FILE_LEVEL = getattr(logging, config.Log.FILE_LEVEL, logging.DEBUG)
_DISCORD_LEVEL = getattr(logging, config.Log.DISCORD_LEVEL, logging.INFO)

# Format/datefmt only — no `level` kwarg so root logger's level stays at
# Python's default (WARNING), keeping third-party libs quiet.
logging.basicConfig(
    format='%(asctime)s [%(levelname)s] %(module)s:%(lineno)d - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

_FORMATTER = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(module)s:%(lineno)d - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# MindSpace bot logs use their own handlers; logger level is the min of the
# three sinks so each handler can see everything it's configured for.
_logger = logging.getLogger("MindSpace")
_logger.setLevel(min(_STREAM_LEVEL, _FILE_LEVEL, _DISCORD_LEVEL))
_logger.propagate = False

_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(_STREAM_LEVEL)
_stream_handler.setFormatter(_FORMATTER)
_logger.addHandler(_stream_handler)

try:
    os.makedirs(os.path.dirname(config.Log.FILE_PATH), exist_ok=True)
    # Rotate at midnight, keep 3 days of history (mindspace.log + 3 dated backups).
    _file_handler = logging.handlers.TimedRotatingFileHandler(
        config.Log.FILE_PATH,
        when="midnight",
        backupCount=3,
        encoding="utf-8",
    )
    _file_handler.setLevel(_FILE_LEVEL)
    _file_handler.setFormatter(_FORMATTER)
    _logger.addHandler(_file_handler)
except OSError as e:
    _logger.warning(f"Could not open log file {config.Log.FILE_PATH}: {e}")

class MindSpaceLogger:
    """Unified global logger that routes logs to Console (sync) and Discord (via sync callback)."""
    def __init__(self, discord_level=_DISCORD_LEVEL):
        self.log_callback = None
        self.discord_level = discord_level

    def set_callback(self, callback):
        """Set the synchronous callback for Discord logging. Callback should accept (message: str)."""
        self.log_callback = callback

    def _format_discord(self, level_name, message):
        emoji = {
            "DEBUG": "🔍",
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "ERROR": "🚨"
        }.get(level_name, "📝")
        return f"{emoji} **[{level_name}]** {message}"

    def _log(self, level, message, guild=None):
        """Main logging entry point (synchronous)."""
        level_name = logging.getLevelName(level)

        # 1. Immediate Console/File Log.
        _logger.log(level, message, stacklevel=3)

        # 2. Trigger Discord callback if active and level is sufficient
        if self.log_callback and level >= self.discord_level:
            try:
                self.log_callback(self._format_discord(level_name, message))
            except Exception:
                pass

    def debug(self, message, guild=None): self._log(logging.DEBUG, message, guild)
    def info(self, message, guild=None): self._log(logging.INFO, message, guild)
    def warning(self, message, guild=None): self._log(logging.WARNING, message, guild)
    def error(self, message, guild=None): self._log(logging.ERROR, message, guild)

# Global logger instance
logger = MindSpaceLogger()
