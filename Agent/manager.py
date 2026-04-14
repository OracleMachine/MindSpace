import os
import datetime
import git
import config
from viking import VikingContextManager
from pageindex_manager import PageIndexManager
from logger import logger

class KnowledgeBaseManager:
    """
    Unified manager for the MindSpace Knowledge Base.
    Designed for a SINGLE server (One Server = One Repo).
    Initialized once by the bot and shared as the primary state.
    """
    def __init__(self, server_name):
        self.server_name = self._sanitize_name(server_name)
        self.root_path = config.Storage.BASE_PATH
        self.channels_path = config.Paths.CHANNELS
        logger.info(f"KB: opening git repo at {self.root_path}")
        self._repo = self._ensure_repo_exists()
        logger.info("KB: initializing VikingContextManager (OpenViking client)...")
        self.viking = VikingContextManager(self.channels_path)
        logger.info("KB: initializing PageIndexManager (cloud client)...")
        self.pageindex = PageIndexManager()
        self._history_cache = {}  # channel_name → bounded history string
        logger.info("KB: initialization complete")

    def _sanitize_name(self, name):
        """Standardize folder names for the filesystem."""
        return name.replace(" ", "_").replace("-", "_")

    def _ensure_repo_exists(self):
        """Ensure the root directory exists and is a Git repo. Returns a Repo object."""
        if not os.path.exists(self.root_path):
            os.makedirs(self.root_path)
            repo = git.Repo.init(self.root_path)
        else:
            repo = git.Repo(self.root_path)
        
        os.makedirs(self.channels_path, exist_ok=True)
        return repo

    def get_channel_path(self, channel_name):
        """Get or create the specific path for a channel."""
        path = os.path.join(self.channels_path, channel_name)
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
        """
        Commit changes under Channels/ only.
        """
        channels_rel = os.path.relpath(self.channels_path, self.root_path)

        # Stage ONLY Channels/
        self._repo.git.add(channels_rel)
        try:
            self._repo.index.commit(message)
        except Exception as e:
            logger.warning(f"Git commit failed (likely no changes): {e}")

    def index_files(self, file_rel_paths):
        """Index specific files in OpenViking and PageIndex."""
        channels_abs = os.path.abspath(self.channels_path)
        for file_rel_path in file_rel_paths:
            abs_path = os.path.abspath(os.path.join(self.root_path, file_rel_path))
            if not os.path.exists(abs_path):
                continue
            if os.path.commonpath([abs_path, channels_abs]) != channels_abs:
                continue  # not under Channels/ — skip

            channel_name = os.path.relpath(abs_path, channels_abs).split(os.sep)[0]

            # Re-index .md in Viking
            if abs_path.endswith(".md"):
                self.viking.index_file(abs_path, channel_name)

            # Re-index .pdf in PageIndex
            elif abs_path.endswith(".pdf"):
                try:
                    self.pageindex.index_document(abs_path, channel_name)
                except Exception:
                    pass

    def save_state(self, message):
        """
        Orchestrate persistence: Commit changes to Git, then lazily re-index
        the touched files in OpenViking and PageIndex.
        """
        channels_rel = os.path.relpath(self.channels_path, self.root_path)

        # Find modified and untracked files before staging, scoped to Channels/.
        changed_files = [
            item.a_path for item in self._repo.index.diff(None)
            if item.a_path.startswith(channels_rel + os.sep) or item.a_path == channels_rel
        ]
        untracked = [
            p for p in self._repo.untracked_files
            if p.startswith(channels_rel + os.sep) or p == channels_rel
        ]
        to_index = set(changed_files + untracked)

        self.git_commit(message)
        self.index_files(to_index)

    def get_channel_context(self, channel_name: str, query: str = "") -> str:
        """Return Viking L1 context string for a single channel."""
        return self.viking.get_channel_context(channel_name, query)

    def get_global_context(self, query: str) -> str:
        """Return Viking context spanning all channel folders."""
        return self.viking.get_global_context(query)

    def get_deep_context(self, channel_name: str, query: str) -> str:
        """Return PageIndex deep Q&A result for a channel's indexed PDFs."""
        channel_path = self.get_channel_path(channel_name)
        return self.pageindex.query_channel(channel_name, channel_path, query)

    # --- Conversation History (in-memory only; seeded from Discord on startup) ---

    def _trim(self, text: str) -> str:
        """Trim text from the start to the next message boundary if over the char limit."""
        if len(text) <= config.Conversation.HISTORY_MAX_CHARS:
            return text
        overflow = len(text) - config.Conversation.HISTORY_MAX_CHARS
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
        return self._repo.untracked_files

    def list_channel_tree(self, channel_name: str, max_entries: int = 200) -> str:
        """Flat listing of the channel's folder structure for LLM routing prompts.

        Returns one relative path per line, directories first (with trailing /),
        then files. Hidden paths are skipped. Truncates at `max_entries` with a
        marker so prompts stay bounded on deep trees.
        """
        root = os.path.join(self.channels_path, channel_name)
        if not os.path.isdir(root):
            return "(empty channel)"
        dirs: list[str] = []
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            rel_dir = os.path.relpath(dirpath, root)
            if rel_dir != ".":
                dirs.append(rel_dir + "/")
            for fn in sorted(filenames):
                if fn.startswith("."):
                    continue
                rel_file = fn if rel_dir == "." else os.path.join(rel_dir, fn)
                files.append(rel_file)
        entries = dirs + files
        truncated = len(entries) > max_entries
        entries = entries[:max_entries]
        out = "\n".join(entries) if entries else "(empty channel)"
        if truncated:
            out += f"\n... (truncated at {max_entries} entries)"
        return out

