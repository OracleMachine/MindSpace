import os
import json
import hashlib
from pathlib import Path
import openviking as ov  # hard import — fails fast if package not installed
from mindspace.core import config
from mindspace.core.logger import logger

# MindSpace-level fingerprint cache. OpenViking's internal hash check skips
# file-content re-embedding (0 embedding tokens for unchanged files) but NOT
# directory-level .abstract.md / .overview.md LLM summary regeneration: any
# add_resource call with build_index=True (the default) re-runs the
# SemanticProcessor bottom-up, burning 80K-330K LLM tokens per startup on
# a dozen-channel KB. This cache lives next to the OpenViking data and lets
# us skip add_resource entirely when every .md file's sha256 is unchanged,
# or scope the call to just the channels whose aggregate changed.
_HASH_CACHE_FILENAME = ".mindspace_hash_cache.json"

# Single source of truth for where channel content lives inside OpenViking.
# Previously we had two conflicting conventions: full-tree sync wrote under
# `viking://resources/Channels/...`, while per-file runtime indexing wrote to
# `viking://resources/<channel_name>/...`. That created 2× (and worse, N×
# with rebuild auto-renames) duplicates of every file in the vector DB.
# Everything now resolves through this prefix.
_CHANNELS_ROOT_URI = "viking://resources/Channels"


def _channel_dir_uri(channel_name: str) -> str:
    """Canonical URI for a channel's folder inside OpenViking (trailing slash
    — openviking treats that as 'this is a directory URI, place children under it')."""
    return f"{_CHANNELS_ROOT_URI}/{channel_name}/"


