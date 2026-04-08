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
        self.kb = None  # Single KnowledgeBaseManager, initialized on on_ready

    async def setup_hook(self):
        """Start background tasks."""
        self.loop.create_task(logger.process_log_queue())

    async def on_ready(self):
        guild = self.guilds[0]
        self.kb = KnowledgeBaseManager(guild.name)
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logger.info('------')
        await self._ensure_system_log(guild)

    async def on_guild_join(self, guild):
        """Enforce single-server constraint: leave immediately if already serving a server."""
        if self.kb is not None:
            logger.error(
                f"Attempted to join a second server '{guild.name}'. This bot is single-server only. Leaving.",
                list(self.guilds)[0]
            )
            await guild.leave()
            return
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
                await message.channel.send(f"✅ Organization complete. Reasoning: {reasoning}")
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
                research_res = self.agent.run_command(f"Perform deep research on {args} using current KB context.", context=self.kb.get_channel_context(channel_name, query=args))

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
            await message.channel.send(f"✅ Link ingested and snapshotted: {file_path}")
            logger.info(f"**INGEST (URL)**: {message.content[:50]}... -> `{filename}`", message.guild)

        elif message.attachments:
            await message.channel.send("📥 File detected. Ingesting to KB...")
            for attachment in message.attachments:
                file_path = os.path.join(channel_path, attachment.filename)
                await attachment.save(file_path)

                analysis = self.agent.run_command(f"Analyze this file: {file_path}. Determine human-readable slug and placement.")

                commit_msg = self.agent.generate_commit_message(f"Ingested file: {attachment.filename}")
                self.kb.git_commit(commit_msg)
                await message.channel.send(f"✅ File ingested: {attachment.filename}. Analysis: {analysis}")
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

            self.kb.append_history(channel_name, "user", message.content)
            self.kb.append_history(channel_name, "assistant", reply)

            if thought:
                self.kb.append_thought(channel_name, thought)
                logger.info(f"💭 **THOUGHT**: Extracted in {channel_name}: {thought}", message.guild)

            await message.channel.send(reply)

if __name__ == "__main__":
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
