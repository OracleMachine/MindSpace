import os
import glob as glob_module
import openviking as ov  # hard import — fails fast if package not installed
import config


class VikingContextManager:
    """
    Wraps OpenViking for two-mode context management:
    - Channel-scoped (default): passive chat and single-channel commands.
      Uses find() scoped to the channel URI so only that channel's files are searched.
    - Global (!omni only): find() across all channels with no target_uri.

    Raises on init if OpenViking is not installed or not configured.
    """

    def __init__(self, root_path: str):
        self.root_path = root_path
        self._channel_uris = {}
        os.environ.setdefault("OPENVIKING_CONFIG_FILE", config.OPENVIKING_CONF_PATH)
        self.client = ov.SyncOpenViking(path=config.OPENVIKING_DATA_PATH)
        self.client.initialize()

    def rebuild_index(self):
        """Re-index all .md files in the KB after every git commit."""
        self._channel_uris = {}
        for entry in os.scandir(self.root_path):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            channel_name = entry.name
            channel_uri = f"viking://resources/{channel_name}/"
            for file_path in glob_module.glob(os.path.join(entry.path, "**/*.md"), recursive=True):
                try:
                    result = self.client.add_resource(path=file_path, parent=channel_uri)
                    uri = result.get("root_uri")
                    if uri:
                        self._channel_uris.setdefault(channel_name, []).append(uri)
                except Exception:
                    pass
        self.client.wait_processed()

    def get_channel_context(self, channel_name: str, query: str = "") -> str:
        """
        Return context string scoped to a single channel.
        - With query: semantic search limited to channel_uri, returns top overviews.
        - Without query: returns the channel-level overview directly.
        """
        channel_uri = f"viking://resources/{channel_name}/"
        if query:
            results = self.client.find(query, limit=3, target_uri=channel_uri)
            parts = [self.client.overview(r["uri"]) for r in results.get("resources", [])]
            return "\n\n".join(parts) if parts else ""
        else:
            return self.client.overview(channel_uri)

    def get_global_context(self, query: str) -> str:
        """
        Traverse ALL channel folders — semantic search with no channel scope.
        Used exclusively by !omni.
        """
        results = self.client.find(query, limit=10)
        parts = [self.client.overview(r["uri"]) for r in results.get("resources", [])]
        return "\n\n".join(parts) if parts else ""

    def close(self):
        self.client.close()


def _sanitize(name: str) -> str:
    return name.replace(" ", "_").replace("-", "_")
