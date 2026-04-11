import discord
import os
import re
import datetime
import config
import asyncio
import functools
from discord import app_commands


def _slugify_subject(text: str, max_len: int = 50) -> str:
    """Filesystem-safe kebab-case slug from free text, used as the SUBJECT
    segment of TYPE-DATE-SUBJECT output filenames. Falls back to 'untitled'
    for empty / unslugifiable input."""
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:max_len].rstrip("-") or "untitled"


def _extract_title(markdown: str) -> str | None:
    """Return the first H1 (# …) text, or None if absent."""
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _strip_cli_preamble(lines: list[str], heading_prefix: str) -> str:
    """Slice CLI output from the first line starting with `heading_prefix`.

    The Gemini CLI interleaves retry/backoff noise with the final report;
    this drops everything before the expected H1 so only the report is kept.
    Falls back to the full joined output if the heading is never seen.
    """
    for i, line in enumerate(lines):
        if line.lstrip().startswith(heading_prefix):
            return "\n".join(lines[i:]).strip()
    return "\n".join(lines).strip()
from agent import MindSpaceAgent
from tools import MindSpaceTools
from manager import KnowledgeBaseManager
from logger import logger

class MindSpaceBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.bind_bot(self)
        self.agent = None  # initialized in setup_hook (inside the event loop)
        self.kb = None     # initialized in on_ready (after guild info available)
        self.tools = None
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        """Initialize the agent, register slash commands, and start background tasks.
        Runs inside the event loop — genai.Client binds to the correct loop here.
        """
        self.agent = MindSpaceAgent()
        self.log_task = self.loop.create_task(logger.process_log_queue())

        @self.tree.command(name="organize", description="Re-sync and organize the current channel's knowledge base folder.")
        async def cmd_organize(interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            await self.handle_organize(interaction.channel, interaction.guild)

        @self.tree.command(name="consolidate", description="Synthesize stream of consciousness into a structured article.")
        async def cmd_consolidate(interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            await self.handle_consolidate(interaction.channel, interaction.guild, interaction.id)

        @self.tree.command(name="research", description="Deep-dive research on a topic using KB context.")
        @app_commands.describe(topic="The specific topic to research")
        async def cmd_research(interaction: discord.Interaction, topic: str):
            await interaction.response.defer(thinking=True)
            await self.handle_research(interaction.channel, interaction.guild, topic, interaction=interaction)

        @self.tree.command(name="omni", description="Cross-KB synthesis across all channels.")
        @app_commands.describe(query="The broad query to search across the entire knowledge base")
        async def cmd_omni(interaction: discord.Interaction, query: str):
            await interaction.response.defer(thinking=True)
            await self.handle_omni(interaction.channel, interaction.guild, query, interaction.id)

    async def on_ready(self):
        if not self.guilds:
            logger.warning("Bot is not in any guilds.")
            return

        # Initialize the SINGLE KnowledgeBaseManager for the first guild
        guild = self.guilds[0]
        if self.kb is None:
            self.kb = KnowledgeBaseManager(guild.name)
            self.tools = MindSpaceTools(self.kb)
            logger.info(f"Initialized KB and Tools for server: {guild.name}")

        # Sync Slash Commands
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(f"Synced slash commands to guild: {guild.name}")
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}")

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

    # --- CORE COMMAND LOGIC (Agnostic to Trigger) ---

    async def _render_stream_to_channel(self, channel, header: str, handle) -> str:
        """Render a CLI stream handle to a single live-updating Discord message.

        `handle` is any async-iterable that yields already-cleaned text lines
        (e.g. `agent.CliStream`). This method is pure UI — it knows nothing
        about subprocesses, env, or the CLI itself. Returns the full joined
        output for the caller to post-process / save.
        """
        status_msg = await channel.send(f"{header}\n```\n(initializing...)\n```")
        all_lines: list[str] = []
        last_edit = asyncio.get_event_loop().time()

        async for line in handle:
            all_lines.append(line)
            now = asyncio.get_event_loop().time()
            if now - last_edit >= 2.0:
                content = "\n".join(all_lines)
                if len(content) > 1800:
                    content = "..." + content[-1797:]
                try:
                    await status_msg.edit(content=f"{header}\n```\n{content}\n```")
                except discord.HTTPException:
                    pass
                last_edit = now

        try:
            await status_msg.edit(content="✅ Task complete.")
        except discord.HTTPException:
            pass

        return "\n".join(all_lines)

    async def handle_organize(self, channel, guild):
        """Organize the channel folder via Gemini CLI with live progress streaming."""
        await channel.send("🔄 Scanning channel folder...")
        channel_name = channel.name
        channel_path = self.kb.get_channel_path(channel_name)

        rel_prefix = os.path.join("Channels", channel_name) + os.sep
        local_untracked = [
            f[len(rel_prefix):]
            for f in self.kb.list_untracked_files()
            if f.startswith(rel_prefix)
        ]

        if not local_untracked:
            await channel.send("✅ No untracked files in this channel to organize.")
            return

        prompt = f"""You are organizing the MindSpace knowledge base channel folder for #{channel_name}.
Your current working directory IS this channel folder — all paths are relative to it.

UNTRACKED FILES TO ORGANIZE:
{chr(10).join(f'  {f}' for f in local_untracked)}

TASK:
1. Read each file's name and content to understand its topic
2. Determine the most semantically appropriate subfolder within this channel (e.g., Research/, Notes/, Articles/)
3. Create subfolders as needed
4. Move each file to its determined location
5. DO NOT move or modify stream_of_conscious.md
6. DO NOT move files that are already inside a subfolder

You have full write permissions. Operate only within this directory. Make all decisions autonomously.

WHEN DONE, output ONLY this markdown report (no other prose):
## Organize Report
**Summary:** [one sentence]

**Moves:**
- `<from>` → `<subfolder>/<file>`

**Created Folders:**
- `<folder>/`

**Skipped:**
- `<file>`: <reason>
"""
        handle = await self.agent.cli_brain.stream(
            prompt=prompt,
            cwd=channel_path,
        )
        raw = await self._render_stream_to_channel(
            channel, header="🔄 Gemini CLI organizing...", handle=handle,
        )
        report = _strip_cli_preamble(raw.splitlines(), "## Organize Report")

        commit_msg = await asyncio.to_thread(
            self.agent.generate_commit_message,
            f"Organized #{channel_name} channel folder via Gemini CLI"
        )
        await asyncio.to_thread(self.kb.git_commit, commit_msg)
        await self.send_message_safe(channel, report or "No report generated.")
        logger.info(f"**ORGANIZE**: {channel_name} - {commit_msg}", guild)

    async def handle_consolidate(self, channel, guild, interaction_id=None):
        """Synthesize stream of consciousness into a structured permanent article."""
        await channel.send("📑 Consolidating stream of consciousness...")
        channel_name = channel.name
        channel_path = self.kb.get_channel_path(channel_name)
        stream_file = os.path.join(channel_path, "stream_of_conscious.md")

        with open(stream_file, "r") as f:
            content = f.read()

        synthesis = await asyncio.to_thread(
            self.agent.run_command,
            f"Synthesize these thoughts into a structured permanent article:\n\n{content}"
        )
        subject = _slugify_subject(_extract_title(synthesis) or channel_name)
        filename = f"ARTICLE-{datetime.date.today()}-{subject}.md"
        file_path = os.path.join(channel_path, filename)
        self.kb.write_file(file_path, synthesis)
        self.kb.write_file(stream_file, f"# Stream of Consciousness: {channel_name}\n\n")

        commit_msg = await asyncio.to_thread(
            self.agent.generate_commit_message,
            f"Consolidated thoughts in {channel_name} into {filename}"
        )
        await asyncio.to_thread(self.kb.git_commit, commit_msg)

        await channel.send(f"✅ Consolidation complete. Saved to: {file_path}", file=discord.File(file_path))
        logger.info(f"**CONSOLIDATE**: {channel_name} -> `{filename}`", guild)

    async def handle_research(self, channel, guild, topic, interaction: discord.Interaction = None):
        """
        Deep-dive on topic via Gemini CLI (web search + KB context).

        If triggered via slash command (interaction provided), all status
        updates edit the deferred "thinking..." message in place — no
        follow-ups are sent. Text-command fallback uses channel.send.

        Streams CLI output via agent.cli_brain.stream() — each line is
        logged to console as it arrives and the event loop stays responsive.
        """
        # Status updater — edits the deferred interaction response when present,
        # otherwise sends a fresh channel message. Single source for all progress
        # communication so we never spawn extra messages.
        async def status(content: str):
            if interaction is not None:
                try:
                    await interaction.edit_original_response(content=content)
                    return
                except discord.HTTPException as e:
                    logger.warning(f"RESEARCH: failed to edit interaction response: {e}")
            await channel.send(content)

        logger.info(f"RESEARCH: starting — topic={topic!r}")
        await status(f"🔬 Gathering KB context for: {topic}...")
        channel_name = channel.name
        channel_path = self.kb.get_channel_path(channel_name)
        logger.debug(f"RESEARCH: channel_path={channel_path}")

        viking_context = await asyncio.to_thread(self.kb.get_channel_context, channel_name, topic)
        deep_context = await asyncio.to_thread(self.kb.get_deep_context, channel_name, topic)
        logger.debug(
            f"RESEARCH: viking_context={len(viking_context or '')} chars, "
            f"deep_context={len(deep_context or '')} chars"
        )

        combined = ""
        if viking_context:
            combined += f"--- Semantic Overview (Viking) ---\n{viking_context}\n\n"
        if deep_context:
            combined += f"--- Deep Document Analysis (PageIndex) ---\n{deep_context}\n\n"

        prompt = f"""You are performing deep research on the topic: "{topic}"

You have TWO sources of information:
1. The local knowledge base context below (extracted from channel #{channel_name}).
2. The web — use your search tool freely to find recent/authoritative sources.

LOCAL KB CONTEXT:
{combined or "(none — the KB had no relevant matches)"}

TASK:
- Synthesize a thorough research report on the topic.
- Cross-reference local KB findings with fresh web sources.
- Cite every non-trivial claim: inline `[source: <url or KB path>]`.
- If KB context conflicts with web sources, flag the discrepancy explicitly.

OUTPUT FORMAT (markdown, no extra prose outside this structure):
# Research: {topic}

## Executive Summary
<3-5 sentence summary>

## Key Findings
- <finding 1> [source: ...]
- <finding 2> [source: ...]

## Detailed Analysis
<multi-paragraph analysis with inline citations>

## Conflicts & Open Questions
<any contradictions between KB and web, or unresolved questions>

## Sources
- <url or KB path>: <one-line description>
"""
        logger.info(f"RESEARCH: invoking Gemini CLI — cwd={channel_path}, "
                    f"prompt_len={len(prompt)}")
        await status(f"🔬 Running Gemini CLI on: {topic}\n(watch console for live progress)")

        handle = await self.agent.cli_brain.stream(
            prompt=prompt,
            cwd=channel_path,
        )
        # Stream CLI output to the deferred interaction's "thinking..." message,
        # editing it every ~2s with the latest tail so the user sees progress
        # without any follow-up spam. Console still gets every line.
        lines: list[str] = []
        header = f"🔬 Researching: **{topic}**"
        last_edit = asyncio.get_event_loop().time()
        async for line in handle:
            logger.info(f"CLI | {line}")
            lines.append(line)
            now = asyncio.get_event_loop().time()
            if now - last_edit >= 2.0:
                tail = "\n".join(lines[-15:])
                if len(tail) > 1700:
                    tail = "..." + tail[-1697:]
                await status(f"{header}\n```\n{tail}\n```")
                last_edit = now

        logger.info(f"RESEARCH: CLI exited — returncode={handle.returncode}, lines={len(lines)}")

        if handle.returncode != 0:
            logger.error(f"RESEARCH: CLI failed with non-zero exit {handle.returncode}")
            tail = "\n".join(lines[-20:])
            await status(f"❌ Gemini CLI exited {handle.returncode}. Last lines:\n```\n{tail[:1500]}\n```")
            return

        report = _strip_cli_preamble(lines, "# Research:")
        if not report:
            logger.error("RESEARCH: CLI produced empty output")
            await status("⚠️ Research produced no output.")
            return

        logger.debug(f"RESEARCH: report preview:\n{report[:500]}...")

        subject = _slugify_subject(topic)
        filename = f"RESEARCH-{datetime.date.today()}-{subject}.md"
        file_path = os.path.join(channel_path, filename)
        lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- URI: viking://{guild.name}/{channel_name}/{filename}"
        self.kb.write_file(file_path, report + lineage)
        logger.info(f"RESEARCH: wrote report to {file_path}")

        commit_msg = await asyncio.to_thread(
            self.agent.generate_commit_message,
            f"Research synthesis on {topic}"
        )
        await asyncio.to_thread(self.kb.git_commit, commit_msg)

        # Final update: edit the "thinking..." message to show completion AND attach
        # the research file in the same message — no follow-up created.
        if interaction is not None:
            try:
                await interaction.edit_original_response(
                    content=f"✅ Research complete: **{topic}**",
                    attachments=[discord.File(file_path)],
                )
            except discord.HTTPException as e:
                logger.warning(f"RESEARCH: failed to attach file to interaction: {e}")
                await channel.send(f"✅ Research complete.", file=discord.File(file_path))
        else:
            await channel.send(f"✅ Research complete.", file=discord.File(file_path))
        logger.info(f"RESEARCH: completed — topic={topic!r} channel=#{channel_name}", guild)

    async def handle_omni(self, channel, guild, query, interaction_id=None):
        """Cross-KB synthesis across all channels via Gemini CLI with live streaming."""
        await channel.send(f"🌐 Gathering global KB context for: {query}...")
        channel_name = channel.name
        channel_path = self.kb.get_channel_path(channel_name)

        global_context = await asyncio.to_thread(self.kb.get_global_context, query)

        prompt = f"""You are performing a cross-channel synthesis across the ENTIRE MindSpace knowledge base.

QUERY: "{query}"

GLOBAL KB CONTEXT (semantic search across all channels):
{global_context or "(no KB matches — rely on web search)"}

TASK:
- Answer the query comprehensively, drawing from ALL relevant channels.
- Use web search to supplement gaps or verify recency.
- Cite every claim: inline `[source: <channel/file> or <url>]`.
- Highlight cross-channel connections and themes.

OUTPUT FORMAT (markdown):
# Omni: {query}

## Summary
<concise answer, 3-5 sentences>

## Findings by Channel
### #<channel-name>
- <finding> [source: ...]

## Cross-Channel Connections
<how findings from different channels relate>

## Sources
- <path or url>: <one-line description>
"""

        # cwd=Channels/ so the CLI can read across all channels (omni's whole
        # point) but is sandboxed from bot-home/, openviking/, and ov.conf.
        handle = await self.agent.cli_brain.stream(
            prompt=prompt,
            cwd=config.CHANNELS_PATH,
        )
        raw = await self._render_stream_to_channel(
            channel, header=f"🌐 Gemini CLI synthesizing: {query}...", handle=handle,
        )
        report = _strip_cli_preamble(raw.splitlines(), "# Omni:")

        if not report.strip():
            await channel.send("⚠️ Omni synthesis produced no output.")
            return

        subject = _slugify_subject(query)
        filename = f"OMNI-{datetime.date.today()}-{subject}.md"
        file_path = os.path.join(channel_path, filename)
        lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- URI: viking://{guild.name}/omni/{filename}"
        self.kb.write_file(file_path, report + lineage)

        commit_msg = await asyncio.to_thread(
            self.agent.generate_commit_message,
            f"Omni query synthesis: {query}"
        )
        await asyncio.to_thread(self.kb.git_commit, commit_msg)
        await channel.send(f"✅ Omni search complete.", file=discord.File(file_path))
        logger.info(f"**OMNI**: {query}", guild)

    # --- HELPERS ---

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
        """Seed in-memory history from Discord message history on startup."""
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

    async def close(self):
        """Cleanly shut down: close GenAI client and cancel background tasks."""
        if hasattr(self, 'log_task') and not self.log_task.done():
            self.log_task.cancel()
            try:
                await self.log_task
            except asyncio.CancelledError:
                pass

        if self.agent:
            self.agent.close()
        await super().close()

    async def on_guild_join(self, guild):
        """Enforce single-server constraint: leave immediately if already serving a server."""
        if self.kb is not None:
            logger.error(
                f"Attempted to join a second server '{guild.name}'. This bot is SINGLE-SERVER ONLY. Leaving.",
                self.guilds[0]
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

    async def send_message_safe(self, channel, content):
        """Send a message, splitting into multiple parts if it exceeds Discord's 2000-char limit."""
        if not content:
            return
        limit = 2000
        while len(content) > limit:
            split_at = content.rfind('\n', 0, limit)
            if split_at == -1:
                split_at = limit
            chunk = content[:split_at].strip()
            if chunk:
                await channel.send(chunk)
            content = content[split_at:].strip()
        if content:
            await channel.send(content)

    # --- EVENT HANDLERS ---

    async def on_message(self, message):
        if message.author == self.user:
            return

        channel_name = message.channel.name
        channel_path = self.kb.get_channel_path(channel_name)

        # --- 1. ACTIVE COMMANDS (! prefix — parity with slash commands) ---
        if message.content.startswith('!'):
            command_parts = message.content[1:].split(' ', 1)
            cmd = command_parts[0].lower()
            args = command_parts[1] if len(command_parts) > 1 else ""

            if cmd == "organize":
                await self.handle_organize(message.channel, message.guild)
            elif cmd == "consolidate":
                await self.handle_consolidate(message.channel, message.guild, message.id)
            elif cmd == "research":
                if not args:
                    await message.channel.send("Usage: `!research [topic]`")
                    return
                await self.handle_research(message.channel, message.guild, args, message.id)
            elif cmd == "omni":
                if not args:
                    await message.channel.send("Usage: `!omni [query]`")
                    return
                await self.handle_omni(message.channel, message.guild, args, message.id)
            return

        # --- 2. KNOWLEDGE INGESTION (URLs / file attachments) ---
        elif "http://" in message.content or "https://" in message.content:
            await message.channel.send("🌐 URL detected. Snapshotting to KB...")
            markdown_snapshot = await asyncio.to_thread(
                self.agent.process_url, message.content, channel_name
            )
            subject = _slugify_subject(_extract_title(markdown_snapshot) or "webpage")
            filename = f"WEBPAGE-{datetime.date.today()}-{subject}.md"
            file_path = os.path.join(channel_path, filename)
            self.kb.write_file(file_path, markdown_snapshot)

            commit_msg = await asyncio.to_thread(
                self.agent.generate_commit_message,
                f"Ingested webpage snapshot: {filename}"
            )
            await asyncio.to_thread(self.kb.git_commit, commit_msg)
            await self.send_message_safe(message.channel, f"✅ Link ingested and snapshotted: {file_path}")
            logger.info(f"**INGEST (URL)**: {message.content[:50]}... -> `{filename}`", message.guild)

        elif message.attachments:
            await message.channel.send("📥 File detected. Ingesting to KB...")
            for attachment in message.attachments:
                file_path = os.path.join(channel_path, attachment.filename)
                await attachment.save(file_path)

                doc_id, analysis = await asyncio.to_thread(
                    self.agent.analyze_file, file_path, self.kb.pageindex
                )
                if doc_id:
                    logger.info(f"**PAGEINDEX**: Indexed `{attachment.filename}` doc_id={doc_id}", message.guild)

                commit_msg = await asyncio.to_thread(
                    self.agent.generate_commit_message,
                    f"Ingested file: {attachment.filename}"
                )
                await asyncio.to_thread(self.kb.git_commit, commit_msg)
                await self.send_message_safe(message.channel, f"✅ File ingested: {attachment.filename}. Analysis: {analysis}")
                logger.info(f"**INGEST (FILE)**: `{attachment.filename}` in {channel_name}", message.guild)

        # --- 3. PASSIVE DIALOGUE (thought recording + tool-augmented replies) ---
        else:
            available_tools = self.tools.get_tools(channel_name)

            reply, thought = await asyncio.to_thread(
                self.agent.engage_dialogue,
                message.content,
                channel_name,
                history=self.kb.get_history(channel_name),
                stream_content=self.kb.get_stream_content(channel_name),
                tools=available_tools
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
        pi_client = PageIndexClient(api_key=config.PAGEINDEX_API_KEY)
        pi_client.list_documents(limit=1)

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
