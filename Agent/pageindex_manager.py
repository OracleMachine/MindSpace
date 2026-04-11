import os
import json
import time
import glob as glob_module
from pageindex import PageIndexClient
import config
from logger import logger


_INDEX_FILE = os.path.join(config.BASE_STORAGE_PATH, ".pageindex_index.json")
_POLL_INTERVAL = 3   # seconds between readiness polls
_POLL_TIMEOUT = 120  # max seconds to wait for processing


class PageIndexManager:
    """
    Wraps the PageIndex cloud API for deep PDF document reasoning.

    Responsibilities:
    - Upload PDFs to PageIndex; wait for async processing
    - Maintain one cloud folder per Discord channel for organization
    - Persist doc_id and folder_id mappings to disk (survives bot restarts)
    - Expose deep Q&A via chat_completions scoped to a channel's documents

    Note: PageIndex is PDF-only. Markdown and other files are not submitted.
    """

    def __init__(self):
        self.client = PageIndexClient(api_key=config.PAGEINDEX_API_KEY)
        self._index = self._load_index()   # {"folders": {channel: id}, "documents": {path: doc_id}}

    # --- Persistence ---

    def _load_index(self) -> dict:
        if os.path.exists(_INDEX_FILE):
            try:
                with open(_INDEX_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"folders": {}, "documents": {}}

    def _save_index(self):
        os.makedirs(os.path.dirname(_INDEX_FILE), exist_ok=True)
        with open(_INDEX_FILE, "w") as f:
            json.dump(self._index, f, indent=2)

    # --- Folder management ---

    def get_or_create_folder(self, channel_name: str) -> str:
        """Return the PageIndex folder_id for a channel, creating it if needed."""
        if channel_name in self._index["folders"]:
            return self._index["folders"][channel_name]
        result = self.client.create_folder(name=channel_name)
        folder_id = result["folder"]["id"]
        self._index["folders"][channel_name] = folder_id
        self._save_index()
        return folder_id

    # --- Document indexing ---

    def index_document(self, file_path: str, channel_name: str) -> str:
        """
        Upload a PDF to PageIndex and wait for processing.
        Returns doc_id. Cached — won't re-upload if already indexed.
        Only processes .pdf files; raises ValueError for other types.
        """
        if os.path.splitext(file_path)[1].lower() != ".pdf":
            raise ValueError(f"PageIndex only supports PDF files, got: {file_path}")

        if file_path in self._index["documents"]:
            return self._index["documents"][file_path]

        folder_id = self.get_or_create_folder(channel_name)
        result = self.client.submit_document(file_path, folder_id=folder_id)
        doc_id = result["doc_id"]

        # Wait for processing (async API)
        deadline = time.time() + _POLL_TIMEOUT
        while not self.client.is_retrieval_ready(doc_id):
            if time.time() > deadline:
                raise TimeoutError(f"PageIndex processing timed out for {file_path}")
            time.sleep(_POLL_INTERVAL)

        self._index["documents"][file_path] = doc_id
        self._save_index()
        return doc_id

    def get_doc_ids_for_channel(self, channel_path: str) -> dict[str, str]:
        """Return {file_path: doc_id} for all indexed PDFs under channel_path."""
        return {
            path: doc_id
            for path, doc_id in self._index["documents"].items()
            if path.startswith(channel_path)
        }

    # --- Deep querying ---

    def query_channel(self, channel_name: str, channel_path: str, query: str) -> str:
        """
        Run a deep Q&A query against all indexed PDFs in a channel.
        Uses chat_completions scoped to the channel's doc_ids.
        Returns empty string if no documents are indexed.
        """
        doc_ids = list(self.get_doc_ids_for_channel(channel_path).values())
        if not doc_ids:
            return ""
        response = self.client.chat_completions(
            messages=[{"role": "user", "content": query}],
            doc_id=doc_ids,
        )
        # Response follows OpenAI chat completions schema
        return response["choices"][0]["message"]["content"].strip()

    def get_tree(self, doc_id: str) -> str:
        """Return the document tree structure as a string (for context injection)."""
        result = self.client.get_tree(doc_id)
        return json.dumps(result.get("tree", result), indent=2)

    # --- Rebuild on startup/commit ---

    def rebuild_index(self, kb_root_path: str):
        """
        Re-submit any PDF files in the KB that aren't yet indexed.
        Called after git_commit to pick up newly committed PDFs.
        Skips files already in the index; ignores failures silently.
        """
        logger.info(f"PageIndex: scanning {kb_root_path} for .pdf files...")
        all_pdfs = glob_module.glob(
            os.path.join(kb_root_path, "**/*.pdf"), recursive=True
        )
        pending = [p for p in all_pdfs if p not in self._index["documents"]]
        logger.info(
            f"PageIndex: found {len(all_pdfs)} PDF(s) total, {len(pending)} pending upload, "
            f"{len(all_pdfs) - len(pending)} already indexed"
        )
        for i, file_path in enumerate(pending, start=1):
            channel_name = os.path.relpath(file_path, kb_root_path).split(os.sep)[0]
            logger.info(f"PageIndex: [{i}/{len(pending)}] uploading {file_path} (channel={channel_name})")
            try:
                self.index_document(file_path, channel_name)
                logger.info(f"PageIndex: [{i}/{len(pending)}] ready")
            except Exception as e:
                logger.warning(f"PageIndex: [{i}/{len(pending)}] failed: {e}")
        logger.info("PageIndex: rebuild_index complete")

    # --- Preflight validation ---

    def validate(self):
        """Validate API key with a lightweight list call. Raises on failure."""
        self.client.list_documents(limit=1)
