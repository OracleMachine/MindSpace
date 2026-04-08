import os
import glob as glob_module
import config

try:
    import openviking as ov
    OPENVIKING_AVAILABLE = True
except ImportError:
    OPENVIKING_AVAILABLE = False

class VikingContextManager:
    """
    Wraps OpenViking for two-mode context management:
    - Channel-scoped (default): passive chat and single-channel commands.
      Uses find() scoped to the channel URI so only that channel's files are searched.
    - Global (!omni only): find() across all channels with no target_uri.

    Falls back to reading stream_of_conscious.md if OpenViking is not installed.
    """

    def __init__(self, root_path: str):
        self.root_path = root_path
        self._channel_uris = {}  # channel_name → list of root_uris returned by add_resource()
        self._ready = False
        if OPENVIKING_AVAILABLE:
            try:
                self.client = ov.SyncOpenViking(path=config.OPENVIKING_DATA_PATH)
                self.client.initialize()
                self._ready = True
            except Exception as e:
                from logger import logger
                logger.warning(f"OpenViking not configured, falling back to filesystem. ({e})")

    def rebuild_index(self):
        """Re-index all .md files in the KB after every git commit."""
        if not OPENVIKING_AVAILABLE:
            return
        self._channel_uris = {}
        for entry in os.scandir(self.root_path):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            channel_name = entry.name
            for file_path in glob_module.glob(os.path.join(entry.path, "**/*.md"), recursive=True):
                try:
                    result = self.client.add_resource(path=file_path)
                    uri = result.get("root_uri")
                    if uri:
                        self._channel_uris.setdefault(channel_name, []).append(uri)
                except Exception:
                    pass
        self.client.wait_processed()

    def get_channel_context(self, channel_name: str, query: str = "") -> str:
        """
        Return L1 context string scoped to a single channel.
        - With query: semantic search limited to channel_uri, returns top overviews.
        - Without query: returns the channel-level L1 overview directly.
        """
        sanitized = _sanitize(channel_name)
        if self._ready:
            try:
                channel_uri = f"viking://resources/{sanitized}/"
                if query:
                    results = self.client.find(query, limit=3, target_uri=channel_uri)
                    parts = [self.client.overview(r["uri"]) for r in results.get("resources", [])]
                    return "\n\n".join(parts) if parts else ""
                else:
                    return self.client.overview(channel_uri)
            except Exception:
                pass
        # Fallback: read stream_of_conscious.md
        stream_file = os.path.join(self.root_path, sanitized, "stream_of_conscious.md")
        if os.path.exists(stream_file):
            with open(stream_file, "r") as f:
                return f.read()
        return ""

    def get_global_context(self, query: str) -> str:
        """
        Traverse ALL channel folders — semantic search with no channel scope.
        Used exclusively by !omni.
        """
        if self._ready:
            try:
                results = self.client.find(query, limit=10)
                parts = [self.client.overview(r["uri"]) for r in results.get("resources", [])]
                return "\n\n".join(parts) if parts else ""
            except Exception:
                pass
        # Fallback: collect every channel's stream_of_conscious.md
        parts = []
        for entry in os.scandir(self.root_path):
            if entry.is_dir() and not entry.name.startswith("."):
                stream_file = os.path.join(entry.path, "stream_of_conscious.md")
                if os.path.exists(stream_file):
                    with open(stream_file, "r") as f:
                        parts.append(f.read())
        return "\n\n".join(parts)

    def close(self):
        if self._ready:
            self.client.close()


def _sanitize(name: str) -> str:
    return name.replace(" ", "_").replace("-", "_")
