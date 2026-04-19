import os
import re
from datetime import datetime, timezone
from pathlib import Path
import openviking as ov  # hard import — fails fast if package not installed
from mindspace.core import config
from mindspace.core.logger import logger

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


# Python's datetime only supports microseconds; trim OV's nanosecond precision
# before fromisoformat so "2026-02-11T16:52:16.256334192+08:00" -> ".256334+08:00".
_NANOS_TRIM_RE = re.compile(r"(\.\d{6})\d+")


def _parse_ov_modtime(s: str | None) -> datetime | None:
    """Parse OpenViking's modTime (ISO 8601, possibly 'Z' suffix or nanosecond
    precision) to a timezone-aware datetime. Returns None on parse failure —
    the caller treats that as 'no OV data, assume dirty'."""
    if not s:
        return None
    normalized = _NANOS_TRIM_RE.sub(r"\1", s.replace("Z", "+00:00"))
    try:
        dt = datetime.fromisoformat(normalized)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _max_local_mtime(channel_dir: Path) -> datetime | None:
    """Max mtime across all .md files under `channel_dir` (excluding view.md,
    to mirror the OpenViking exclude set). Returns None for an empty or
    non-existent channel."""
    if not channel_dir.is_dir():
        return None
    best: float | None = None
    for p in channel_dir.rglob("*.md"):
        if p.name == "view.md":
            continue
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if best is None or m > best:
            best = m
    if best is None:
        return None
    return datetime.fromtimestamp(best, tz=timezone.utc)


def _max_remote_modtime(client, channel_uri: str) -> datetime | None:
    """Max modTime across every entry returned by OpenViking's `tree()` under
    `channel_uri`. Includes OV's own `.abstract.md` / `.overview.md` entries —
    which is exactly what we want, since their modTime tracks the last time
    OV summarized this channel, i.e. the freshness floor of our index.
    Returns None if OV has nothing there yet (treated as 'dirty')."""
    try:
        entries = client.tree(channel_uri)
    except Exception:
        return None
    best: datetime | None = None
    for e in entries or []:
        dt = _parse_ov_modtime(e.get("modTime"))
        if dt is not None and (best is None or dt > best):
            best = dt
    return best


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
        Sync disk state with OpenViking.

        - `channel_name=None, force=False` (startup): per-channel dirty check.
          For each channel on disk, compare the max local .md mtime against
          the max modTime of OV's `tree(channel_uri)`. If local is newer
          (or OV has no data) → that channel is dirty → sync with summaries
          (`build_index=True`). Otherwise → file-only sync (`build_index=False`)
          to keep OV's filesystem mirror aligned without burning LLM tokens.
          No local cache; OV's own timestamps are the freshness marker.
        - `channel_name=None, force=True`: full-tree rebuild with summaries
          on. The "re-seed everything from scratch" escape hatch, wired to
          `MINDSPACE_FORCE_REINDEX=1`.
        - `channel_name=<name>`: single-channel rebuild with summaries on.
          Used by `/sync` and by `_safe_search`'s stale-result retry.

        Uses `to=<exact target URI>` rather than `parent=<root>`. OpenViking's
        tree_builder calls `_resolve_unique_uri` on the `parent=` path when the
        candidate URI already exists, producing `Channels`, `Channels_1`, ...
        siblings on each restart. `to=` skips that resolver and writes into the
        exact URI, overwriting in place.
        """
        if channel_name is None:
            if force:
                logger.info(
                    f"⚙️ OpenViking: forced full rebuild (MINDSPACE_FORCE_REINDEX=1); "
                    f"syncing entire tree with summaries ({self.root_path})..."
                )
                self._invoke_add_resource(
                    self.root_path, _CHANNELS_ROOT_URI,
                    label=self.root_path, build_index=True,
                )
            else:
                self._startup_sync_with_dirty_check()
            return

        self._ensure_channel_dir(channel_name)
        target_path = os.path.join(self.root_path, channel_name)
        target_uri = f"{_CHANNELS_ROOT_URI}/{channel_name}"
        logger.info(f"⚙️ OpenViking: Syncing channel #{channel_name} with summaries...")
        self._invoke_add_resource(
            target_path, target_uri, label=f"#{channel_name}", build_index=True,
        )

    def _startup_sync_with_dirty_check(self):
        """Per-channel startup dispatcher. Catches external edits that didn't
        flow through `index_file` — a user editing files directly on disk
        bumps their mtime, which ends up newer than OV's last-index modTime,
        which flags the channel as dirty."""
        root = Path(self.root_path)
        if not root.is_dir():
            logger.warning(
                f"OpenViking: channels root {root} does not exist; "
                f"skipping startup sync."
            )
            return

        channel_dirs = sorted(
            (p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=lambda p: p.name,
        )
        if not channel_dirs:
            logger.info(f"OpenViking: no channels under {root}; nothing to sync.")
            return

        dirty: list[Path] = []
        clean: list[Path] = []
        for chan_dir in channel_dirs:
            local = _max_local_mtime(chan_dir)
            if local is None:
                # Empty channel (no .md files) — skip entirely; nothing to sync.
                continue
            remote = _max_remote_modtime(self.client, f"{_CHANNELS_ROOT_URI}/{chan_dir.name}")
            if remote is None or local > remote:
                dirty.append(chan_dir)
            else:
                clean.append(chan_dir)

        logger.info(
            f"⚙️ OpenViking: startup dirty-check → "
            f"{len(dirty)} dirty, {len(clean)} clean out of "
            f"{len(channel_dirs)} channel(s)."
            + (f" Dirty: {', '.join(p.name for p in dirty)}." if dirty else "")
        )

        for chan_dir in clean:
            self._sync_channel_dir(chan_dir, build_index=False,
                                   log=f"⚙️ OpenViking: #{chan_dir.name} up-to-date — file-only sync...")
        for chan_dir in dirty:
            self._sync_channel_dir(chan_dir, build_index=True,
                                   log=f"⚙️ OpenViking: #{chan_dir.name} has external edits — refreshing summaries...")

    def _sync_channel_dir(self, chan_dir: Path, *, build_index: bool, log: str):
        """Single per-channel sync call. Ensures the channel URI exists, logs
        the intent, and delegates to `_invoke_add_resource` with the chosen
        summary flag."""
        self._ensure_channel_dir(chan_dir.name)
        chan_uri = f"{_CHANNELS_ROOT_URI}/{chan_dir.name}"
        logger.info(log)
        self._invoke_add_resource(
            str(chan_dir), chan_uri, label=f"#{chan_dir.name}", build_index=build_index,
        )

    def _invoke_add_resource(
        self, target_path: str, target_uri: str, label: str, build_index: bool,
    ) -> bool:
        """The actual OpenViking call. Returns True on success, False on error
        (logged, not re-raised — matches the prior swallow-and-log contract).

        `build_index=False` is the critical cheap path: OpenViking still
        persists files to its internal filesystem (Phase 3 of the ingestion
        pipeline runs unconditionally), but skips Phase 4 — the semantic
        summarizer + vectorizer that burns LLM tokens regenerating
        `.abstract.md` / `.overview.md` for every directory in the tree.
        Safe at startup because existing vectors and summaries persist in
        OV's store across runs; we only need to refresh them when files
        actually change."""
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
                build_index=build_index,
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
