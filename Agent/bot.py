import discord
import os
import datetime
import config
import asyncio
from agent import MindSpaceAgent
from manager import KnowledgeBaseManager
from logger import logger

class MindSpaceBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.bind_bot(self)
        self.agent = MindSpaceAgent()
        self.kb = None  # Unified KnowledgeBaseManager, initialized in on_ready

    async def setup_hook(self):
        """Start background tasks."""
        self.loop.create_task(logger.process_log_queue())

    async def on_ready(self):
        if not self.guilds:
            logger.warning("Bot is not in any guilds.")
            return

        # Initialize the SINGLE KnowledgeBaseManager for the first guild
        guild = self.guilds[0]
        if self.kb is None:
            self.kb = KnowledgeBaseManager(guild.name)
            logger.info(f"Initialized KnowledgeBaseManager for server: {guild.name}")
        
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logger.info('------')
        
        await self._ensure_system_log(guild)
        await self._sync_kb_channels(guild)
        
        # Initial indexing (full sync on startup)
        self.kb.viking.rebuild_index()
        self.kb.pageindex.rebuild_index(self.kb.channels_path)
        
        for channel in guild.text_channels:
            if channel.name != "system-log":
                await self._seed_channel_history(channel)

    async def _sync_kb_channels(self, guild):
        """Create Discord channels for any KB folders that don't have a matching channel."""
        existing_names = {ch.name for ch in guild.text_channels}
        root = config.CHANNELS_PATH
        try:
            for entry in os.scandir(root):
                if not entry.is_dir() or entry.name.startswith(".") or entry.name == "system-log":
                    continue
                channel_name = entry.name
                self.kb.get_channel_path(channel_name)  # ensure stream_of_conscious.md exists
                if channel_name not in existing_names:
                    try:
                        await guild.create_text_channel(channel_name)
                        logger.info(f"Created Discord channel #{channel_name} for existing KB folder", guild)
                    except Exception as e:
                        logger.error(f"Could not create channel #{channel_name}: {e}", guild)
        except Exception as e:
            logger.error(f"Error syncing KB channels: {e}", guild)

    async def _seed_channel_history(self, channel):
        """Seed chat_history.txt from Discord message history if no local file exists."""
        entries = []
        try:
            async for message in channel.history(limit=50, oldest_first=False):
                if not message.content.strip():
                    continue
                role = "assistant" if message.author == self.user else message.author.display_name
                timestamp = message.created_at.strftime("%Y-%m-%d %H:%M")
                entries.append(f"[{timestamp}] {role}:\n{message.content}\n\n")
        except Exception as e:
            logger.error(f"Could not seed history for #{channel.name}: {e}")
            return
        if entries:
            text = "".join(reversed(entries))  # oldest first
            self.kb.seed_history(channel.name, text)
            logger.info(f"Seeded #{channel.name} with {len(entries)} messages from Discord")

    async def on_guild_join(self, guild):
        """Enforce single-server constraint: leave immediately if already serving a server."""
        if self.kb is not None:
            logger.error(
                f"Attempted to join a second server '{guild.name}'. This bot is SINGLE-SERVER ONLY. Leaving.",
                self.guilds[0]
            )
            await guild.leave()
            return
        
        # If this is the first guild, it will be handled by on_ready if not already initialized
        self.kb = KnowledgeBaseManager(guild.name)
        await self._ensure_system_log(guild)

    async def _ensure_system_log(self, guild):
        """Ensure a #system-log channel exists in the guild."""
        channel = discord.utils.get(guild.text_channels, name="system-log")
        if not channel:
            try:
                channel = await guild.create_text_channel("system-log")
                await channel.send("🚀 **MindSpace System Log Initialized.**")
            except Exception as e:
                logger.error(f"Error creating system-log in {guild.name}: {e}")
        return channel

    async def send_system_log(self, guild, message):
        """Internal method for logger to send to Discord."""
        channel = await self._ensure_system_log(guild)
        if channel:
            await channel.send(message)

    async def send_message_safe(self, channel, content):
        """Send a message, splitting into multiple parts if it exceeds Discord's 2000-char limit."""
        if not content:
            return
        
        # Split by 2000 chars, ideally at a newline
        limit = 2000
        while len(content) > limit:
            # Try to find the last newline before the limit
            split_at = content.rfind('\n', 0, limit)
            if split_at == -1:
                split_at = limit
            
            chunk = content[:split_at].strip()
            if chunk:
                await channel.send(chunk)
            content = content[split_at:].strip()
        
        if content:
            await channel.send(content)

    async def on_message(self, message):
        if message.author == self.user:
            return

        channel_name = message.channel.name
        channel_path = self.kb.get_channel_path(channel_name)

        # --- 1. HANDLE ACTIVE COMMANDS ---
        if message.content.startswith('!'):
            command_parts = message.content[1:].split(' ', 1)
            cmd = command_parts[0].lower()
            args = command_parts[1] if len(command_parts) > 1 else ""

            if cmd == "organize":
                await message.channel.send("🔄 Re-syncing and organizing knowledge base...")
                untracked = self.kb.list_untracked_files()
                reasoning = self.agent.run_command(f"Organize these new files: {untracked}. Decide semantic hierarchy.")

                commit_msg = self.agent.generate_commit_message(f"Organized files in {channel_name}: {reasoning}")
                self.kb.git_commit(commit_msg)
                await self.send_message_safe(message.channel, f"✅ Organization complete. Reasoning: {reasoning}")
                logger.info(f"**ORGANIZE**: {channel_name} - {commit_msg}", message.guild)

            elif cmd == "consolidate":
                await message.channel.send("📑 Consolidating stream of consciousness...")
                stream_file = os.path.join(channel_path, "stream_of_conscious.md")

                with open(stream_file, "r") as f:
                    content = f.read()

                synthesis = self.agent.run_command(f"Synthesize these thoughts into a structured permanent article:\n\n{content}")
                filename = f"ARTICLE-{datetime.date.today()}-{message.id}.md"
                file_path = os.path.join(channel_path, filename)
                self.kb.write_file(file_path, synthesis)

                self.kb.write_file(stream_file, f"# Stream of Consciousness: {channel_name}\n\n")

                commit_msg = self.agent.generate_commit_message(f"Consolidated thoughts in {channel_name} into {filename}")
                self.kb.git_commit(commit_msg)

                await message.channel.send(f"✅ Consolidation complete. Saved to: {file_path}", file=discord.File(file_path))
                logger.info(f"**CONSOLIDATE**: {channel_name} -> `{filename}`", message.guild)

            elif cmd == "research":
                await message.channel.send(f"🔬 Synthesizing research on: {args}...")
                viking_context = self.kb.get_channel_context(channel_name, query=args)
                deep_context = self.kb.get_deep_context(channel_name, args)
                combined = ""
                if viking_context:
                    combined += f"--- Semantic Overview (Viking) ---\n{viking_context}\n\n"
                if deep_context:
                    combined += f"--- Deep Document Analysis (PageIndex) ---\n{deep_context}\n\n"
                research_res = self.agent.run_command(
                    f"Perform deep research on {args} using the provided KB context, citing specific sources.",
                    context=combined or None
                )

                filename = f"RESEARCH-{datetime.date.today()}-{message.id}.md"
                file_path = os.path.join(channel_path, filename)

                lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- Discord: {message.jump_url}\n- URI: viking://{message.guild.name}/{channel_name}/{filename}"
                self.kb.write_file(file_path, research_res + lineage)

                commit_msg = self.agent.generate_commit_message(f"Research synthesis on {args}")
                self.kb.git_commit(commit_msg)

                await message.channel.send(f"✅ Research complete.", file=discord.File(file_path))
                logger.info(f"**RESEARCH**: {args} in {channel_name}", message.guild)

            elif cmd == "omni":
                if not args:
                    await message.channel.send("Usage: `!omni [query]`")
                    return
                await message.channel.send(f"🌐 Traversing entire knowledge base for: {args}...")
                global_context = self.kb.get_global_context(args)
                result = self.agent.run_command(
                    f"Using the full knowledge base context across all channels, answer this query comprehensively with citations: {args}",
                    context=global_context
                )
                filename = f"OMNI-{datetime.date.today()}-{message.id}.md"
                file_path = os.path.join(channel_path, filename)
                lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- Discord: {message.jump_url}\n- URI: viking://{message.guild.name}/omni/{filename}"
                self.kb.write_file(file_path, result + lineage)
                commit_msg = self.agent.generate_commit_message(f"Omni query synthesis: {args}")
                self.kb.git_commit(commit_msg)
                await message.channel.send(f"✅ Omni search complete.", file=discord.File(file_path))
                logger.info(f"**OMNI**: {args}", message.guild)

        # --- 2. HANDLE KNOWLEDGE INGESTION (Links/Files) ---
        elif "http://" in message.content or "https://" in message.content:
            await message.channel.send("🌐 URL detected. Snapshotting to KB...")
            markdown_snapshot = self.agent.process_url(message.content, channel_name)

            filename = f"WEBPAGE-{datetime.date.today()}-{message.id}.md"
            file_path = os.path.join(channel_path, filename)
            self.kb.write_file(file_path, markdown_snapshot)

            commit_msg = self.agent.generate_commit_message(f"Ingested webpage snapshot: {filename}")
            self.kb.git_commit(commit_msg)
            await self.send_message_safe(message.channel, f"✅ Link ingested and snapshotted: {file_path}")
            logger.info(f"**INGEST (URL)**: {message.content[:50]}... -> `{filename}`", message.guild)

        elif message.attachments:
            await message.channel.send("📥 File detected. Ingesting to KB...")
            for attachment in message.attachments:
                file_path = os.path.join(channel_path, attachment.filename)
                await attachment.save(file_path)

                doc_id, analysis = self.agent.analyze_file(file_path, self.kb.pageindex)
                if doc_id:
                    logger.info(f"**PAGEINDEX**: Indexed `{attachment.filename}` doc_id={doc_id}", message.guild)

                commit_msg = self.agent.generate_commit_message(f"Ingested file: {attachment.filename}")
                self.kb.git_commit(commit_msg)
                await self.send_message_safe(message.channel, f"✅ File ingested: {attachment.filename}. Analysis: {analysis}")
                logger.info(f"**INGEST (FILE)**: `{attachment.filename}` in {channel_name}", message.guild)

        # --- 3. PASSIVE THOUGHT RECORDING (Active Dialogue) ---
        else:
            reply, thought = self.agent.engage_dialogue(
                message.content,
                channel_name,
                context=self.kb.get_channel_context(channel_name, query=message.content),
                history=self.kb.get_history(channel_name),
                stream_content=self.kb.get_stream_content(channel_name)
            )

            self.kb.append_history(channel_name, message.author.display_name, message.content)
            self.kb.append_history(channel_name, "assistant", reply)

            if thought:
                self.kb.append_thought(channel_name, thought)
                logger.info(f"💭 **THOUGHT**: Extracted in {channel_name}: {thought}", message.guild)

            await self.send_message_safe(message.channel, reply)

