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
        # Bind the global logger to this bot instance
        logger.bind_bot(self)
        self.agent = MindSpaceAgent()
        self.kb_managers = {}  # Map server_id to KnowledgeBaseManager instance

    async def setup_hook(self):
        """Start background tasks."""
        self.loop.create_task(logger.process_log_queue())

    async def on_ready(self):
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logger.info('------')
        for guild in self.guilds:
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

    def _get_manager(self, guild):
        """Lazy-load manager for the specific server."""
        if guild.id not in self.kb_managers:
            self.kb_managers[guild.id] = KnowledgeBaseManager(guild.name)
        return self.kb_managers[guild.id]

    async def on_message(self, message):
        if message.author == self.user:
            return

        kb = self._get_manager(message.guild)
        channel_name = message.channel.name
        channel_path = kb.get_channel_path(channel_name)

        # --- 1. HANDLE ACTIVE COMMANDS ---
        if message.content.startswith('!'):
            command_parts = message.content[1:].split(' ', 1)
            cmd = command_parts[0].lower()
            args = command_parts[1] if len(command_parts) > 1 else ""

            if cmd == "organize":
                await message.channel.send("🔄 Re-syncing and organizing knowledge base...")
                untracked = kb.list_untracked_files()
                reasoning = self.agent.run_command(f"Organize these new files: {untracked}. Decide semantic hierarchy.")
                
                # Perform organizational Git commit
                commit_msg = self.agent.generate_commit_message(f"Organized files in {channel_name}: {reasoning}")
                kb.git_commit(commit_msg)
                await message.channel.send(f"✅ Organization complete. Reasoning: {reasoning}")
                logger.info(f"**ORGANIZE**: {channel_name} - {commit_msg}", message.guild)

            elif cmd == "consolidate":
                await message.channel.send("📑 Consolidating stream of consciousness...")
                stream_file = os.path.join(channel_path, "STREAM_OF_CONSCIOUS.MD")
                
                with open(stream_file, "r") as f:
                    content = f.read()
                
                # Synthesize and create new article
                synthesis = self.agent.run_command(f"Synthesize these thoughts into a structured permanent article:\n\n{content}")
                filename = f"ARTICLE-{datetime.date.today()}-{message.id}.MD"
                file_path = os.path.join(channel_path, filename)
                kb.write_file(file_path, synthesis)
                
                # Clear Stream
                kb.write_file(stream_file, f"# Stream of Consciousness: {channel_name}\n\n")
                
                # Commit and Instant Delivery
                commit_msg = self.agent.generate_commit_message(f"Consolidated thoughts in {channel_name} into {filename}")
                kb.git_commit(commit_msg)
                
                await message.channel.send(f"✅ Consolidation complete. Saved to: {file_path}", file=discord.File(file_path))
                logger.info(f"**CONSOLIDATE**: {channel_name} -> `{filename}`", message.guild)

            elif cmd == "research":
                await message.channel.send(f"🔬 Synthesizing research on: {args}...")
                research_res = self.agent.run_command(f"Perform deep research on {args} using current KB context.", kb.get_channel_context(channel_name))
                
                filename = f"RESEARCH-{datetime.date.today()}-{message.id}.MD"
                file_path = os.path.join(channel_path, filename)
                
                # Append lineage as per SPEC.MD
                lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- Discord: {message.jump_url}\n- URI: viking://{message.guild.name}/{channel_name}/{filename}"
                kb.write_file(file_path, research_res + lineage)
                
                commit_msg = self.agent.generate_commit_message(f"Research synthesis on {args}")
                kb.git_commit(commit_msg)
                
                await message.channel.send(f"✅ Research complete.", file=discord.File(file_path))
                logger.info(f"**RESEARCH**: {args} in {channel_name}", message.guild)

            elif cmd == "omni":
                if not args:
                    await message.channel.send("Usage: `!omni [query]`")
                    return
                await message.channel.send(f"🌐 Traversing entire knowledge base for: {args}...")
                global_context = kb.get_global_context(args)
                result = self.agent.run_command(
                    f"Using the full knowledge base context across all channels, answer this query comprehensively with citations: {args}",
                    global_context
                )
                filename = f"OMNI-{datetime.date.today()}-{message.id}.MD"
                file_path = os.path.join(channel_path, filename)
                lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- Discord: {message.jump_url}\n- URI: viking://{message.guild.name}/omni/{filename}"
                kb.write_file(file_path, result + lineage)
                commit_msg = self.agent.generate_commit_message(f"Omni query synthesis: {args}")
                kb.git_commit(commit_msg)
                await message.channel.send(f"✅ Omni search complete.", file=discord.File(file_path))
                logger.info(f"**OMNI**: {args}", message.guild)

        # --- 2. HANDLE KNOWLEDGE INGESTION (Links/Files) ---
        elif "http://" in message.content or "https://" in message.content:
            await message.channel.send("🌐 URL detected. Snapshotting to KB...")
            # Use Gemini SDK to process URL content directly
            markdown_snapshot = self.agent.process_url(message.content, channel_name)
            
            filename = f"WEBPAGE-{datetime.date.today()}-{message.id}.MD"
            file_path = os.path.join(channel_path, filename)
            kb.write_file(file_path, markdown_snapshot)
            
            commit_msg = self.agent.generate_commit_message(f"Ingested webpage snapshot: {filename}")
            kb.git_commit(commit_msg)
            await message.channel.send(f"✅ Link ingested and snapshotted: {file_path}")
            logger.info(f"**INGEST (URL)**: {message.content[:50]}... -> `{filename}`", message.guild)

        elif message.attachments:
            await message.channel.send("📥 File detected. Ingesting to KB...")
            for attachment in message.attachments:
                file_path = os.path.join(channel_path, attachment.filename)
                await attachment.save(file_path)
                
                # Use Gemini SDK to analyze and place semantically
                analysis = self.agent.run_command(f"Analyze this file: {file_path}. Determine human-readable slug and placement.")
                
                commit_msg = self.agent.generate_commit_message(f"Ingested file: {attachment.filename}")
                kb.git_commit(commit_msg)
                await message.channel.send(f"✅ File ingested: {attachment.filename}. Analysis: {analysis}")
                logger.info(f"**INGEST (FILE)**: `{attachment.filename}` in {channel_name}", message.guild)

        # --- 3. PASSIVE THOUGHT RECORDING (Active Dialogue) ---
        else:
            reply, thought = self.agent.engage_dialogue(
                message.content,
                channel_name,
                context_files=kb.get_channel_context(channel_name),
                history=kb.get_history(channel_name),
                stream_content=kb.get_stream_content(channel_name)
            )

            # Record this turn in conversation history
            kb.append_history(channel_name, "user", message.content)
            kb.append_history(channel_name, "assistant", reply)

            if thought:
                kb.append_thought(channel_name, thought)
                logger.info(f"💭 **THOUGHT**: Extracted in {channel_name}: {thought}", message.guild)

            await message.channel.send(reply)

if __name__ == "__main__":
    # Validate required environment variables as per SPEC.md
    required_vars = {
        "DISCORD_TOKEN": config.DISCORD_TOKEN,
        "GEMINI_API_KEY": config.GEMINI_API_KEY
    }
    
    missing = [var for var, val in required_vars.items() if not val]
    
    if missing:
        logger.error(f"❌ Error: Missing required environment variables: {', '.join(missing)}")
        logger.error("Please ensure they are exported in your ZSH environment (~/.zshrc).")
    else:
        logger.info("✅ Environment validated. Starting MindSpace Bot...")
        intents = discord.Intents.default()
        intents.message_content = True
        bot = MindSpaceBot(intents=intents)
        bot.run(config.DISCORD_TOKEN)
