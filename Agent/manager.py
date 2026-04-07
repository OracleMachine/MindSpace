import os
import subprocess
import datetime
from collections import deque
import config
from viking import VikingContextManager

class KnowledgeBaseManager:
    def __init__(self, server_name):
        self.server_name = self._sanitize_name(server_name)
        self.root_path = config.BASE_STORAGE_PATH
        self._ensure_repo_exists()
        self.viking = VikingContextManager(self.root_path)
        self._conversation_history = {}  # channel_name → deque of (role, content)

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
        folder_name = self._sanitize_name(channel_name)
        path = os.path.join(self.root_path, folder_name)
        if not os.path.exists(path):
            os.makedirs(path)
            # Initialize core files as per design.md
            self.write_file(os.path.join(path, "index.md"), f"# Index: {folder_name}\n\nAbstract: Initialized.")
            self.write_file(os.path.join(path, "stream_of_conscious.md"), f"# Stream of Consciousness: {folder_name}\n\n")
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

    def get_channel_context(self, channel_name: str) -> list:
        """Return Viking L0/L1 context for a single channel."""
        return self.viking.get_channel_context(channel_name)

    def get_global_context(self, query: str) -> list:
        """Return Viking context spanning all channel folders."""
        return self.viking.get_global_context(query)

    def get_history(self, channel_name: str) -> list:
        """Return recent conversation turns for the channel."""
        return list(self._conversation_history.get(channel_name, []))

    def append_history(self, channel_name: str, role: str, content: str):
        """Append a turn to the channel's conversation history."""
        if channel_name not in self._conversation_history:
            self._conversation_history[channel_name] = deque(maxlen=config.CONVERSATION_HISTORY_LIMIT)
        self._conversation_history[channel_name].append((role, content))

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
