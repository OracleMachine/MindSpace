import os
from pathlib import Path
import openviking as ov  # hard import — fails fast if package not installed
import config
from logger import logger


class VikingContextManager:
    """
    Wraps OpenViking for two-mode context management:
    - Channel-scoped (default): passive chat and single-channel commands.
    - Global (!omni only): find() across all channels.

    Synchronization is handled natively by OpenViking using internal hash-tracking.
    Unchanged files cost zero tokens to re-scan.
    """

    def __init__(self, root_path: str):
        self.root_path = root_path
        self._ensured_channels: set[str] = set()  # channel URIs confirmed to exist
        os.environ.setdefault("OPENVIKING_CONFIG_FILE", config.Paths.VIKING_CONF)
        logger.info(f"Viking: constructing SyncOpenViking(path={config.Paths.VIKING_DATA})...")
        self.client = ov.SyncOpenViking(path=config.Paths.VIKING_DATA)
        logger.info("Viking: calling client.initialize()...")
        self.client.initialize()
        logger.info("Viking: client initialized")

    def _ensure_channel_dir(self, channel_name: str) -> str:
        """
        Ensure viking://resources/<channel_name>/ exists in the OpenViking store.
        """
        channel_uri = f"viking://resources/{channel_name}/"
        if channel_uri in self._ensured_channels:
            return channel_uri
        try:
            self.client.mkdir(channel_uri)
        except Exception:
            pass  # fine if exists
        self._ensured_channels.add(channel_uri)
        return channel_uri

    def index_file(self, file_path: str, channel_name: str) -> bool:
        """
        Surgically upsert a single file into the index (O(1) operation).
        """
        path_obj = Path(file_path)
        # Skip ignored extensions from config (e.g., 'pdf' vs '.pdf')
        ext = path_obj.suffix.lower().lstrip(".")
        if ext in config.Storage.IGNORED_EXTENSIONS:
            return False

        if path_obj.suffix.lower() != ".md":
            return False
        channel_uri = self._ensure_channel_dir(channel_name)
        try:
            # OpenViking inherently handles the update if the file already exists
            self.client.add_resource(path=file_path, parent=channel_uri)
            return True
        except Exception as e:
            logger.error(f"OpenViking: failed to index {file_path}: {e}")
            return False

    def rebuild_index(self, channel_name: str = None):
        """
        Sync disk state with OpenViking. Uses native directory mirroring.
        If channel_name is provided, only that channel folder is synced.
        """
        if channel_name:
            target_path = os.path.join(self.root_path, channel_name)
            parent_uri = f"viking://resources/{channel_name}/"
            logger.info(f"OpenViking: Syncing channel #{channel_name}...")
        else:
            target_path = self.root_path
            parent_uri = "viking://resources/"
            logger.info("OpenViking: Syncing entire Knowledge Base (Channels/)...")

        try:
            # OpenViking walks the dir and uses local CPU hashes to skip unchanged files (0 tokens).
            # We exclude files based on config (typically PDFs, as they are handled by PageIndex).
            # config.Storage.IGNORED_EXTENSIONS is now dot-less (e.g. ['pdf', 'jpg'])
            exclude_patterns = [f"*.{ext}" for ext in config.Storage.IGNORED_EXTENSIONS]
            self.client.add_resource(path=target_path, parent=parent_uri, exclude=exclude_patterns)
            self.client.wait_processed()
            logger.info(f"OpenViking: Sync complete for {target_path}")
        except Exception as e:
            logger.error(f"OpenViking: Sync failed: {e}")

    def _safe_search(self, query: str, target_uri: str, limit: int, channel_name: str = None, is_retry: bool = False) -> str:
        """
        Execute search with a single retry if stale files are detected on disk.
        """
        results = self.client.find(query, limit=limit, target_uri=target_uri)
        res_dict = results.to_dict()
        
        all_matches = res_dict.get("resources", []) + res_dict.get("memories", [])
        logger.info(f"🔍 OpenViking: Found {len(all_matches)} semantic matches for query: '{query}' (Retry: {is_retry})")
        
        parts = []
        stale_detected = False
        
        for i, r in enumerate(all_matches):
            uri = r.get("uri")
            score = r.get("score", 0)
            reason = r.get("match_reason", "No reason provided")
            
            try:
                overview = r.get("overview") or r.get("abstract")
                if not overview:
                    st = self.client.stat(uri)
                    if st.get("isDir"):
                        overview = self.client.overview(uri)
                    else:
                        content = self.client.read(uri)
                        overview = (content[:500] + "...") if len(content) > 500 else content
                
                parts.append(
                    f"Source: {uri}\n"
                    f"Relevance Score: {score:.4f}\n"
                    f"Match Reason: {reason}\n"
                    f"Content Overview:\n{overview}"
                )
            except (FileNotFoundError, RuntimeError, ov.VikingError) as e:
                # Target errors that imply the file is gone or the URI is stale
                logger.warning(f"OpenViking: Match #{i+1} ({uri}) is stale or missing: {e}")
                stale_detected = True
                continue
            except Exception as e:
                # Other errors (network, permissions) should be logged but not trigger re-index
                logger.error(f"OpenViking: Unexpected error reading {uri}: {e}")
                continue

        if stale_detected and not is_retry:
            logger.info("OpenViking: Stale results found. Syncing Knowledge Base and retrying search...")
            # Synchronous sync (O(N) CPU hash check, 0 tokens for unchanged files)
            self.rebuild_index(channel_name)
            # Retry the search exactly once
            return self._safe_search(query, target_uri, limit, channel_name, is_retry=True)

        return "\n\n---\n\n".join(parts) or "No relevant information found."

    def get_channel_context(self, channel_name: str, query: str = "") -> str:
        """
        Return context string scoped to a single channel.
        """
        channel_uri = f"viking://resources/{channel_name}/"
        if query:
            logger.info(f"🔎 OpenViking: Searching channel #{channel_name} for '{query}'...")
            return self._safe_search(query, channel_uri, limit=3, channel_name=channel_name)
        else:
            logger.info(f"📖 OpenViking: Retrieving base overview for channel #{channel_name}")
            return self.client.overview(channel_uri)

    def get_global_context(self, query: str) -> str:
        """
        Traverse ALL channel folders.
        """
        logger.info(f"🌐 OpenViking: Global search for '{query}'...")
        # Scope global search to resources to avoid internal system leaks
        return self._safe_search(query, "viking://resources/", limit=10)

    def close(self):
        self.client.close()


def _sanitize(name: str) -> str:
    return name.replace(" ", "_").replace("-", "_")
