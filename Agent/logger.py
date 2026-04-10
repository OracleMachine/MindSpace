import logging
import asyncio
import datetime

# --- Standard Logging Setup ---
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

_internal_logger = logging.getLogger("MindSpace")
_internal_logger.setLevel(logging.DEBUG)

# If you want to see detailed OpenViking semantic search logs, uncomment this:
# logging.getLogger("openviking").setLevel(logging.DEBUG)

class MindSpaceLogger:
    """Unified global logger that routes logs to Console (sync) and Discord (background)."""
    def __init__(self, discord_level=logging.INFO):
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
        
        # 1. Immediate Console Log
        if level == logging.DEBUG: _internal_logger.debug(message)
        elif level == logging.INFO: _internal_logger.info(message)
        elif level == logging.WARNING: _internal_logger.warning(message)
        elif level == logging.ERROR: _internal_logger.error(message)

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
                _internal_logger.error(f"Failed to send log to Discord: {e}")
            finally:
                self._queue.task_done()

# Global logger instance
logger = MindSpaceLogger()
