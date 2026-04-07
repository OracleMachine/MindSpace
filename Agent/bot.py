import discord
import os
import datetime
import config
from agent import MindSpaceAgent
from manager import KnowledgeBaseManager

class MindSpaceBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.agent = MindSpaceAgent()
        self.kb_managers = {}  # Map server_id to KnowledgeBaseManager instance

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

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

            elif cmd == "research":
                await message.channel.send(f"🔬 Synthesizing research on: {args}...")
                index_path = os.path.join(channel_path, "INDEX.MD")
                research_res = self.agent.run_command(f"Perform deep research on {args} using current KB context.", [index_path])
                
                filename = f"RESEARCH-{datetime.date.today()}-{message.id}.MD"
                file_path = os.path.join(channel_path, filename)
                
                # Append lineage as per SPEC.MD
                lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- Discord: {message.jump_url}\n- URI: viking://{message.guild.name}/{channel_name}/{filename}"
                kb.write_file(file_path, research_res + lineage)
                
                commit_msg = self.agent.generate_commit_message(f"Research synthesis on {args}")
                kb.git_commit(commit_msg)
                
                await message.channel.send(f"✅ Research complete.", file=discord.File(file_path))

        # --- 2. HANDLE KNOWLEDGE INGESTION (Links/Files) ---
        elif "http://" in message.content or "https://" in message.content:
            await message.channel.send("🌐 URL detected. Snapshotting to KB...")
            # Use Gemini CLI to process URL content directly
            markdown_snapshot = self.agent.process_url(message.content, channel_name)
            
            filename = f"WEBPAGE-{datetime.date.today()}-{message.id}.MD"
            file_path = os.path.join(channel_path, filename)
            kb.write_file(file_path, markdown_snapshot)
            
            commit_msg = self.agent.generate_commit_message(f"Ingested webpage snapshot: {filename}")
            kb.git_commit(commit_msg)
            await message.channel.send(f"✅ Link ingested and snapshotted: {file_path}")

        elif message.attachments:
            await message.channel.send("📥 File detected. Ingesting to KB...")
            for attachment in message.attachments:
                file_path = os.path.join(channel_path, attachment.filename)
                await attachment.save(file_path)
                
                # Use Gemini CLI to analyze and place semantically
                analysis = self.agent.run_command(f"Analyze this file: {file_path}. Determine human-readable slug and placement.")
                
                commit_msg = self.agent.generate_commit_message(f"Ingested file: {attachment.filename}")
                kb.git_commit(commit_msg)
                await message.channel.send(f"✅ File ingested: {attachment.filename}. Analysis: {analysis}")

        # --- 3. PASSIVE THOUGHT RECORDING (Active Dialogue) ---
        else:
            # Gemini CLI maintains the dialogue AND identifies extractable thoughts
            # For simplicity in this prototype, we pass the channel INDEX.MD for context
            index_path = os.path.join(channel_path, "INDEX.MD")
            reply, thought = self.agent.engage_dialogue(message.content, channel_name, [index_path])
            
            if thought:
                kb.append_thought(channel_name, thought)
                # No commit on passive thought to keep history clean unless active sync happens
                print(f"Secretly extracted thought in {channel_name}: {thought}")
            
            await message.channel.send(reply)

if __name__ == "__main__":
    # Validate required environment variables as per SPEC.md
    required_vars = {
        "DISCORD_TOKEN": config.DISCORD_TOKEN,
        "GEMINI_API_KEY": config.GEMINI_API_KEY
    }
    
    missing = [var for var, val in required_vars.items() if not val]
    
    if missing:
        print(f"❌ Error: Missing required environment variables: {', '.join(missing)}")
        print("Please ensure they are exported in your ZSH environment (~/.zshrc).")
    else:
        print("✅ Environment validated. Starting MindSpace Bot...")
        intents = discord.Intents.default()
        intents.message_content = True
        bot = MindSpaceBot(intents=intents)
        bot.run(config.DISCORD_TOKEN)
