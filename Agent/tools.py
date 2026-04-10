class MindSpaceTools:
    """
    A collection of tools available to the MindSpace Agent for autonomous information retrieval.
    """
    def __init__(self, kb, channel_name: str = None):
        self.kb = kb
        self.channel_name = channel_name

    def search_channel_knowledge_base(self, query: str) -> str:
        """
        Search the knowledge base of the CURRENT Discord channel. 
        Always use this first if the user is asking about topics related to the current conversation or channel theme.

        Args:
            query: The specific topic or question to search for in this channel.
        """
        if not self.channel_name:
            return "Error: No channel context bound to tools."
        
        context = self.kb.get_channel_context(self.channel_name, query)
        deep = self.kb.get_deep_context(self.channel_name, query)
        combined = ""
        if context:
            combined += f"--- Semantic Overview (Viking) ---\n{context}\n\n"
        if deep:
            combined += f"--- Deep Document Analysis (PageIndex) ---\n{deep}\n\n"
        
        return combined or "No relevant information found in this channel's knowledge base."

    def search_global_knowledge_base(self, query: str) -> str:
        """
        Search the ENTIRE MindSpace repository across all channels and folders.
        Use this if the channel-specific search yielded no results or if the user is asking 
        a broad question that spans multiple domains.

        Args:
            query: The specific topic or question to search for across the whole KB.
        """
        global_context = self.kb.get_global_context(query)
        return global_context or "No information found in the global knowledge base."
