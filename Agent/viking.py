import os
import glob as glob_module
import openviking as ov  # hard import — fails fast if package not installed
import config
from logger import logger


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

    def index_file(self, file_path: str, channel_name: str):
        """Index a single .md file into its channel-scoped Viking URI."""
        if not file_path.endswith(".md"):
            return
        channel_uri = f"viking://resources/{channel_name}/"
        try:
            result = self.client.add_resource(path=file_path, parent=channel_uri)
            uri = result.get("root_uri")
            if uri:
                self._channel_uris.setdefault(channel_name, []).append(uri)
        except Exception:
            pass

    def rebuild_index(self):
        """Re-index all .md files in the KB. Used on startup."""
        self._channel_uris = {}
        for entry in os.scandir(self.root_path):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            channel_name = entry.name
            for file_path in glob_module.glob(os.path.join(entry.path, "**/*.md"), recursive=True):
                self.index_file(file_path, channel_name)
        self.client.wait_processed()

    def _format_results(self, res_dict: dict, query: str) -> str:
        """Helper to format semantic search results with match reasons and scores."""
        parts = []
        # Check both 'resources' and 'memories'
        all_matches = res_dict.get("resources", []) + res_dict.get("memories", [])
        
        logger.info(f"🔍 OpenViking: Found {len(all_matches)} semantic matches for query: '{query}'")
        
        for i, r in enumerate(all_matches):
            uri = r.get("uri")
            score = r.get("score", 0)
            reason = r.get("match_reason", "No reason provided")
            overview = self.client.overview(uri)
            
            logger.debug(f"  Match #{i+1}: {uri} (Score: {score:.4f})")
            logger.debug(f"  Reason: {reason}")
            
            parts.append(
                f"Source: {uri}\n"
                f"Relevance Score: {score:.4f}\n"
                f"Match Reason: {reason}\n"
                f"Content Overview:\n{overview}"
            )
        return "\n\n---\n\n".join(parts)

    def get_channel_context(self, channel_name: str, query: str = "") -> str:
        """
        Return context string scoped to a single channel.
        - With query: semantic search limited to channel_uri, returns top overviews.
        - Without query: returns the channel-level overview directly.
        """
        channel_uri = f"viking://resources/{channel_name}/"
        if query:
            logger.info(f"🔎 OpenViking: Searching channel #{channel_name} for '{query}'...")
            results = self.client.find(query, limit=3, target_uri=channel_uri)
            return self._format_results(results.to_dict(), query)
        else:
            logger.info(f"📖 OpenViking: Retrieving base overview for channel #{channel_name}")
            return self.client.overview(channel_uri)

    def get_global_context(self, query: str) -> str:
        """
        Traverse ALL channel folders — semantic search with no channel scope.
        Used exclusively by !omni.
        """
        logger.info(f"🌐 OpenViking: Global search for '{query}'...")
        results = self.client.find(query, limit=10)
        return self._format_results(results.to_dict(), query)

    def close(self):
        self.client.close()


def _sanitize(name: str) -> str:
    return name.replace(" ", "_").replace("-", "_")
