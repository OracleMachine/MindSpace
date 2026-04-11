import os
import json
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

    Indexing is incremental. A JSON cache at BASE_STORAGE_PATH/.viking_index.json
    tracks {rel_path: {"mtime": float, "uri": str}} so restarts only re-index
    files that are new or modified since last run, and deletions are purged
    from the vector store. This prevents duplicate vectors on restart.
    """

    _CACHE_FILENAME = ".viking_index.json"

    def __init__(self, root_path: str):
        self.root_path = root_path
        self._cache_path = os.path.join(config.BASE_STORAGE_PATH, self._CACHE_FILENAME)
        self._cache, self._cache_was_present = self._load_cache()
        self._ensured_channels: set[str] = set()  # channel URIs confirmed to exist
        os.environ.setdefault("OPENVIKING_CONFIG_FILE", config.OPENVIKING_CONF_PATH)
        self.client = ov.SyncOpenViking(path=config.OPENVIKING_DATA_PATH)
        self.client.initialize()

    def _ensure_channel_dir(self, channel_name: str) -> str:
        """
        Ensure viking://resources/<channel_name>/ exists in the OpenViking store.
        OpenViking's add_resource(parent=...) requires the parent URI to already
        exist, so we lazily mkdir it on first use. Cached per instance.
        Returns the channel URI.
        """
        channel_uri = f"viking://resources/{channel_name}/"
        if channel_uri in self._ensured_channels:
            return channel_uri
        try:
            self.client.mkdir(channel_uri)
            logger.debug(f"OpenViking: mkdir {channel_uri}")
        except Exception as e:
            # mkdir on an existing dir may raise — that's fine, we just want it to exist.
            logger.debug(f"OpenViking: mkdir {channel_uri} raised (likely already exists): {e}")
        self._ensured_channels.add(channel_uri)
        return channel_uri

    def _load_cache(self) -> tuple[dict, bool]:
        """Return (cache_dict, was_present). was_present=False triggers a cold-start wipe."""
        try:
            with open(self._cache_path) as f:
                return json.load(f), True
        except FileNotFoundError:
            return {}, False
        except json.JSONDecodeError as e:
            logger.warning(f"OpenViking: cache file corrupted ({e}), treating as cold start")
            return {}, False

    def _cold_start_wipe(self):
        """
        Nuke all resources in the OpenViking store. Called when the cache is missing,
        meaning we cannot trust the store to match disk state (e.g. leftovers from an
        older run without incremental indexing). Safe no-op on an empty store.
        """
        logger.info("OpenViking: cache missing — wiping vector store for clean rebuild")
        try:
            self.client.rm("viking://resources/", recursive=True)
        except Exception as e:
            logger.warning(f"OpenViking: cold-start wipe failed (store may already be empty): {e}")

    def _save_cache(self):
        tmp = self._cache_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._cache, f, indent=2)
        os.replace(tmp, self._cache_path)

    def index_file(self, file_path: str, channel_name: str) -> bool:
        """
        Idempotently index a single .md file. Skips unchanged files, re-indexes
        modified files (removing the stale vector first), and caches successes.
        Returns True if the file is up-to-date in the index after this call.
        """
        if not file_path.endswith(".md"):
            return False
        rel = os.path.relpath(file_path, self.root_path)
        try:
            mtime = os.path.getmtime(file_path)
        except OSError as e:
            logger.error(f"OpenViking: cannot stat {file_path}: {e}")
            return False

        cached = self._cache.get(rel)
        if cached and cached.get("mtime", -1) >= mtime:
            return True  # already indexed, unchanged

        # Modified: remove the old vector before re-adding to avoid duplicates.
        if cached and cached.get("uri"):
            try:
                self.client.rm(cached["uri"])
            except Exception as e:
                logger.warning(f"OpenViking: failed to rm stale {cached['uri']}: {e}")

        channel_uri = self._ensure_channel_dir(channel_name)
        try:
            result = self.client.add_resource(path=file_path, parent=channel_uri)
            uri = result.get("root_uri") if isinstance(result, dict) else None
            if not uri:
                # On failure the SDK returns {status: "error", errors: [...]} with no root_uri.
                status = result.get("status") if isinstance(result, dict) else None
                errors = result.get("errors") if isinstance(result, dict) else None
                logger.warning(
                    f"OpenViking: add_resource failed for {file_path} "
                    f"(status={status}, errors={errors}). Full response: {result!r}"
                )
                return False
            self._cache[rel] = {"mtime": mtime, "uri": uri}
            self._save_cache()
            return True
        except Exception as e:
            logger.error(f"OpenViking: failed to index {file_path}: {e}")
            return False

    def rebuild_index(self):
        """
        Sync disk state with the OpenViking index. Only new/modified files are
        re-indexed; deleted files are purged from the vector store and cache.
        If the cache is missing, the store is wiped first and everything is
        re-indexed from scratch (self-healing on cold start).
        """
        logger.info(f"OpenViking: starting index sync (cache {'present' if self._cache_was_present else 'MISSING → cold start'})")
        if not self._cache_was_present:
            self._cold_start_wipe()
            self._cache_was_present = True  # don't wipe again on a manual re-call

        # Snapshot current disk state.
        disk_files = {}  # rel_path -> (abs_path, channel_name)
        for entry in os.scandir(self.root_path):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            channel = entry.name
            for fp in glob_module.glob(os.path.join(entry.path, "**/*.md"), recursive=True):
                rel = os.path.relpath(fp, self.root_path)
                disk_files[rel] = (fp, channel)
        logger.info(f"OpenViking: found {len(disk_files)} .md files on disk across "
                    f"{len({c for _, c in disk_files.values()})} channel(s)")

        # Purge entries that are gone from disk.
        stale_keys = [rel for rel in self._cache if rel not in disk_files]
        if stale_keys:
            logger.info(f"OpenViking: removing {len(stale_keys)} stale entries from store")
        removed = 0
        for rel in stale_keys:
            uri = self._cache[rel].get("uri")
            if uri:
                try:
                    self.client.rm(uri)
                    logger.debug(f"OpenViking: rm'd {uri}")
                except Exception as e:
                    logger.warning(f"OpenViking: failed to rm deleted {uri}: {e}")
            del self._cache[rel]
            removed += 1

        # Determine work set up-front so we can log meaningful progress.
        work = []  # list of (rel, fp, channel, is_new)
        unchanged = 0
        for rel, (fp, channel) in disk_files.items():
            try:
                mtime = os.path.getmtime(fp)
            except OSError as e:
                logger.warning(f"OpenViking: cannot stat {fp}: {e}")
                continue
            cached = self._cache.get(rel)
            if cached and cached.get("mtime", -1) >= mtime:
                unchanged += 1
                continue
            work.append((rel, fp, channel, cached is None))

        if not work:
            logger.info(f"OpenViking: nothing to index — {unchanged} files unchanged, {removed} removed")
            self.client.wait_processed()
            self._save_cache()
            return

        logger.info(f"OpenViking: indexing {len(work)} file(s) "
                    f"({sum(1 for _, _, _, n in work if n)} new, "
                    f"{sum(1 for _, _, _, n in work if not n)} modified); "
                    f"{unchanged} unchanged will be skipped")

        new_count = 0
        modified_count = 0
        failed = 0
        # Log every file at debug, and emit an INFO progress line every ~10 files
        # (or every file if the set is small) so cold starts aren't silent.
        progress_every = max(1, min(10, len(work) // 5 or 1))
        for i, (rel, fp, channel, is_new) in enumerate(work, start=1):
            logger.debug(f"OpenViking: [{i}/{len(work)}] indexing {rel} ({'new' if is_new else 'modified'})")
            if self.index_file(fp, channel):
                if is_new:
                    new_count += 1
                else:
                    modified_count += 1
            else:
                failed += 1
            if i % progress_every == 0 or i == len(work):
                logger.info(f"OpenViking: progress {i}/{len(work)} "
                            f"({new_count} new, {modified_count} modified, {failed} failed)")

        logger.info("OpenViking: waiting for background processing to complete...")
        self.client.wait_processed()
        self._save_cache()
        logger.info(
            f"OpenViking: rebuild_index complete — {new_count} new, {modified_count} modified, "
            f"{removed} removed, {unchanged} unchanged, {failed} failed "
            f"(total on disk: {len(disk_files)})"
        )

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
            
            # SAFE OVERVIEW FETCH: handle files vs directories
            try:
                # Use pre-populated overview/abstract if available and not empty
                overview = r.get("overview") or r.get("abstract")
                
                if not overview:
                    # Determine type to use correct fetch method
                    st = self.client.stat(uri)
                    if st.get("isDir"):
                        overview = self.client.overview(uri)
                    else:
                        # For files, read a snippet of the content
                        content = self.client.read(uri)
                        limit = 500
                        overview = (content[:limit] + "...") if len(content) > limit else content
            except Exception as e:
                logger.warning(f"OpenViking: could not get overview for {uri}: {e}")
                overview = "Content overview unavailable."
            
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
