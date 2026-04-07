import os

try:
    import openviking as ov
    OPENVIKING_AVAILABLE = True
except ImportError:
    OPENVIKING_AVAILABLE = False

class VikingContextManager:
    """
    Wraps OpenViking for two-mode context management:
    - Channel-scoped: default for all messages and single-channel commands.
    - Global: activated by !omni to traverse all channel folders.

    Falls back to reading index.md files directly if OpenViking is not installed.
    Replace the TODO blocks below with actual OpenViking API calls once confirmed.
    """

    def __init__(self, root_path: str):
        self.root_path = root_path
        if OPENVIKING_AVAILABLE:
            # TODO: Initialize OpenViking index pointed at the server root
            # self._index = ov.Index(root_path, uri_prefix="viking://")
            pass

    def rebuild_index(self):
        """Re-index the entire KB after every git commit."""
        if OPENVIKING_AVAILABLE:
            # TODO: Trigger OpenViking to re-crawl and update its index
            # self._index.rebuild()
            pass

    def get_channel_context(self, channel_name: str) -> list:
        """
        Return context for a single channel (L0/L1 layers).
        Used by all default operations: dialogue, !research, !consolidate.
        """
        channel_path = os.path.join(self.root_path, _sanitize(channel_name))
        if OPENVIKING_AVAILABLE:
            # TODO: Return OpenViking L1 context slices scoped to channel_path
            # return self._index.get_context(channel_path, level="L1")
            pass
        # Fallback: pass index.md directly (current behaviour)
        index_file = os.path.join(channel_path, "index.md")
        return [index_file] if os.path.exists(index_file) else []

    def get_global_context(self, query: str) -> list:
        """
        Traverse ALL channel folders in the server root and return relevant slices.
        Used exclusively by !omni.
        """
        if OPENVIKING_AVAILABLE:
            # TODO: Run a server-wide OpenViking query returning L1 slices ranked by relevance
            # return self._index.query(query, scope="global", level="L1")
            pass
        # Fallback: collect every channel's index.md
        context_files = []
        for entry in os.scandir(self.root_path):
            if entry.is_dir() and not entry.name.startswith("."):
                index_file = os.path.join(entry.path, "index.md")
                if os.path.exists(index_file):
                    context_files.append(index_file)
        return context_files


def _sanitize(name: str) -> str:
    return name.replace(" ", "_").replace("-", "_")
