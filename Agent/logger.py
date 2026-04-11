import logging
import logging.handlers
import asyncio
import datetime
import os

import config

# --- Standard Logging Setup ---
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR

_STREAM_LEVEL = getattr(logging, config.LOG_STREAM_LEVEL, logging.INFO)
_FILE_LEVEL = getattr(logging, config.LOG_FILE_LEVEL, logging.DEBUG)
_DISCORD_LEVEL = getattr(logging, config.LOG_DISCORD_LEVEL, logging.INFO)

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
    os.makedirs(os.path.dirname(config.LOG_FILE_PATH), exist_ok=True)
    # Rotate at midnight, keep 3 days of history (mindspace.log + 3 dated backups).
    _file_handler = logging.handlers.TimedRotatingFileHandler(
        config.LOG_FILE_PATH,
        when="midnight",
        backupCount=3,
        encoding="utf-8",
    )
    _file_handler.setLevel(_FILE_LEVEL)
    _file_handler.setFormatter(_FORMATTER)
    _logger.addHandler(_file_handler)
except OSError as e:
    _logger.warning(f"Could not open log file {config.LOG_FILE_PATH}: {e}")

# If you want to see detailed OpenViking semantic search logs, uncomment this:
# logging.getLogger("openviking").setLevel(logging.DEBUG)

class MindSpaceLogger:
    """Unified global logger that routes logs to Console (sync) and Discord (background)."""
    def __init__(self, discord_level=_DISCORD_LEVEL):
        self.bot = None
        self.discord_level = discord_level
        self._queue = asyncio.Queue()

    def bind_bot(self, bot):
        """Link the Discord bot to the logger."""
        self.bot = bot

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

        # 1. Immediate Console Log.
        # stacklevel=3 skips: _logger.log (1) -> _log (2) -> debug/info/... (3)
        # so %(module)s and %(lineno)d resolve to the actual caller, not logger.py.
        _logger.log(level, message, stacklevel=3)

        # 2. Queue for Discord Log if bot is active and level is sufficient
        if self.bot and guild and level >= self.discord_level:
            try:
                # Use loop.call_soon_threadsafe to ensure it works even if called from a blocking thread
                self.bot.loop.call_soon_threadsafe(self._queue.put_nowait, (guild, self._format_discord(level_name, message)))
            except Exception:
                # Fallback if loop isn't running or queue is full
                pass

    def debug(self, message, guild=None): self._log(logging.DEBUG, message, guild)
    def info(self, message, guild=None): self._log(logging.INFO, message, guild)
    def warning(self, message, guild=None): self._log(logging.WARNING, message, guild)
    def error(self, message, guild=None): self._log(logging.ERROR, message, guild)

    async def process_log_queue(self):
        """Background task to drain the log queue and send to Discord."""
        while True:
            guild, formatted_message = await self._queue.get()
            try:
                if self.bot:
                    await self.bot.send_system_log(guild, formatted_message)
            except Exception as e:
                _logger.error(f"Failed to send log to Discord: {e}")
            finally:
                self._queue.task_done()

# Global logger instance
logger = MindSpaceLogger()
