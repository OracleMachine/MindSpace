class MindSpaceTools:
    """
    A collection of tools available to the MindSpace Agent for autonomous information retrieval.
    """
    def __init__(self, kb):
        self.kb = kb

    def list_channel_files(self, channel_name: str) -> str:
        """
        List all files and sub-folders in the CURRENT Discord channel.
        Use this to understand what research or articles are stored in the active channel.

        Args:
            channel_name: The name of the channel to list. (Automatically provided by the bot).
        """
        import os
        target_path = os.path.join(self.kb.channels_path, channel_name)
        
        if not os.path.exists(target_path):
            return f"Error: Channel folder {channel_name} does not exist."
        
        files = []
        try:
            for root, dirs, filenames in os.walk(target_path):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for f in filenames:
                    if not f.startswith('.'):
                        rel_path = os.path.relpath(os.path.join(root, f), target_path)
                        files.append(rel_path)
            
            if not files:
                return f"The channel #{channel_name} is empty."
            
            return f"Files in #{channel_name}:\n- " + "\n- ".join(sorted(files))
        except Exception as e:
            return f"Error listing channel files: {str(e)}"

    def list_global_files(self) -> str:
        """
        List all channels and top-level folders in the ENTIRE repository.
        Use this to see which channels exist in the knowledge base.
        """
        import os
        try:
            channels = [d for d in os.listdir(self.kb.channels_path) if os.path.isdir(os.path.join(self.kb.channels_path, d)) and not d.startswith('.')]
            if not channels:
                return "The repository contains no channel folders."
            return "Available Channels in KB:\n- " + "\n- ".join(sorted(channels))
        except Exception as e:
            return f"Error listing global files: {str(e)}"

    def search_channel_knowledge_base(self, query: str, channel_name: str) -> str:
        """
        Search the knowledge base of the CURRENT Discord channel. 
        Always use this first if the user is asking about topics related to the current conversation or channel theme.

        Args:
            query: The specific topic or question to search for in this channel.
            channel_name: The name of the channel to search. (Automatically provided by the bot).
        """
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
