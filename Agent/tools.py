import os
import logging
from logger import logger

class MindSpaceTools:
    """
    A collection of tools available to the MindSpace Agent for autonomous information retrieval.
    """
    def __init__(self, kb):
        self.kb = kb

    def _generate_tree(self, startpath: str) -> str:
        """Helper to generate a ASCII tree representation of a directory."""
        tree = []
        
        def walk_dir(path, prefix=""):
            try:
                entries = sorted(os.listdir(path))
            except OSError:
                return
            # Filter hidden
            entries = [e for e in entries if not e.startswith('.')]
            
            count = len(entries)
            for i, entry in enumerate(entries):
                full_path = os.path.join(path, entry)
                is_last = (i == count - 1)
                
                connector = "└── " if is_last else "├── "
                
                if os.path.isdir(full_path):
                    tree.append(f"{prefix}{connector}{entry}/")
                    new_prefix = prefix + ("    " if is_last else "│   ")
                    walk_dir(full_path, new_prefix)
                else:
                    tree.append(f"{prefix}{connector}{entry}")

        root_name = os.path.basename(startpath.rstrip(os.sep))
        tree.append(f"{root_name}/")
        walk_dir(startpath)
        return "\n".join(tree)

    def list_channel_files(self, channel_name: str) -> str:
        """
        List all files and sub-folders in the CURRENT Discord channel in a tree structure.
        Use this to understand what research or articles are stored in the active channel.
        """
        logger.debug(f"Tool Execution: list_channel_files for #{channel_name}")
        target_path = os.path.join(self.kb.channels_path, channel_name)
        
        if not os.path.exists(target_path):
            return f"Error: Channel folder {channel_name} does not exist."
        
        try:
            tree_output = self._generate_tree(target_path)
            if len(tree_output.splitlines()) <= 1:
                return f"The channel #{channel_name} is empty."
            return f"File structure for #{channel_name}:\n```\n{tree_output}\n```"
        except Exception as e:
            logger.error(f"Tool Error: list_channel_files failed: {e}")
            return f"Error listing channel files: {str(e)}"

    def search_channel_knowledge_base(self, query: str, channel_name: str) -> str:
        """
        Search the knowledge base of the CURRENT Discord channel. 
        Always use this first if the user is asking about topics related to the current conversation or channel theme.

        Args:
            query: The specific topic or question to search for in this channel.
        """
        logger.debug(f"Tool Execution: search_channel_knowledge_base for #{channel_name} query='{query}'")
        try:
            context = self.kb.get_channel_context(channel_name, query)
            deep = self.kb.get_deep_context(channel_name, query)
            combined = ""
            if context:
                combined += f"--- Semantic Overview (Viking) ---\n{context}\n\n"
            if deep:
                combined += f"--- Deep Document Analysis (PageIndex) ---\n{deep}\n\n"
            
            return combined or "No relevant information found in this channel's knowledge base."
        except Exception as e:
            logger.error(f"Tool Error: search_channel_knowledge_base failed: {e}")
            return f"Error searching channel KB: {str(e)}"

    def search_global_knowledge_base(self, query: str) -> str:
        """
        Search the ENTIRE MindSpace repository across all channels and folders.
        Use this if the channel-specific search yielded no results or if the user is asking 
        a broad question that spans multiple domains.

        Args:
            query: The specific topic or question to search for across the whole KB.
        """
        logger.debug(f"Tool Execution: search_global_knowledge_base query='{query}'")
        try:
            global_context = self.kb.get_global_context(query)
            return global_context or "No information found in the global knowledge base."
        except Exception as e:
            logger.error(f"Tool Error: search_global_knowledge_base failed: {e}")
            return f"Error searching global KB: {str(e)}"

    def list_global_files(self) -> str:
        """
        List all channels and top-level folders in the ENTIRE repository in a tree structure.
        Use this to see which channels exist in the knowledge base and how they are organized.
        """
        logger.debug("Tool Execution: list_global_files")
        try:
            tree_output = self._generate_tree(self.kb.channels_path)
            if len(tree_output.splitlines()) <= 1:
                return "The repository contains no channel folders."
            return f"Global Knowledge Base structure:\n```\n{tree_output}\n```"
        except Exception as e:
            logger.error(f"Tool Error: list_global_files failed: {e}")
            return f"Error listing global files: {str(e)}"

    def get_tools(self, channel_name: str):
        """
        Returns a list of tools bound to the specific channel.
        Uses explicit wrapper functions to ensure clean signatures and metadata for the SDK.
        """

        def list_channel_files() -> str:
            """
            List all files and sub-folders in the CURRENT Discord channel in a tree structure.
            """
            return self.list_channel_files(channel_name)

        def search_channel_knowledge_base(query: str) -> str:
            """
            Search the knowledge base of the CURRENT Discord channel.
            Args:
                query: The topic or question to search for.
            """
            return self.search_channel_knowledge_base(query, channel_name)

        def record_thought(summary: str) -> str:
            """
            Record a valuable insight extracted from the conversation into this channel's
            stream of consciousness. Call this when the user shares noteworthy information,
            analysis, or conclusions worth preserving for future reference.
            Args:
                summary: A concise summary of the insight to record.
            """
            logger.debug(f"Tool Execution: record_thought for #{channel_name}: {summary}")
            try:
                self.kb.append_thought(channel_name, summary)
                return f"Thought recorded: {summary}"
            except Exception as e:
                logger.error(f"Tool Error: record_thought failed: {e}")
                return f"Error recording thought: {str(e)}"

        return [
            list_channel_files,
            search_channel_knowledge_base,
            self.search_global_knowledge_base,
            self.list_global_files,
            record_thought,
        ]