def _file_sha256(path: str) -> str:
    """Stream-hash a file in 64KB chunks — constant memory regardless of size."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_channel_md_files(channel_root: str) -> list[tuple[str, str]]:
    """Walk a channel folder and return [(rel_path, abs_path), ...] sorted by
    rel_path. Mirrors the OpenViking exclude set used in rebuild_index — only
    .md files, no view.md (view.md is stance/opinion, not source material and
    must not flow into vector retrieval)."""
    results: list[tuple[str, str]] = []
    for dirpath, _, filenames in os.walk(channel_root):
        for fn in filenames:
            if fn == "view.md" or not fn.endswith(".md"):
                continue
            abs_path = os.path.join(dirpath, fn)
            results.append((os.path.relpath(abs_path, channel_root), abs_path))
    results.sort(key=lambda t: t[0])
    return results


def _hash_channel(channel_root: str) -> tuple[str, dict[str, str]]:
    """Return (channel_aggregate_sha, {rel_path: file_sha}) for a single channel.
    The aggregate is deterministic across runs: sort rel_paths, join
    `<rel>:<sha>` lines, hash the whole blob."""
    files = {rel: _file_sha256(p) for rel, p in _iter_channel_md_files(channel_root)}
    body = "\n".join(f"{p}:{h}" for p, h in sorted(files.items()))
    return hashlib.sha256(body.encode()).hexdigest(), files


def _hash_tree(root_path: str) -> dict:
    """Walk every immediate child dir of Channels/ and produce the full cache
    structure: {"aggregate": <tree_sha>, "channels": {<name>: {"aggregate": ..., "files": {...}}}}.
    Tree aggregate is a hash of sorted (channel_name, channel_aggregate) pairs."""
    channels: dict[str, dict] = {}
    if os.path.isdir(root_path):
        for name in sorted(os.listdir(root_path)):
            chan_dir = os.path.join(root_path, name)
            if not os.path.isdir(chan_dir):
                continue
            agg, files = _hash_channel(chan_dir)
            channels[name] = {"aggregate": agg, "files": files}
    body = "\n".join(f"{n}:{v['aggregate']}" for n, v in sorted(channels.items()))
    return {"aggregate": hashlib.sha256(body.encode()).hexdigest(), "channels": channels}


def _load_hash_cache(cache_path: str) -> dict | None:
    try:
        with open(cache_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_hash_cache(cache_path: str, cache: dict) -> None:
    tmp_path = cache_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(cache, f, indent=2)
    os.replace(tmp_path, cache_path)  # atomic on POSIX


def _diff_changed_channels(current: dict, cached: dict | None) -> list[str]:
    """Return channel names whose aggregate changed OR that were added/removed."""
    cur_ch = current.get("channels") or {}
    old_ch = (cached or {}).get("channels") or {}
    changed: set[str] = set()
    for name in cur_ch.keys() | old_ch.keys():
        cur_agg = (cur_ch.get(name) or {}).get("aggregate")
        old_agg = (old_ch.get(name) or {}).get("aggregate")
        if cur_agg != old_agg:
            changed.add(name)
    return sorted(changed)


def _format_token_summary(telemetry: dict | None) -> str:
    """Render the telemetry dict returned by `add_resource(telemetry=True)` as
    a single human-readable line — so every sync produces a receipt that
    disambiguates "zero tokens, pure CPU hash-walk" from "hit Gemini for N
    embeddings." Returns '' when the dict is missing or malformed."""
    if not telemetry:
        return ""
    summary = telemetry.get("summary", {}) or {}
    tokens = summary.get("tokens", {}) or {}
    total = tokens.get("total", 0) or 0
    embedding_total = (tokens.get("embedding") or {}).get("total", 0) or 0
    llm = tokens.get("llm") or {}
    llm_total = llm.get("total", 0) or 0
    duration_ms = summary.get("duration_ms", 0) or 0
    if total <= 0:
        return (
            f"✅ OpenViking: sync complete in {duration_ms:.0f}ms — "
            f"no tokens spent (CPU-only: hashes matched, embeddings cached)"
        )
    return (
        f"✅ OpenViking: sync complete in {duration_ms:.0f}ms — "
        f"{total:,} tokens used ({embedding_total:,} embedding + "
        f"{llm_total:,} LLM; input/output {llm.get('input', 0):,}/{llm.get('output', 0):,})"
    )


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
        logger.info("⚙️ OpenViking: Initializing storage engine... (This can take a few seconds on first boot)")
        self.client.initialize()
        logger.info("✅ OpenViking: Engine ready")

    def _ensure_channel_dir(self, channel_name: str) -> str:
        """
        Ensure the canonical channel URI (viking://resources/Channels/<name>/)
        exists in the OpenViking store. Idempotent — uses a local set cache.
        """
        channel_uri = _channel_dir_uri(channel_name)
        if channel_uri in self._ensured_channels:
            return channel_uri
        # The Channels/ parent must exist before creating a child under it.
        # mkdir is a no-op if already present; wrapped in try/except because
        # OpenViking raises on "already exists" rather than returning a flag.
        for uri in (_CHANNELS_ROOT_URI + "/", channel_uri):
            try:
                self.client.mkdir(uri)
            except Exception:
                pass
        self._ensured_channels.add(channel_uri)
        return channel_uri

    def index_file(self, file_path: str, channel_name: str) -> bool:
        """
        Surgically upsert a single file into the index (O(1) operation).
        """
        path_obj = Path(file_path)
        # view.md files are stance/opinion, not source material — exclude to keep
        # semantic search results grounded in evidence, not in the view itself.
        if path_obj.name == "view.md":
            return False
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

    def rebuild_index(self, channel_name: str = None, force: bool = False):
        """
        Sync disk state with OpenViking. Uses native directory mirroring.

        - `channel_name=None` (the startup path) runs the hash-gated full rebuild:
          short-circuits when the MindSpace fingerprint matches the cache,
          otherwise scopes add_resource to just the channels whose aggregate
          changed.
        - `channel_name=<name>` always syncs that channel (used by the
          per-channel path below and by any caller that already knows the
          scope).
        - `force=True` bypasses the hash cache and issues a full-tree sync.
          Wired to the `MINDSPACE_FORCE_REINDEX=1` env var for operator
          overrides.

        Uses `to=<exact target URI>` rather than `parent=<root>`. OpenViking's
        tree_builder calls `_resolve_unique_uri` on the `parent=` path when the
        candidate URI already exists, producing `Channels`, `Channels_1`, ...
        siblings on each restart. `to=` skips that resolver and writes into the
        exact URI, overwriting in place.
        """
        if channel_name is None:
            self._full_rebuild(force=force)
            return

        self._ensure_channel_dir(channel_name)
        target_path = os.path.join(self.root_path, channel_name)
        target_uri = f"{_CHANNELS_ROOT_URI}/{channel_name}"
        logger.info(f"⚙️ OpenViking: Syncing channel #{channel_name}...")
        self._invoke_add_resource(target_path, target_uri, label=f"#{channel_name}")

    def _invoke_add_resource(self, target_path: str, target_uri: str, label: str) -> bool:
        """The actual OpenViking call. Returns True on success, False if the
        client raised. Errors are logged, not re-raised — matching the prior
        swallow-and-log behaviour."""
        # `exclude` must be a comma-separated string — openviking's _parse_patterns
        # calls .strip()/.split(",") on it directly, so passing a list crashes.
        exclude_patterns = ",".join(
            [f"*.{ext}" for ext in config.Storage.IGNORED_EXTENSIONS] + ["view.md"]
        )
        try:
            # wait=True replaces the separate wait_processed() call and lets
            # add_resource attach telemetry to its return value. telemetry=True
            # surfaces token counts + duration so we can tell, per run, whether
            # the sync was pure-CPU (zero tokens) or hit the Gemini embedding API.
            result = self.client.add_resource(
                path=target_path, to=target_uri, exclude=exclude_patterns,
                wait=True, telemetry=True,
            )
            summary_line = _format_token_summary((result or {}).get("telemetry"))
            if summary_line:
                logger.info(summary_line)
            else:
                # Telemetry dict absent (unexpected — different OpenViking build?);
                # fall back to the old terse line rather than silently succeed.
                logger.info(f"✅ OpenViking: Sync complete for {label}")
            return True
        except Exception as e:
            logger.error(f"❌ OpenViking: Sync failed for {label}: {e}")
            return False

    def _full_rebuild(self, force: bool):
        """Hash-gated full rebuild. Compares a MindSpace-level fingerprint of
        every indexable .md under Channels/ against a persisted cache; skips
        OpenViking entirely when nothing changed, otherwise syncs only the
        channels whose aggregate changed. Force=True bypasses the cache and
        issues a single full-tree add_resource (same path as pre-hash-gate)."""
        cache_path = os.path.join(config.Paths.VIKING_DATA, _HASH_CACHE_FILENAME)
        current = _hash_tree(self.root_path)
        cached = None if force else _load_hash_cache(cache_path)

        if cached is not None and current["aggregate"] == cached.get("aggregate"):
            logger.info(
                f"✅ OpenViking: KB unchanged since last sync — skipping "
                f"(tree_hash={current['aggregate'][:12]}, 0 tokens)"
            )
            return

        if force:
            logger.info(
                f"⚙️ OpenViking: forced full rebuild (MINDSPACE_FORCE_REINDEX=1); "
                f"syncing entire tree ({self.root_path})..."
            )
            ok = self._invoke_add_resource(self.root_path, _CHANNELS_ROOT_URI, label=self.root_path)
            if ok:
                _save_hash_cache(cache_path, current)
            return

        changed = _diff_changed_channels(current, cached)
        if not changed:
            # Tree aggregate differs but no channel aggregates do — can happen
            # only if the cache file is malformed or was written from a different
            # layout. Safest to do a full rebuild and re-seed the cache.
            logger.warning(
                "⚠️ OpenViking: hash cache inconsistent with current state; "
                "running full-tree rebuild to re-seed."
            )
            ok = self._invoke_add_resource(self.root_path, _CHANNELS_ROOT_URI, label=self.root_path)
            if ok:
                _save_hash_cache(cache_path, current)
            return

        logger.info(
            f"⚙️ OpenViking: hash diff in {len(changed)} channel(s): "
            f"{', '.join(changed)}"
        )
        all_ok = True
        for name in changed:
            # Channels in cache but not on disk were removed externally.
            # Nothing to sync here; OpenViking cleanup of the stale URI is
            # out of scope for this change.
            if name not in current["channels"]:
                logger.info(f"⚙️ OpenViking: channel #{name} removed from disk; skipping sync.")
                continue
            self._ensure_channel_dir(name)
            target_path = os.path.join(self.root_path, name)
            target_uri = f"{_CHANNELS_ROOT_URI}/{name}"
            logger.info(f"⚙️ OpenViking: Syncing channel #{name}...")
            if not self._invoke_add_resource(target_path, target_uri, label=f"#{name}"):
                all_ok = False
        if all_ok:
            _save_hash_cache(cache_path, current)
        else:
            logger.warning(
                "⚠️ OpenViking: at least one channel sync failed; not updating "
                "hash cache (next startup will retry)."
            )

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
            logger.info("⚠️ OpenViking: Stale results found. Re-syncing Knowledge Base to ensure accuracy... (This might take a few seconds)")
            # Synchronous sync (O(N) CPU hash check, 0 tokens for unchanged files)
            self.rebuild_index(channel_name)
            # Retry the search exactly once
            return self._safe_search(query, target_uri, limit, channel_name, is_retry=True)

        return "\n\n---\n\n".join(parts) or "No relevant information found."

    def get_channel_context(self, channel_name: str, query: str = "") -> str:
        """
        Return context string scoped to a single channel.
        """
        channel_uri = _channel_dir_uri(channel_name)
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
        # Scope global search to the Channels subtree (ignoring any residual
        # top-level metadata under viking://resources/ like .overview.md that
        # OpenViking maintains for the root itself).
        return self._safe_search(query, _CHANNELS_ROOT_URI + "/", limit=10)

    def close(self):
        self.client.close()


def _sanitize(name: str) -> str:
    return name.replace(" ", "_").replace("-", "_")
