"""
PageIndexManager — **reserved interface, no backend wired**.

The PageIndex cloud service (api.pageindex.ai) was the original backend for
deep PDF Q&A. It is currently disabled. Every public method below is a
no-op stub returning an empty value of the advertised shape so existing
callers (`KnowledgeBaseManager`, `MindSpaceAgent.analyze_file`, startup
indexing, `!research` context assembly) continue to run without changes.

When a replacement PDF deep-reasoning backend arrives, only the bodies of
the methods in this file need to change — class name, method signatures,
and return types stay as they are.

Callers that currently depend on this interface:
- `knowledgebase/manager.py` — instantiation, `index_document`, `query_channel`
- `agent/agent.py:analyze_file` — `index_document`, `get_tree`
- `main.py:_startup_indexing` — `rebuild_index`
- `bot/services.py` and `agent/tools.py` — transitively via `kb.get_deep_context`
"""

from mindspace.core.logger import logger


class PageIndexManager:
    _warned = False  # class-level flag — one log line per process, not per construction

    def __init__(self):
        if not PageIndexManager._warned:
            logger.info(
                "PageIndex integration disabled — PDF deep-reasoning calls are no-ops. "
                "Swap a real backend into knowledgebase/pageindex.py when ready."
            )
            PageIndexManager._warned = True

    # --- Folder management ---

    def get_or_create_folder(self, channel_name: str) -> str:
        return ""

    # --- Document indexing ---

    def index_document(self, file_path: str, channel_name: str) -> str | None:
        return None

    def get_doc_ids_for_channel(self, channel_path: str) -> dict[str, str]:
        return {}

    # --- Deep querying ---

    def query_channel(self, channel_name: str, channel_path: str, query: str) -> str:
        return ""

    def get_tree(self, doc_id: str) -> str:
        return ""

    # --- Rebuild on startup/commit ---

    def rebuild_index(self, kb_root_path: str) -> None:
        return None

    # --- Preflight validation ---

    def validate(self) -> None:
        return None
