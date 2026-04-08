import os
import subprocess
import datetime
import config
from viking import VikingContextManager

class KnowledgeBaseManager:
    def __init__(self, server_name):
        self.server_name = self._sanitize_name(server_name)
        self.root_path = config.BASE_STORAGE_PATH
        self._ensure_repo_exists()
        self.viking = VikingContextManager(self.root_path)
        self._history_cache = {}  # channel_name → bounded history string

    def _sanitize_name(self, name):
        """Standardize folder names for the filesystem."""
        return name.replace(" ", "_").replace("-", "_")

    def _ensure_repo_exists(self):
        """Ensure the root directory exists and is a Git repo."""
        if not os.path.exists(self.root_path):
            os.makedirs(self.root_path)
            subprocess.run(["git", "init"], cwd=self.root_path)

    def get_channel_path(self, channel_name):
        """Get or create the specific path for a channel."""
        path = os.path.join(self.root_path, channel_name)
        if not os.path.exists(path):
            os.makedirs(path)
            self.write_file(os.path.join(path, "stream_of_conscious.md"), f"# Stream of Consciousness: {channel_name}\n\n")
        return path

    def write_file(self, file_path, content):
        """Atomic write to the filesystem."""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(content)

    def append_thought(self, channel_name, thought):
        """Append a timestamped thought to the stream_of_conscious.md."""
        path = self.get_channel_path(channel_name)
        stream_file = os.path.join(path, "stream_of_conscious.md")
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(stream_file, "a") as f:
            f.write(f"\n- [{timestamp}] {thought}")

    def git_commit(self, message):
        """Perform a Git commit and rebuild the Viking index."""
        subprocess.run(["git", "add", "."], cwd=self.root_path)
        subprocess.run(["git", "commit", "-m", message], cwd=self.root_path)
        self.viking.rebuild_index()

    def get_channel_context(self, channel_name: str, query: str = "") -> str:
        """Return Viking L1 context string for a single channel."""
        return self.viking.get_channel_context(channel_name, query)

    def get_global_context(self, query: str) -> str:
        """Return Viking context spanning all channel folders."""
        return self.viking.get_global_context(query)

    # --- Conversation History (in-memory only; seeded from Discord on startup) ---

    def _trim(self, text: str) -> str:
        """Trim text from the start to the next message boundary if over the char limit."""
        if len(text) <= config.CONVERSATION_HISTORY_MAX_CHARS:
            return text
        overflow = len(text) - config.CONVERSATION_HISTORY_MAX_CHARS
        cut = text.find("\n[", overflow)
        return text[cut + 1:] if cut != -1 else ""

    def seed_history(self, channel_name: str, text: str):
        """Populate in-memory history from Discord on startup."""
        trimmed = self._trim(text)
        if trimmed:
            self._history_cache[channel_name] = trimmed

    def get_history(self, channel_name: str) -> str:
        """Return the in-memory conversation history string for the channel."""
        return self._history_cache.get(channel_name, "")

    def append_history(self, channel_name: str, role: str, content: str):
        """Append a new turn to the in-memory history and trim if over limit."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"[{timestamp}] {role}:\n{content}\n\n"
        current = self.get_history(channel_name)
        self._history_cache[channel_name] = self._trim(current + entry)

    # --- Stream of Consciousness ---

    def get_stream_content(self, channel_name: str) -> str:
        """Read the current stream_of_conscious.md for the channel."""
        path = self.get_channel_path(channel_name)
        stream_file = os.path.join(path, "stream_of_conscious.md")
        if os.path.exists(stream_file):
            with open(stream_file, "r") as f:
                return f.read()
        return ""

    def list_untracked_files(self):
        """Find untracked files on disk for the !organize command."""
        result = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                                cwd=self.root_path, capture_output=True, text=True)
        return result.stdout.strip().split("\n") if result.stdout.strip() else []