def _preflight_check():
    """
    Verify all required dependencies are installed and API keys are valid.
    Uses lightweight checks to avoid redundant manager initialization.
    """
    # 1. PageIndex: check install
    try:
        from pageindex import PageIndexClient
    except ImportError:
        raise RuntimeError("PageIndex is not installed. Run: pip install pageindex")

    # 2. OpenViking: check install
    try:
        import openviking as ov
    except ImportError:
        raise RuntimeError("OpenViking is not installed. Run: pip install openviking")

    # 3. GitPython: check install
    try:
        import git
    except ImportError:
        raise RuntimeError("GitPython is not installed. Run: pip install GitPython")

    # 4. API Key Validation (Minimal live calls)
    try:
        # PageIndex validation
        pi_client = PageIndexClient(api_key=config.PAGEINDEX_API_KEY)
        pi_client.list_documents(limit=1)
        
        # OpenViking validation
        os.environ.setdefault("OPENVIKING_CONFIG_FILE", config.OPENVIKING_CONF_PATH)
        ov_client = ov.SyncOpenViking(path=config.OPENVIKING_DATA_PATH)
        ov_client.initialize()
        ov_client.find("preflight check", limit=1)
        ov_client.close()
    except Exception as e:
        raise RuntimeError(f"API key validation failed: {e}")


if __name__ == "__main__":
    required_vars = {
        "DISCORD_TOKEN": config.DISCORD_TOKEN,
        "GEMINI_API_KEY": config.GEMINI_API_KEY,
        "PAGEINDEX_API_KEY": config.PAGEINDEX_API_KEY,
    }

    missing = [var for var, val in required_vars.items() if not val]
    if missing:
        logger.error(f"❌ Missing required environment variables: {', '.join(missing)}")
        logger.error("Please ensure they are exported in your ZSH environment (~/.zshrc).")
        exit(1)

    try:
        _preflight_check()
    except RuntimeError as e:
        logger.error(f"❌ Preflight check failed: {e}")
        exit(1)

    logger.info("✅ Preflight passed. Starting MindSpace Bot...")
    intents = discord.Intents.default()
    intents.message_content = True
    bot = MindSpaceBot(intents=intents)
    bot.run(config.DISCORD_TOKEN)
