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
            # Initialize core files as per SPEC.MD
            self.write_file(os.path.join(path, "INDEX.MD"), f"# Index: {folder_name}\n\nAbstract: Initialized.")
            self.write_file(os.path.join(path, "STREAM_OF_CONSCIOUS.MD"), f"# Stream of Consciousness: {folder_name}\n\n")
        return path

    def write_file(self, file_path, content):
        """Atomic write to the filesystem."""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(content)

    def append_thought(self, channel_name, thought):
        """Append a timestamped thought to the STREAM_OF_CONSCIOUS.MD."""
        path = self.get_channel_path(channel_name)
        stream_file = os.path.join(path, "STREAM_OF_CONSCIOUS.MD")
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

    def list_untracked_files(self):
        """Find untracked files on disk for the !organize command."""
        result = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], 
                                cwd=self.root_path, capture_output=True, text=True)
        return result.stdout.strip().split("\n") if result.stdout.strip() else []
