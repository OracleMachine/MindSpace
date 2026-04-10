class MindSpaceTools:
    def __init__(self, kb):
        self.kb = kb

    def search_global_knowledge_base(self, query: str) -> str:
        """
        Search the ENTIRE MindSpace knowledge base across all channels and folders.
        Call this tool if the immediate channel context is insufficient to answer the user.

        Args:
            query: The specific topic or question to search for globally.
        """
        global_context = self.kb.get_global_context(query)
        return global_context or "No global results found for this query."
