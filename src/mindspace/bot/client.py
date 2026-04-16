import discord
import os
import re
import io
import datetime
import pathlib
import asyncio
import functools
import git
import uuid
import inspect
from typing import List
from discord import app_commands

from mindspace.core import config
from mindspace.core.logger import logger
from mindspace.agent.agent import MindSpaceAgent
from mindspace.agent.tools import MindSpaceTools
from mindspace.knowledgebase.manager import KnowledgeBaseManager
from mindspace.bot.handlers import ActiveCommandHandler, KnowledgeIngestionHandler, PassiveDialogueHandler
from mindspace.bot.views import ProposalView, RefineModal, _render_diff, _format_proposal_message
import mindspace.agent.mcp as mcp_bridge

# CRITICAL: Suppress litellm background logging workers BEFORE they can start.
try:
    import litellm
    litellm._suppress_logging_worker = True
    litellm.suppress_debug_info = True
except ImportError:
    pass

def _extract_title(markdown: str) -> str | None:
    """Return the first H1 (# …) text, or None if absent."""
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return None

def _slugify_subject(text: str, max_len: int = 50) -> str:
    """Filesystem-safe kebab-case slug from free text."""
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:max_len].rstrip("-") or "untitled"

class MindSpaceBot(discord.Client):
    RESERVED_CHANNELS = {
        "system-log": "🚀 **MindSpace System Log Initialized.**",
        "notification": "🔔 **MindSpace Notification Channel Initialized.**",
    }

    HELP_TEXT = (pathlib.Path(__file__).parent.parent.parent.parent / "docs" / "help.md").read_text(encoding="utf-8")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.agent = None
        self.kb = None
        self.tools = None
        self.mcp_pool = None
        self._pending_proposals: dict[str, dict] = {}
        self.tree = app_commands.CommandTree(self)

        self._log_queue = asyncio.Queue()
        self._log_publisher_task = None

        self.message_handlers: List[ActiveCommandHandler | KnowledgeIngestionHandler | PassiveDialogueHandler] = [
            ActiveCommandHandler(),
            KnowledgeIngestionHandler(),
            PassiveDialogueHandler()
        ]

    def wrap_tool_with_progress(self, fn, on_progress):
        @functools.wraps(fn)
        async def inner(*args, **kwargs):
            arg_parts = [repr(a)[:60] for a in args] + [f"{k}={repr(v)[:60]}" for k, v in kwargs.items()]
            call_summary = f"🔧 {fn.__name__}({', '.join(arg_parts)})"
            await on_progress(call_summary)
            try:
                if inspect.iscoroutinefunction(fn):
                    result = await fn(*args, **kwargs)
                else:
                    result = await asyncio.to_thread(fn, *args, **kwargs)
                res_preview = str(result)[:100].replace('\n', ' ') + ("..." if len(str(result)) > 100 else "")
                logger.info(f"Tool: {call_summary} -> {res_preview}")
                return result
            except Exception as e:
                logger.error(f"Tool Error: {fn.__name__} failed: {e}")
                raise
        return inner

    def wrap_mcp_with_progress(self, on_progress):
        self._mcp_originals = {}
        for srv, session in self.mcp_pool.sessions.items():
            orig = session.call_tool
            self._mcp_originals[srv] = orig
            async def _wrapped(name, arguments=None, *, _orig=orig, _srv=srv, **kw):
                arg_str = ", ".join(f"{k}={repr(v)[:60]}" for k, v in (arguments or {}).items())
                await on_progress(f"🌐 MCP {_srv}: {name}({arg_str})")
                return await _orig(name, arguments, **kw)
            session.call_tool = _wrapped
        return self.mcp_pool.sessions

    def unwrap_mcp(self):
        if hasattr(self, '_mcp_originals'):
            for srv, orig in self._mcp_originals.items():
                self.mcp_pool.sessions[srv].call_tool = orig
            del self._mcp_originals

    async def ingest_content(self, channel, file_path, content, action_desc):
        """Unified helper to write file and commit state."""
        self.kb.write_file(file_path, content)
        commit_msg = await self.agent.generate_commit_message(action_desc)
        await asyncio.to_thread(self.kb.save_state, commit_msg)
        return commit_msg

    async def handle_url_ingest(self, message):
        channel_name = message.channel.name
        channel_path = self.kb.get_channel_path(channel_name)
        await message.channel.send("🌐 URL detected. Snapshotting to KB...")
        markdown_snapshot = await self.agent.process_url(message.content, channel_name)
        subject = _slugify_subject(_extract_title(markdown_snapshot) or "webpage")
        filename = f"WEBPAGE-{datetime.date.today()}-{subject}.md"
        file_path = os.path.join(channel_path, filename)
        
        await self.ingest_content(message.channel, file_path, markdown_snapshot, f"Ingested webpage snapshot: {filename}")
        await self.send_message_safe(message.channel, f"✅ Link ingested and snapshotted: {file_path}")
        logger.info(f"**INGEST (URL)**: {message.content[:50]}... -> `{filename}`", message.guild)

    async def handle_attachment_ingest(self, message):
        mentioned = self.user.mentioned_in(message)
        advice = re.sub(r"<@!?\d+>", "", message.content).strip()
        for attachment in message.attachments:
            is_md = attachment.filename.lower().endswith((".md", ".markdown"))
            if mentioned and is_md:
                await message.channel.send(f"✍️ Reviewed ingest for `{attachment.filename}` — preparing proposal...")
                await self._handle_file_proposal(message, attachment, advice)
            else:
                prefix = f"📥 `{attachment.filename}` — "
                if mentioned:
                    await message.channel.send(f"{prefix}proposal flow is `.md`-only; routing with advice...")
                else:
                    await message.channel.send(f"{prefix}routing into KB by content...")
                await self._handle_file_autoroute(message, attachment, advice=advice if mentioned else "")


    async def setup_hook(self):
        self.agent = MindSpaceAgent()
        mcp_bridge.sync_cli_settings()
        self.mcp_pool = mcp_bridge.MCPSessionPool(config.MCP.SERVERS)
        await self.mcp_pool.connect()

        self._log_publisher_task = self.loop.create_task(self._log_publisher())

        @self.tree.command(name="organize", description="Re-sync and organize the current channel's knowledge base folder.")
        async def cmd_organize(interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            await self.handle_organize(interaction.channel, interaction.guild)

        @self.tree.command(name="consolidate", description="Synthesize stream of consciousness into a structured article.")
        async def cmd_consolidate(interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            await self.handle_consolidate(interaction.channel, interaction.guild)

        @self.tree.command(name="research", description="Deep-dive research on a topic using KB context.")
        @app_commands.describe(topic="The specific topic to research")
        async def cmd_research(interaction: discord.Interaction, topic: str):
            await interaction.response.defer(thinking=True)
            await self.handle_research(interaction.channel, interaction.guild, topic, interaction=interaction)

        @self.tree.command(name="change_my_view", description="Update the static mindset (view.md) for this channel.")
        @app_commands.describe(instruction="How to update the core mindset (e.g., 'Emphasize local-first development')")
        async def cmd_change_my_view(interaction: discord.Interaction, instruction: str):
            await interaction.response.defer(thinking=True)
            await self.handle_change_my_view(interaction.channel, interaction.guild, instruction, interaction=interaction)

        @self.tree.command(name="omni", description="Cross-KB synthesis across all channels.")
        @app_commands.describe(query="The broad query to search across the entire knowledge base")
        async def cmd_omni(interaction: discord.Interaction, query: str):
            await interaction.response.defer(thinking=True)
            await self.handle_omni(interaction.channel, interaction.guild, query)

        @self.tree.command(name="help", description="Post the MindSpace usage guide to #notification.")
        async def cmd_help(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True, thinking=False)
            channel = await self.handle_help(interaction.guild)
            target = channel.mention if channel else "`#notification`"
            await interaction.followup.send(
                f"📬 Help posted to {target}.", ephemeral=True
            )

    async def on_ready(self):
        if not self.guilds:
            logger.warning("Bot is not in any guilds.")
            return

        guild = self.guilds[0]
        
        def bridge_to_discord(msg: str):
            self.loop.call_soon_threadsafe(self._log_queue.put_nowait, msg)
        logger.set_callback(bridge_to_discord)

        if self.kb is None:
            self.kb = KnowledgeBaseManager(guild.name)
            self.tools = MindSpaceTools(self.kb)
            self.agent.set_kb(self.kb)
            logger.info(f"Initialized KB and Tools for server: {guild.name}")

        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(f"Synced slash commands to guild: {guild.name}")
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}")

        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logger.info('------')

        logger.info("Startup: ensuring reserved channels...")
        await self._ensure_reserved_channels(guild)
        logger.info("Startup: syncing KB folders → Discord channels...")
        await self._sync_kb_channels(guild)

        agent_repo_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        agent_repo = git.Repo(agent_repo_path)
        agent_git = agent_repo.git.describe("--tags", "--dirty", "--always")
        thought_git = self.kb._repo.git.describe("--tags", "--dirty", "--always")

        logger.info(f"Startup: all indexing complete — bot fully ready (Agent: {agent_git} Thought: {thought_git})")

        for channel in guild.text_channels:
            if channel.name not in self.RESERVED_CHANNELS:
                await self._seed_channel_history(channel)

    async def _render_stream_to_channel(self, channel, header: str, handle) -> str:
        status_msg = await channel.send(f"{header}\n```\n(initializing...)\n```")
        all_parts: list[str] = []

        async for chunk in handle:
            all_parts.append(chunk)
            content = "".join(all_parts)
            if len(content) > 1800:
                content = "..." + content[-1797:]
            try:
                await status_msg.edit(content=f"{header}\n```\n{content}\n```")
            except discord.HTTPException:
                pass

        try:
            await status_msg.edit(content="✅ Task complete.")
        except discord.HTTPException:
            pass

        return "".join(all_parts).strip()

    async def handle_organize(self, channel, guild):
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

        prompt = f"""
You are organizing the MindSpace knowledge base channel folder for #{channel_name}.
Your current working directory IS this channel folder — all paths are relative to it.

UNTRACKED FILES TO ORGANIZE:
{chr(10).join(f'  {f}' for f in local_untracked)}

TASK:
1. Read each file's name and content to understand its topic
2. Determine the most semantically appropriate subfolder within this channel (e.g., Research/, Notes/, Articles/)
3. Create subfolders as needed
4. Move each file to its determined location
5. DO NOT move or modify stream_of_conscious.md or view.md
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
        handle = await self.agent.stream(
            prompt=prompt,
            cwd=channel_path,
            channel_name=channel_name,
        )
        await self._render_stream_to_channel(
            channel, header="🔄 Gemini CLI organizing...", handle=handle,
        )
        report = handle.get_full_response()

        commit_msg = await self.agent.generate_commit_message(
            f"Organized #{channel_name} channel folder via Gemini CLI"
        )
        await asyncio.to_thread(self.kb.save_state, commit_msg)
        await self.send_message_safe(channel, report or "No report generated.")
        logger.info(f"**ORGANIZE**: {channel_name} - {commit_msg}", guild)

    async def handle_consolidate(self, channel, guild):
        await channel.send("📑 Consolidating stream of consciousness...")
        channel_name = channel.name
        channel_path = self.kb.get_channel_path(channel_name)
        stream_file = os.path.join(channel_path, "stream_of_conscious.md")

        with open(stream_file, "r") as f:
            content = f.read()

        synthesis = await self.agent.run_command(
            f"Synthesize these thoughts into a structured permanent article:\n\n{content}",
            channel_name=channel_name
        )
        subject = _slugify_subject(_extract_title(synthesis) or channel_name)
        filename = f"ARTICLE-{datetime.date.today()}-{subject}.md"
        file_path = os.path.join(channel_path, filename)
        self.kb.write_file(file_path, synthesis)
        self.kb.write_file(stream_file, f"# Stream of Consciousness: {channel_name}\n\n")

        commit_msg = await self.agent.generate_commit_message(
            f"Consolidated thoughts in {channel_name} into {filename}"
        )
        await asyncio.to_thread(self.kb.save_state, commit_msg)

        await channel.send(f"✅ Consolidation complete. Saved to: {file_path}", file=discord.File(file_path))
        logger.info(f"**CONSOLIDATE**: {channel_name} -> `{filename}`", guild)

    async def handle_research(self, channel, guild, topic, interaction: discord.Interaction = None):
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

        viking_context = await asyncio.to_thread(self.kb.get_channel_context, channel_name, topic)
        deep_context = await asyncio.to_thread(self.kb.get_deep_context, channel_name, topic)

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
        await status(f"🔬 Running Gemini CLI on: {topic}\n(watch console for live progress)")

        handle = await self.agent.stream(
            prompt=prompt,
            cwd=channel_path,
            channel_name=channel_name,
        )
        header = f"🔬 Researching: **{topic}**"
        await self._render_stream_to_channel(channel, header=header, handle=handle)

        if handle.returncode != 0:
            logger.error(f"RESEARCH: CLI failed with non-zero exit {handle.returncode}")
            await status(f"❌ Gemini CLI exited {handle.returncode}.")
            return

        report = handle.get_full_response()
        if not report:
            await status("⚠️ Research produced no output.")
            return

        subject = _slugify_subject(topic)
        filename = f"RESEARCH-{datetime.date.today()}-{subject}.md"
        file_path = os.path.join(channel_path, filename)
        lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- URI: viking://{guild.name}/{channel_name}/{filename}"
        self.kb.write_file(file_path, report + lineage)

        commit_msg = await self.agent.generate_commit_message(
            f"Research synthesis on {topic}"
        )
        await asyncio.to_thread(self.kb.save_state, commit_msg)

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

    async def handle_omni(self, channel, guild, query):
        await channel.send(f"🌐 Gathering global KB context for: {query}...")
        channel_name = channel.name
        channel_path = self.kb.get_channel_path(channel_name)

        global_context = await asyncio.to_thread(self.kb.get_global_context, query)

        prompt = f"""
You are performing a cross-channel synthesis across the ENTIRE MindSpace knowledge base.

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
        handle = await self.agent.stream(
            prompt=prompt,
            cwd=config.Paths.CHANNELS,
            channel_name=channel.name,
        )
        await self._render_stream_to_channel(
            channel, header=f"🌐 Gemini CLI synthesizing: {query}...", handle=handle,
        )
        report = handle.get_full_response()

        if not report.strip():
            await channel.send("⚠️ Omni synthesis produced no output.")
            return

        subject = _slugify_subject(query)
        filename = f"OMNI-{datetime.date.today()}-{subject}.md"
        file_path = os.path.join(channel_path, filename)
        lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- URI: viking://{guild.name}/omni/{filename}"
        self.kb.write_file(file_path, report + lineage)

        commit_msg = await self.agent.generate_commit_message(
            f"Omni query synthesis: {query}"
        )
        await asyncio.to_thread(self.kb.save_state, commit_msg)
        await channel.send(f"✅ Omni search complete.", file=discord.File(file_path))
        logger.info(f"**OMNI**: {query}", guild)

    async def handle_change_my_view(self, channel, guild, instruction, interaction=None):
        channel_name = channel.name
        current_view = self.kb.get_view(channel_name)
        
        status_msg = None
        if not interaction:
            status_msg = await channel.send(f"🔄 Thinking about your mindset change: {instruction}...")

        prompt = (
            f"You are updating the user's static mindset view for channel #{channel_name}.\n"
            f"Current view.md content:\n{current_view if current_view else '(empty)'}\n\n"
            f"Instruction to update the view:\n{instruction}\n\n"
            f"TASK:\n"
            f"Rewrite the entire view.md content to incorporate the new instruction while maintaining existing core principles if they still apply. "
            f"Keep it concise and impactful. Output ONLY the new markdown content. Do not include formatting backticks or explanations."
        )
        
        new_view = await self.agent.run_command(prompt, channel_name=channel_name)
        
        if new_view.startswith("```markdown"):
            new_view = new_view[11:]
        if new_view.startswith("```"):
            new_view = new_view[3:]
        if new_view.endswith("```"):
            new_view = new_view[:-3]
        new_view = new_view.strip()

        proposal_id = self._create_proposal(
            channel_name=channel_name,
            rel_path="view.md",
            existing_content=current_view,
            proposed_content=new_view,
            instruction=instruction,
            rationale="User requested a mindset view change."
        )

        if status_msg:
            try:
                await status_msg.delete()
            except:
                pass

        await self._send_proposal(channel, proposal_id)
        logger.info(f"**CHANGE_MY_VIEW**: Proposed update for #{channel_name}: {instruction}", guild)

    async def handle_help(self, guild):
        channel = await self._ensure_channel(
            guild, "notification", self.RESERVED_CHANNELS["notification"]
        )
        if channel:
            await self.send_message_safe(channel, self.HELP_TEXT)
            logger.info("**HELP**: posted usage guide to #notification", guild)
        return channel

    async def handle_sync(self, channel, guild):
        channel_name = channel.name
        await channel.send(f"🔄 Syncing Knowledge Base for `#{channel_name}`...")
        try:
            await asyncio.to_thread(self.kb.viking.rebuild_index, channel_name)
            await channel.send(f"✅ KB sync complete for `#{channel_name}`. All manual edits and reorganizations are now indexed.")
            logger.info(f"**SYNC**: Manual sync triggered for #{channel_name}", guild)
        except Exception as e:
            await channel.send(f"❌ Sync failed: {e}")
            logger.error(f"**SYNC**: Failed for #{channel_name}: {e}", guild)

    def _create_proposal(self, channel_name: str, rel_path: str,
                          existing_content: str, proposed_content: str,
                          instruction: str, rationale: str) -> str:
        proposal_id = uuid.uuid4().hex[:8]
        self._pending_proposals[proposal_id] = {
            "channel_name": channel_name,
            "rel_path": rel_path,
            "existing_content": existing_content,
            "proposed_content": proposed_content,
            "rationale": rationale,
            "instruction": instruction,
        }
        return proposal_id

    async def _send_proposal(self, channel, proposal_id: str):
        proposal = self._pending_proposals.get(proposal_id)
        if not proposal:
            return
        diff_text = _render_diff(
            proposal["existing_content"],
            proposal["proposed_content"],
            proposal["rel_path"],
        )
        content, embed, file = _format_proposal_message(
            proposal["rel_path"], proposal["rationale"], diff_text
        )
        view = ProposalView(self, proposal_id)
        await channel.send(content=content, embed=embed, view=view, file=file)

    async def handle_propose_update(self, channel_name: str, rel_path: str, instruction: str, rationale: str):
        if rel_path.lower() == "view.md":
            return "Error: `view.md` is a protected system file and can only be modified via `!change_my_view`."
        
        channel_path = self.kb.get_channel_path(channel_name)
        abs_path = os.path.abspath(os.path.join(channel_path, rel_path))

        channels_abs = os.path.abspath(config.Paths.CHANNELS)
        if os.path.commonpath([abs_path, channels_abs]) != channels_abs:
            return "Error: Security violation — cannot edit files outside the knowledge base."

        existing_content = ""
        if os.path.exists(abs_path):
            with open(abs_path, "r") as f:
                existing_content = f.read()

        if existing_content:
            prompt = (
                f"Modify the following Knowledge Base file based on this instruction:\n"
                f"INSTRUCTION: {instruction}\n\n"
                f"ORIGINAL FILE CONTENT:\n---\n{existing_content}\n---\n\n"
                f"Apply the instruction surgically. Maintain existing tone and formatting.\n"
                f"Output ONLY the complete, rewritten markdown document — no commentary."
            )
        else:
            prompt = (
                f"Create a new Knowledge Base file based on this instruction:\n"
                f"INSTRUCTION: {instruction}\n\n"
                f"The file will be saved as: {rel_path}\n"
                f"Output ONLY the complete markdown document — no commentary."
            )
        proposed_content = await self.agent.brain.run_command_async(prompt)

        if not proposed_content.strip():
            return "Error: The rewrite agent returned empty content."

        diff_text = _render_diff(existing_content, proposed_content, rel_path)
        if not diff_text.strip():
            return f"The file `{rel_path}` already appears to satisfy the instruction."

        return self._create_proposal(
            channel_name=channel_name,
            rel_path=rel_path,
            existing_content=existing_content,
            proposed_content=proposed_content,
            instruction=instruction,
            rationale=rationale,
        )

    def _sanitize_subfolder(self, channel_name: str, subfolder: str) -> str:
        if not subfolder:
            return ""
        sub = subfolder.strip().strip("/")
        if not sub:
            return ""
        channel_abs = os.path.abspath(os.path.join(self.kb.channels_path, channel_name))
        target_abs = os.path.abspath(os.path.join(channel_abs, sub))
        try:
            if os.path.commonpath([target_abs, channel_abs]) != channel_abs:
                return ""
        except ValueError:
            return ""
        return sub

    def _sanitize_filename(self, filename: str, fallback: str) -> str:
        if not filename:
            return fallback
        name = os.path.basename(filename).lstrip(".")
        return name or fallback

    def _dedupe_path(self, abs_path: str) -> str:
        if not os.path.exists(abs_path):
            return abs_path
        stem, ext = os.path.splitext(abs_path)
        i = 2
        while True:
            candidate = f"{stem}-{i}{ext}"
            if not os.path.exists(candidate):
                return candidate
            i += 1

    async def _handle_file_autoroute(self, message, attachment, advice: str = ""):
        channel_name = message.channel.name
        ext = os.path.splitext(attachment.filename)[1].lower()

        raw_bytes = await attachment.read()
        snippet = ""
        if self.agent.is_text_ext(ext):
            snippet = raw_bytes[:5000].decode("utf-8", errors="replace")

        tree_listing = self.kb.list_channel_tree(channel_name)
        subfolder, new_name = await self.agent.route_file(
            filename=attachment.filename,
            snippet=snippet,
            tree_listing=tree_listing,
            channel_name=channel_name,
            advice=advice,
        )
        subfolder = self._sanitize_subfolder(channel_name, subfolder)
        new_name = self._sanitize_filename(new_name, attachment.filename)

        channel_path = self.kb.get_channel_path(channel_name)
        final_dir = os.path.join(channel_path, subfolder) if subfolder else channel_path
        os.makedirs(final_dir, exist_ok=True)
        final_path = self._dedupe_path(os.path.join(final_dir, new_name))
        with open(final_path, "wb") as f:
            f.write(raw_bytes)

        # Legacy side-effect: PDFs upload to PageIndex, text-ish files get an
        # LLM summary. Runs on the final path so PageIndex caches correctly.
        doc_id, analysis = await self.agent.analyze_file(final_path, self.kb.pageindex)
        if doc_id:
            logger.info(
                f"**PAGEINDEX**: Indexed `{attachment.filename}` doc_id={doc_id}",
                message.guild,
            )

        rel = os.path.relpath(final_path, channel_path)
        commit_msg = await self.agent.generate_commit_message(
            f"Ingested file into #{channel_name}: {rel}"
        )
        await asyncio.to_thread(self.kb.save_state, commit_msg)

        summary_tail = f"\n> {analysis}" if analysis else ""
        await self.send_message_safe(
            message.channel,
            f"✅ File ingested: `{attachment.filename}` → `{rel}`{summary_tail}",
        )
        logger.info(
            f"**INGEST (FILE)**: `{attachment.filename}` → `{rel}` in #{channel_name}",
            message.guild,
        )

    def _proposal_fallback_path(self, draft_content: str, attachment_name: str) -> str:
        subject = _slugify_subject(_extract_title(draft_content) or attachment_name.rsplit(".", 1)[0])
        return f"NOTE-{datetime.date.today()}-{subject}.md"

    def _resolve_proposal_target(self, channel_path: str, plan: dict,
                                 draft_content: str, attachment_name: str
                                 ) -> tuple[str, str, str]:
        mode = plan["mode"]
        target_rel = plan["target_rel_path"]
        channel_abs = os.path.abspath(channel_path)
        def _fallback(): return "new", self._proposal_fallback_path(draft_content, attachment_name), ""
        if not target_rel or not target_rel.endswith(".md"): return _fallback()
        candidate_abs = os.path.abspath(os.path.join(channel_path, target_rel))
        try:
            if os.path.commonpath([candidate_abs, channel_abs]) != channel_abs: return _fallback()
        except ValueError: return _fallback()
        if mode == "update":
            if not os.path.exists(candidate_abs): return _fallback()
            with open(candidate_abs, "r") as f: return "update", target_rel, f.read()
        return "new", target_rel, ""

    async def _handle_file_proposal(self, message, attachment, advice: str = ""):
        channel_name = message.channel.name
        draft_content = (await attachment.read()).decode("utf-8", errors="replace")
        if not draft_content.strip():
            await message.channel.send(f"⚠️ `{attachment.filename}` is empty — nothing to propose.")
            return
        kb_context = await asyncio.to_thread(self.kb.get_channel_context, channel_name, draft_content[:300])
        tree_listing = self.kb.list_channel_tree(channel_name)
        plan = await self.agent.plan_file_proposal(draft_content, advice, kb_context, tree_listing, channel_name)
        channel_path = self.kb.get_channel_path(channel_name)
        mode, target_rel, existing_content = self._resolve_proposal_target(channel_path, plan, draft_content, attachment.filename)
        await message.channel.send(f"💡 Generating proposal for `{attachment.filename}` → `{target_rel}` ({mode})...")
        merged = await self.agent.merge_file_proposal(draft_content, existing_content, target_rel, advice, channel_name)
        if mode == "update" and merged.lstrip().startswith("NEW_FILE_INSTEAD"):
            merged = merged.lstrip()[len("NEW_FILE_INSTEAD"):].lstrip("\n") or draft_content
            target_rel = self._proposal_fallback_path(draft_content, attachment.filename)
            mode, existing_content = "new", ""
        if not merged.strip():
            await message.channel.send(f"⚠️ Proposal generation returned empty content.")
            return
        pid = self._create_proposal(channel_name, target_rel, existing_content, merged, advice, plan["rationale"])
        await self._send_proposal(message.channel, pid)

    async def _sync_kb_channels(self, guild):
        existing_names = {ch.name for ch in guild.text_channels}
        root = config.Paths.CHANNELS
        for entry in os.scandir(root):
            if not entry.is_dir() or entry.name.startswith(".") or entry.name in self.RESERVED_CHANNELS:
                continue
            if entry.name not in existing_names:
                try: await guild.create_text_channel(entry.name)
                except: pass

    async def _seed_channel_history(self, channel):
        entries = []
        try:
            async for message in channel.history(limit=50, oldest_first=False):
                if not message.content.strip(): continue
                role = "assistant" if message.author == self.user else message.author.display_name
                timestamp = message.created_at.strftime("%Y-%m-%d %H:%M")
                entries.append(f"[{timestamp}] {role}:\n{message.content}\n\n")
        except: return
        if entries:
            self.kb.seed_history(channel.name, "".join(reversed(entries)))

    async def _log_publisher(self):
        while True:
            msg = await self._log_queue.get()
            if self.guilds:
                await self.send_system_log(self.guilds[0], msg)
            self._log_queue.task_done()

    async def close(self):
        if self._log_publisher_task: self._log_publisher_task.cancel()
        if self.mcp_pool: await self.mcp_pool.close()
        if self.agent: self.agent.close()
        await super().close()

    async def on_guild_join(self, guild):
        if self.kb is not None:
            await guild.leave()
            return
        self.kb = KnowledgeBaseManager(guild.name)
        await self._ensure_reserved_channels(guild)

    async def _ensure_channel(self, guild, name, init_message=None):
        channel = discord.utils.get(guild.text_channels, name=name)
        if not channel:
            try:
                channel = await guild.create_text_channel(name)
                if init_message: await channel.send(init_message)
            except: pass
        return channel

    async def _ensure_reserved_channels(self, guild):
        for name, greeting in self.RESERVED_CHANNELS.items():
            await self._ensure_channel(guild, name, greeting)

    async def send_system_log(self, guild, message):
        channel = await self._ensure_channel(guild, "system-log")
        if channel: await channel.send(message)

    async def send_message_safe(self, channel, content):
        if not content: return
        while len(content) > 2000:
            split_at = content.rfind('\n', 0, 2000)
            if split_at == -1: split_at = 2000
            await channel.send(content[:split_at].strip())
            content = content[split_at:].strip()
        if content: await channel.send(content)

    async def on_message(self, message):
        if message.author == self.user or message.channel.name in self.RESERVED_CHANNELS: return
        for handler in self.message_handlers:
            if await handler.handle(message, self): break
