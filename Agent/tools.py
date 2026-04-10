import contextvars

# Context variable to hold the current channel name for the tool call
# This ensures concurrency safety when multiple messages are handled at once.
current_channel_context = contextvars.ContextVar("current_channel_context", default=None)

class MindSpaceTools:
    """
    A collection of tools available to the MindSpace Agent for autonomous information retrieval.
    """
    def __init__(self, kb):
        self.kb = kb

    def search_channel_knowledge_base(self, query: str) -> str:
        """
        Search the knowledge base of the CURRENT Discord channel. 
        Always use this first if the user is asking about topics related to the current conversation or channel theme.

        Args:
            query: The specific topic or question to search for in this channel.
        """
        channel_name = current_channel_context.get()
        if not channel_name:
            return "Error: No channel context bound to tools."
        
        context = self.kb.get_channel_context(channel_name, query)
        deep = self.kb.get_deep_context(channel_name, query)
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
