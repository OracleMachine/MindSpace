import discord
import os
import re
import io
import datetime
import difflib
import config
import asyncio
import functools
import git
import uuid
import inspect
import pathlib
from discord import app_commands

# CRITICAL: Suppress litellm background logging workers BEFORE they can start.
# This prevents 'Event loop is closed' errors during pre-launch indexing.
try:
    import litellm
    litellm._suppress_logging_worker = True
    litellm.suppress_debug_info = True
except ImportError:
    pass


def _render_diff(existing: str, proposed: str, file_path: str) -> str:
    if not existing:
        diff_lines = [f"+{line}" for line in proposed.splitlines()]
        return f"--- /dev/null\n+++ b/{file_path}\n" + "\n".join(diff_lines)
    return "\n".join(difflib.unified_diff(
        existing.splitlines(), proposed.splitlines(),
        fromfile=f"a/{file_path}", tofile=f"b/{file_path}", lineterm=""
    ))


_DIFF_EMBED_LIMIT = 4000  # headroom under Discord's 4096-char embed description cap


def _format_proposal_message(rel_path: str, rationale: str, diff_text: str):
    """Build a proposal message payload.

    Returns `(content, embed, file)` where:
      - `content` is the short header line (always visible in the feed).
      - `embed` carries the diff in its description — up to ~4 KB inline,
        roughly 2.7× what the previous single-message code fence allowed.
      - `file` is a `discord.File` with the *complete* diff, attached when
        even the embed limit forces truncation. `None` otherwise.

    Callers should pass all three fields to `send(...)` / the edit equivalent
    so nothing gets lost on long updates.
    """
    content = (
        f"\U0001f4a1 **KB Update Proposal for `{rel_path}`**\n"
        f"> {rationale}"
    )

    fence_overhead = len("```diff\n\n```")
    inline_limit = _DIFF_EMBED_LIMIT - fence_overhead

    attachment = None
    if len(diff_text) > inline_limit:
        marker = "\n... (truncated — full diff attached)"
        cut = inline_limit - len(marker)
        preview = diff_text[:cut].rsplit("\n", 1)[0] + marker
        safe_name = rel_path.replace("/", "_").replace("\\", "_") + ".diff"
        attachment = discord.File(
            io.BytesIO(diff_text.encode("utf-8")), filename=safe_name
        )
    else:
        preview = diff_text

    embed = discord.Embed(
        description=f"```diff\n{preview}\n```",
        color=0x3498db,
    )
    return content, embed, attachment


class ProposalView(discord.ui.View):
    def __init__(self, bot, proposal_id, timeout=3600):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.proposal_id = proposal_id

    async def on_timeout(self):
        self.bot._pending_proposals.pop(self.proposal_id, None)

    @discord.ui.button(label="Apply", style=discord.ButtonStyle.green, emoji="✅")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        proposal = self.bot._pending_proposals.pop(self.proposal_id, None)
        if not proposal:
            await interaction.edit_original_response(content="⚠️ Proposal expired or not found.", view=None)
            return

        abs_path = os.path.join(
            self.bot.kb.channels_path, proposal["channel_name"], proposal["rel_path"]
        )
        # Write to disk only now
        self.bot.kb.write_file(abs_path, proposal["proposed_content"])

        # Index immediately so the search index picks up the new content
        # before the final commit/indexing loop.
        await asyncio.to_thread(self.bot.kb.index_files, [os.path.join("Channels", proposal["channel_name"], proposal["rel_path"])])

        # Generate a commit message based on the update's rationale
        commit_msg = await self.bot.agent.generate_commit_message(
            f"KB update: {proposal['rel_path']} — {proposal['rationale']}"
        )
        await asyncio.to_thread(self.bot.kb.save_state, commit_msg)

        await interaction.edit_original_response(
            content=f"✅ **Applied proposal:** {proposal['rationale']}\n*(Written, indexed, and committed)*", view=None
        )
        logger.info(f"PROPOSAL: Applied change to {proposal['rel_path']}", interaction.guild)

    @discord.ui.button(label="Discard", style=discord.ButtonStyle.red, emoji="🗑️")
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        proposal = self.bot._pending_proposals.pop(self.proposal_id, None)
        rationale = proposal["rationale"] if proposal else "unknown"
        await interaction.edit_original_response(content=f"🗑️ **Discarded proposal:** {rationale}", view=None)

    @discord.ui.button(label="Refine", style=discord.ButtonStyle.blurple, emoji="✍️")
    async def refine(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RefineModal(self))


class RefineModal(discord.ui.Modal, title="Refine Proposal"):
    feedback = discord.ui.TextInput(
        label="How should this be changed?",
        style=discord.TextStyle.paragraph,
        placeholder='e.g., "Make it shorter", "Add more details about X"',
        required=True,
    )

    def __init__(self, view: ProposalView):
        super().__init__()
        self.proposal_view = view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        proposal = self.proposal_view.bot._pending_proposals.get(self.proposal_view.proposal_id)
        if not proposal:
            return

        await interaction.edit_original_response(
            content=f"⏳ **Refining proposal based on feedback:** {self.feedback.value}...", view=None
        )

        # Isolated Refinement Agent - only pass the proposed version and user feedback
        # to save tokens and focus on the iterative update.
        prompt = f"""
Modify the following proposed Knowledge Base update based on this user feedback:
FEEDBACK: {self.feedback.value}

PREVIOUS PROPOSED VERSION:
---
{proposal['proposed_content']}
---

TASK:
- Incorporate the feedback into the proposed version.
- Maintain existing tone, formatting, and markdown structure.
- Output ONLY the complete, revised markdown document.
- NO commentary or conversational filler.
"""
        revised = await self.proposal_view.bot.agent.brain.run_command_async(prompt)

        proposal["proposed_content"] = revised
        diff_text = _render_diff(proposal["existing_content"], revised, proposal["rel_path"])
        content, embed, file = _format_proposal_message(
            proposal["rel_path"], proposal["rationale"], diff_text
        )
        view = ProposalView(self.proposal_view.bot, self.proposal_view.proposal_id)
        # `attachments=[...]` fully replaces the prior attachments — pass the
        # refined diff file if it still overflows, or clear any stale file when
        # the refined diff fits inline.
        await interaction.edit_original_response(
            content=content,
            embed=embed,
            attachments=[file] if file is not None else [],
            view=view,
        )


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


from agent import MindSpaceAgent
from tools import MindSpaceTools
from manager import KnowledgeBaseManager
from logger import logger
import mcp_bridge

class MindSpaceBot(discord.Client):
    # Reserved channels are auto-created on startup and never processed by
    # on_message. Each value is the init greeting posted on first creation.
    RESERVED_CHANNELS = {
        "system-log": "🚀 **MindSpace System Log Initialized.**",
        "notification": "🔔 **MindSpace Notification Channel Initialized.**",
    }

    HELP_TEXT = (pathlib.Path(__file__).parent / "help.md").read_text(encoding="utf-8")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.agent = None  # initialized in setup_hook (inside the event loop)
        self.kb = None     # initialized in on_ready (after guild info available)
        self.tools = None
        self.mcp_pool = None  # initialized in setup_hook; drains into dialogue brain tools
        self._pending_proposals: dict[str, dict] = {}
        self.tree = app_commands.CommandTree(self)

        # Discord Log Queue (Async sink for the sync logger)
        self._log_queue = asyncio.Queue()
        self._log_publisher_task = None


    async def setup_hook(self):
        """Initialize the agent, register slash commands, and start background tasks.
        Runs inside the event loop — genai.Client binds to the correct loop here.
        """
        self.agent = MindSpaceAgent()
        mcp_bridge.sync_cli_settings()
        self.mcp_pool = mcp_bridge.MCPSessionPool(config.MCP.SERVERS)
        await self.mcp_pool.connect()

        # Start the log publisher immediately
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

        # Initialize the SINGLE KnowledgeBaseManager for the first guild
        guild = self.guilds[0]
        
        # Inject synchronous logging callback.
        # Uses call_soon_threadsafe so logger.info() works from any thread.
        def bridge_to_discord(msg: str):
            self.loop.call_soon_threadsafe(self._log_queue.put_nowait, msg)
        logger.set_callback(bridge_to_discord)

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

        logger.info("Startup: ensuring reserved channels...")
        await self._ensure_reserved_channels(guild)
        logger.info("Startup: syncing KB folders → Discord channels...")
        await self._sync_kb_channels(guild)

        # Get Git info for both repos
        agent_repo = git.Repo(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        agent_git = agent_repo.git.describe("--tags", "--dirty", "--always")
        thought_git = self.kb._repo.git.describe("--tags", "--dirty", "--always")

        logger.info(f"Startup: all indexing complete — bot fully ready (Agent: {agent_git} Thought: {thought_git})")

        for channel in guild.text_channels:
            if channel.name not in self.RESERVED_CHANNELS:
                await self._seed_channel_history(channel)

    # --- CORE COMMAND LOGIC (Agnostic to Trigger) ---

    async def _render_stream_to_channel(self, channel, header: str, handle) -> str:
        """Render a CLI stream handle to a single live-updating Discord message.

        `handle` is any async-iterable that yields clean assistant content chunks.
        This method is pure UI — it knows nothing about subprocesses, env, or the
        CLI itself. Returns the full joined output.
        """
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
        """Synthesize stream of consciousness into a structured permanent article."""
        await channel.send("📑 Consolidating stream of consciousness...")
        channel_name = channel.name
        channel_path = self.kb.get_channel_path(channel_name)
        stream_file = os.path.join(channel_path, "stream_of_conscious.md")

        with open(stream_file, "r") as f:
            content = f.read()

        synthesis = await self.agent.run_command(
            f"Synthesize these thoughts into a structured permanent article:\n\n{content}"
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
        """
        Deep-dive on topic via Gemini CLI (web search + KB context).

        If triggered via slash command (interaction provided), all status
        updates edit the deferred "thinking..." message in place — no
        follow-ups are sent. Text-command fallback uses channel.send.

        Streams CLI output via agent.cli_brain.stream() — each chunk is
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
        header = f"🔬 Researching: **{topic}**"
        await self._render_stream_to_channel(channel, header=header, handle=handle)

        logger.info(f"RESEARCH: CLI exited — returncode={handle.returncode}")

        if handle.returncode != 0:
            logger.error(f"RESEARCH: CLI failed with non-zero exit {handle.returncode}")
            await status(f"❌ Gemini CLI exited {handle.returncode}.")
            return

        report = handle.get_full_response()
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

        commit_msg = await self.agent.generate_commit_message(
            f"Research synthesis on {topic}"
        )
        await asyncio.to_thread(self.kb.save_state, commit_msg)

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

    async def handle_omni(self, channel, guild, query):
        """Cross-KB synthesis across all channels via Gemini CLI with live streaming."""
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

        # cwd=Channels/ so the CLI can read across all channels (omni's whole
        # point) but is sandboxed from bot-home/, openviking/, and ov.conf.
        handle = await self.agent.cli_brain.stream(
            prompt=prompt,
            cwd=config.Paths.CHANNELS,
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

    async def handle_help(self, guild):
        """Post the usage guide to #notification. Returns the channel (or None)."""
        channel = await self._ensure_channel(
            guild, "notification", self.RESERVED_CHANNELS["notification"]
        )
        if channel:
            await self.send_message_safe(channel, self.HELP_TEXT)
            logger.info("**HELP**: posted usage guide to #notification", guild)
        return channel

    async def handle_sync(self, channel, guild):
        """Manually trigger a native directory sync for the current channel."""
        channel_name = channel.name
        await channel.send(f"🔄 Syncing Knowledge Base for `#{channel_name}`...")
        
        try:
            # Trigger native directory sync for this channel (O(N) CPU scan, but O(1) token embeddings)
            await asyncio.to_thread(self.kb.viking.rebuild_index, channel_name)
            await channel.send(f"✅ KB sync complete for `#{channel_name}`. All manual edits and reorganizations are now indexed.")
            logger.info(f"**SYNC**: Manual sync triggered for #{channel_name}", guild)
        except Exception as e:
            await channel.send(f"❌ Sync failed: {e}")
            logger.error(f"**SYNC**: Failed for #{channel_name}: {e}", guild)

    def _create_proposal(self, channel_name: str, rel_path: str,
                          existing_content: str, proposed_content: str,
                          instruction: str, rationale: str) -> str:
        """Store a proposal in memory and return its 8-char hex id."""
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
        """Render a pending proposal as a diff message with Apply/Discard/Refine buttons."""
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
        """Callback triggered by the propose_update tool.
        Uses an isolated LLM call to generate proposed content in memory.
        Returns the proposal_id on success, or an error message."""
        channel_path = self.kb.get_channel_path(channel_name)
        abs_path = os.path.abspath(os.path.join(channel_path, rel_path))

        # Security check: ensure path is under Channels/
        channels_abs = os.path.abspath(config.Paths.CHANNELS)
        if os.path.commonpath([abs_path, channels_abs]) != channels_abs:
            return "Error: Security violation — cannot edit files outside the knowledge base."

        existing_content = ""
        if os.path.exists(abs_path):
            with open(abs_path, "r") as f:
                existing_content = f.read()

        # Isolated Rewrite Agent (Stateless)
        # This keeps the Dialogue History lean and prevents context bloat.
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
        logger.info(f"PROPOSAL: Generating rewrite for {rel_path} in #{channel_name}...")
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

    # --- FILE DROP HANDLERS ---

    def _sanitize_subfolder(self, channel_name: str, subfolder: str) -> str:
        """Return a safe subfolder relative to the channel folder, or '' on
        rejection. `commonpath` catches traversal, absolute paths, and symlinks
        in one check — no need for string-level `..` filtering first."""
        if not subfolder:
            return ""
        sub = subfolder.strip().strip("/")
        if not sub:
            return ""
        channel_abs = os.path.abspath(os.path.join(self.kb.channels_path, channel_name))
        target_abs = os.path.abspath(os.path.join(channel_abs, sub))
        try:
            if os.path.commonpath([target_abs, channel_abs]) != channel_abs:
                logger.warning(f"autoroute: rejected subfolder {subfolder!r} (escapes channel)")
                return ""
        except ValueError:
            # commonpath raises on absolute paths with different drives (Windows)
            logger.warning(f"autoroute: rejected subfolder {subfolder!r} (commonpath failed)")
            return ""
        return sub

    def _sanitize_filename(self, filename: str, fallback: str) -> str:
        """Strip path separators and hidden-file prefixes."""
        if not filename:
            return fallback
        name = os.path.basename(filename).lstrip(".")
        return name or fallback

    def _dedupe_path(self, abs_path: str) -> str:
        """If abs_path exists, append -2, -3, ... before the extension until free."""
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
        """Path A — route a dropped file into a content-appropriate subfolder of
        the current channel and rename by content. Writes bytes directly to the
        final path (no staging) and then runs `analyze_file` for PDF indexing
        and a content summary. `advice` is optional steering text, non-empty
        only when the user @mentioned the bot with a non-`.md` file."""
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
        """Content-derived new-file path used whenever the planner's target is
        unusable (empty, non-`.md`, escapes channel, or missing on disk)."""
        subject = _slugify_subject(
            _extract_title(draft_content) or attachment_name.rsplit(".", 1)[0]
        )
        return f"NOTE-{datetime.date.today()}-{subject}.md"

    def _resolve_proposal_target(self, channel_path: str, plan: dict,
                                 draft_content: str, attachment_name: str
                                 ) -> tuple[str, str, str]:
        """Validate the planner's target and resolve `existing_content`. Falls
        back to a content-derived new-file path on any validation failure.
        Returns (mode, target_rel, existing_content)."""
        mode = plan["mode"]
        target_rel = plan["target_rel_path"]
        channel_abs = os.path.abspath(channel_path)

        def _fallback() -> tuple[str, str, str]:
            return "new", self._proposal_fallback_path(draft_content, attachment_name), ""

        if not target_rel or not target_rel.endswith(".md"):
            return _fallback()

        candidate_abs = os.path.abspath(os.path.join(channel_path, target_rel))
        try:
            if os.path.commonpath([candidate_abs, channel_abs]) != channel_abs:
                logger.warning(f"file_proposal: rejected target_rel {target_rel!r} (escapes channel)")
                return _fallback()
        except ValueError:
            logger.warning(f"file_proposal: rejected target_rel {target_rel!r} (commonpath failed)")
            return _fallback()

        if mode == "update":
            if not os.path.exists(candidate_abs):
                logger.info(
                    f"file_proposal: planned update target {target_rel!r} missing on disk; "
                    "flipping to new"
                )
                return _fallback()
            with open(candidate_abs, "r") as f:
                return "update", target_rel, f.read()

        return "new", target_rel, ""

    async def _handle_file_proposal(self, message, attachment, advice: str = ""):
        """Path B — user @mentioned the bot with a `.md` drop. LLM decides whether
        to merge into an existing KB file or create a new one, then generates the
        content and surfaces it through the proposal UI. `advice` is optional
        steering text and may be empty — the mention itself is the trigger."""
        channel_name = message.channel.name

        draft_content = (await attachment.read()).decode("utf-8", errors="replace")
        if not draft_content.strip():
            await message.channel.send(
                f"⚠️ `{attachment.filename}` is empty — nothing to propose."
            )
            return

        kb_context = await asyncio.to_thread(
            self.kb.get_channel_context, channel_name, draft_content[:300]
        )
        tree_listing = self.kb.list_channel_tree(channel_name)

        plan = await self.agent.plan_file_proposal(
            draft_content=draft_content,
            advice=advice,
            kb_context=kb_context,
            tree_listing=tree_listing,
            channel_name=channel_name,
        )
        channel_path = self.kb.get_channel_path(channel_name)
        mode, target_rel, existing_content = self._resolve_proposal_target(
            channel_path, plan, draft_content, attachment.filename
        )
        rationale = plan["rationale"]

        await message.channel.send(
            f"💡 Generating proposal for `{attachment.filename}` → `{target_rel}` ({mode})..."
        )

        merged = await self.agent.merge_file_proposal(
            draft_content=draft_content,
            existing_content=existing_content,
            advice=advice,
            mode=mode,
        )

        # Sentinel escape: LLM rejects the update target after seeing fresh content.
        if mode == "update" and merged.lstrip().startswith("NEW_FILE_INSTEAD"):
            logger.info(f"file_proposal: merge rejected target {target_rel!r}; flipping to new")
            merged = merged.lstrip()[len("NEW_FILE_INSTEAD"):].lstrip("\n") or draft_content
            target_rel = self._proposal_fallback_path(draft_content, attachment.filename)
            mode, existing_content = "new", ""

        if not merged.strip():
            await message.channel.send(
                f"⚠️ Proposal generation returned empty content for `{attachment.filename}`."
            )
            return

        pid = self._create_proposal(
            channel_name=channel_name,
            rel_path=target_rel,
            existing_content=existing_content,
            proposed_content=merged,
            instruction=advice,
            rationale=rationale,
        )
        await self._send_proposal(message.channel, pid)
        logger.info(
            f"**PROPOSE (FILE)**: `{attachment.filename}` -> `{target_rel}` ({mode}) in #{channel_name}",
            message.guild,
        )

    # --- HELPERS ---

    async def _sync_kb_channels(self, guild):
        """Create Discord channels for any KB folders that don't have a matching channel."""
        existing_names = {ch.name for ch in guild.text_channels}
        root = config.Paths.CHANNELS
        try:
            for entry in os.scandir(root):
                if not entry.is_dir() or entry.name.startswith(".") or entry.name in self.RESERVED_CHANNELS:
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

    async def _log_publisher(self):
        """Background task to drain the internal log queue and send to Discord."""
        while True:
            msg = await self._log_queue.get()
            try:
                # We log to the first guild by default
                if self.guilds:
                    await self.send_system_log(self.guilds[0], msg)
            except Exception as e:
                # Don't use logger.error here or you'll loop!
                print(f"Failed to send log to Discord: {e}")
            finally:
                self._log_queue.task_done()

    async def close(self):
        """Cleanly shut down: close GenAI client and cancel background tasks."""
        if self._log_publisher_task and not self._log_publisher_task.done():
            self._log_publisher_task.cancel()
            try:
                await self._log_publisher_task
            except asyncio.CancelledError:
                pass

        if getattr(self, "mcp_pool", None):
            await self.mcp_pool.close()

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
        await self._ensure_reserved_channels(guild)

    async def _ensure_channel(self, guild, name: str, init_message: str | None = None):
        """Ensure a named text channel exists. Returns the channel (or None on
        failure). Posts `init_message` only when the channel is first created."""
        channel = discord.utils.get(guild.text_channels, name=name)
        if not channel:
            try:
                channel = await guild.create_text_channel(name)
                if init_message:
                    await channel.send(init_message)
            except Exception as e:
                logger.error(f"Error creating #{name} in {guild.name}: {e}")
        return channel

    async def _ensure_reserved_channels(self, guild):
        """Ensure every RESERVED_CHANNELS entry exists in the guild."""
        for name, greeting in self.RESERVED_CHANNELS.items():
            await self._ensure_channel(guild, name, greeting)

    async def send_system_log(self, guild, message):
        """Internal method for logger to send to Discord."""
        channel = await self._ensure_channel(
            guild, "system-log", self.RESERVED_CHANNELS["system-log"]
        )
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
        if channel_name in self.RESERVED_CHANNELS:
            return
        channel_path = self.kb.get_channel_path(channel_name)

        # --- 1. ACTIVE COMMANDS (! prefix — parity with slash commands) ---
        if message.content.startswith('!'):
            command_parts = message.content[1:].split(' ', 1)
            cmd = command_parts[0].lower()
            args = command_parts[1] if len(command_parts) > 1 else ""

            if cmd == "organize":
                await self.handle_organize(message.channel, message.guild)
            elif cmd == "consolidate":
                await self.handle_consolidate(message.channel, message.guild)
            elif cmd == "research":
                if not args:
                    await message.channel.send("Usage: `!research [topic]`")
                    return
                await self.handle_research(message.channel, message.guild, args, message.id)
            elif cmd == "omni":
                if not args:
                    await message.channel.send("Usage: `!omni [query]`")
                    return
                await self.handle_omni(message.channel, message.guild, args)
            elif cmd == "sync":
                if args:
                    await message.channel.send("Usage: `!sync` (takes no arguments)")
                    return
                await self.handle_sync(message.channel, message.guild)
            elif cmd == "help":
                # Delete the invocation first so working channels stay clean;
                # the actual help content lands in #notification.
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound):
                    logger.warning(
                        "Could not delete !help invocation "
                        "(missing Manage Messages permission?)"
                    )
                await self.handle_help(message.guild)
            return

        # --- 2. KNOWLEDGE INGESTION (URLs / file attachments) ---
        elif "http://" in message.content or "https://" in message.content:
            await message.channel.send("🌐 URL detected. Snapshotting to KB...")
            markdown_snapshot = await self.agent.process_url(message.content, channel_name)
            subject = _slugify_subject(_extract_title(markdown_snapshot) or "webpage")
            filename = f"WEBPAGE-{datetime.date.today()}-{subject}.md"
            file_path = os.path.join(channel_path, filename)
            self.kb.write_file(file_path, markdown_snapshot)

            commit_msg = await self.agent.generate_commit_message(
                f"Ingested webpage snapshot: {filename}"
            )
            await asyncio.to_thread(self.kb.save_state, commit_msg)
            await self.send_message_safe(message.channel, f"✅ Link ingested and snapshotted: {file_path}")
            logger.info(f"**INGEST (URL)**: {message.content[:50]}... -> `{filename}`", message.guild)

        elif message.attachments:
            # Mention is the explicit trigger for the heavy (reviewed) path.
            # Without a mention, files are silently autorouted. Advice is the
            # user's message minus the mention token — may be empty.
            mentioned = self.user.mentioned_in(message)
            advice = re.sub(r"<@!?\d+>", "", message.content).strip()
            for attachment in message.attachments:
                is_md = attachment.filename.lower().endswith((".md", ".markdown"))
                if mentioned and is_md:
                    await message.channel.send(
                        f"✍️ Reviewed ingest for `{attachment.filename}` — preparing proposal..."
                    )
                    await self._handle_file_proposal(message, attachment, advice)
                else:
                    if mentioned:
                        await message.channel.send(
                            f"📥 `{attachment.filename}` — proposal flow is `.md`-only; "
                            "routing with your advice as a hint..."
                        )
                    else:
                        await message.channel.send(
                            f"📥 `{attachment.filename}` — routing into KB by content..."
                        )
                    await self._handle_file_autoroute(
                        message, attachment,
                        advice=advice if mentioned else "",
                    )

        # --- 3. PASSIVE DIALOGUE (thought recording + tool-augmented replies) ---
        else:
            proposal_ids = []
            async def on_propose(c, p, i, r):
                res = await self.handle_propose_update(c, p, i, r)
                if not res.startswith("Error") and len(res) <= 8: # success returns 8-char hex id
                    proposal_ids.append(res)
                    return f"Proposal for `{p}` generated and queued for your review."
                return res

            available_tools = self.tools.get_tools(channel_name, on_propose_update=on_propose)

            status_msg = await message.channel.send("🧠 **Thinking...**")

            async def on_progress(text: str):
                try:
                    await status_msg.edit(content=f"🧠 **Thinking...**\n{text}")
                except Exception:
                    pass

            def _wrap_tool(fn):
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
                        
                        # Concise one-liner for successful tool execution
                        res_preview = str(result)[:100].replace('\n', ' ') + ("..." if len(str(result)) > 100 else "")
                        logger.info(f"Tool: {call_summary} -> {res_preview}")
                        return result
                    except Exception as e:
                        logger.error(f"Tool Error: {fn.__name__} failed: {e}")
                        raise
                return inner

            wrapped_tools = [_wrap_tool(t) for t in available_tools]

            # Wrap MCP session.call_tool so progress fires for MCP tools too.
            _mcp_originals = {}
            if self.mcp_pool:
                for srv, session in self.mcp_pool.sessions.items():
                    orig = session.call_tool
                    _mcp_originals[srv] = orig
                    async def _wrapped(name, arguments=None, *, _orig=orig, _srv=srv, **kw):
                        arg_str = ", ".join(f"{k}={repr(v)[:60]}" for k, v in (arguments or {}).items())
                        await on_progress(f"🌐 MCP {_srv}: {name}({arg_str})")
                        return await _orig(name, arguments, **kw)
                    session.call_tool = _wrapped

            try:
                reply = await self.agent.engage_dialogue(
                    message.content,
                    channel_name,
                    history=self.kb.get_history(channel_name),
                    tools=wrapped_tools,
                    mcp_sessions=self.mcp_pool.sessions if self.mcp_pool else None,
                )
            finally:
                for srv, orig in _mcp_originals.items():
                    self.mcp_pool.sessions[srv].call_tool = orig

            try:
                await status_msg.delete()
            except Exception:
                pass

            self.kb.append_history(channel_name, message.author.display_name, message.content)
            self.kb.append_history(channel_name, "assistant", reply)

            await self.send_message_safe(message.channel, reply)

            # --- SEND PENDING PROPOSALS ---
            # We process any proposals queued by the propose_update tool this turn.
            for pid in proposal_ids:
                await self._send_proposal(message.channel, pid)


def _preflight_check():
    """
    Verify all required dependencies are installed and API keys are valid.
    Uses lightweight checks to avoid redundant manager initialization.
    """
    logger.info("Preflight: checking PageIndex install...")
    try:
        from pageindex import PageIndexClient
    except ImportError:
        raise RuntimeError("PageIndex is not installed. Run: pip install pageindex")

    logger.info("Preflight: checking OpenViking install...")
    try:
        import openviking as ov
    except ImportError:
        raise RuntimeError("OpenViking is not installed. Run: pip install openviking")

    logger.info("Preflight: checking GitPython install...")
    try:
        import git
    except ImportError:
        raise RuntimeError("GitPython is not installed. Run: pip install GitPython")

    # API Key Validation (Minimal live calls)
    try:
        logger.info("Preflight: validating PageIndex API key (list_documents)...")
        pi_client = PageIndexClient(api_key=config.Auth.PAGEINDEX_API_KEY)
        pi_client.list_documents(limit=1)

        logger.info("Preflight: initializing OpenViking client...")
        os.environ.setdefault("OPENVIKING_CONFIG_FILE", config.Paths.VIKING_CONF)
        ov_client = ov.SyncOpenViking(path=config.Paths.VIKING_DATA)
        ov_client.initialize()
        logger.info("Preflight: probing OpenViking with test query...")
        ov_client.find("preflight check", limit=1)
        ov_client.close()
    except Exception as e:
        raise RuntimeError(f"API key validation failed: {e}")

    # MCP servers — connect once to verify reachability and advertise tool counts.
    # Per-server failures are logged but do not abort startup (matches runtime
    # pool behavior: a transient MCP outage shouldn't take the bot down).
    if config.MCP.SERVERS:
        logger.info(f"Preflight: probing {len(config.MCP.SERVERS)} MCP server(s)...")
        import mcp_bridge

        async def _probe_mcp():
            pool = mcp_bridge.MCPSessionPool(config.MCP.SERVERS)
            await pool.connect()
            try:
                for name, tool_names in pool.tool_lists.items():
                    logger.info(
                        f"Preflight: MCP — {name} exposes {len(tool_names)} tool(s)"
                    )
                connected = len(pool.sessions)
                total = len(config.MCP.SERVERS)
                if connected == 0:
                    logger.warning(f"Preflight: MCP — 0/{total} servers reachable")
                else:
                    logger.info(f"Preflight: MCP — {connected}/{total} servers reachable")
            finally:
                await pool.close()

        asyncio.run(_probe_mcp())
    else:
        logger.debug("Preflight: no MCP servers configured, skipping probe")

    logger.info("Preflight: all checks passed")


def _startup_indexing():
    """
    Perform blocking Knowledge Base indexing BEFORE launching the Discord bot.
    This ensures Viking and PageIndex are ready without stalling the Discord heartbeat.
    """
    logger.info("Startup Indexing: initializing Viking and PageIndex...")
    from viking import VikingContextManager
    from pageindex_manager import PageIndexManager

    viking = VikingContextManager(config.Paths.CHANNELS)
    pageindex = PageIndexManager()

    logger.info("Startup Indexing: running OpenViking rebuild_index (blocking)...")
    viking.rebuild_index()

    logger.info("Startup Indexing: running PageIndex rebuild_index (blocking)...")
    pageindex.rebuild_index(config.Paths.CHANNELS)
    
    # Clean up the sync clients before starting the async bot
    viking.close()
    logger.info("Startup Indexing: complete.")


if __name__ == "__main__":
    logger.info("========== Launching..... ==========")
    required_vars = {
        "DISCORD_TOKEN": config.Auth.DISCORD_TOKEN,
        "GEMINI_API_KEY": config.Auth.GEMINI_API_KEY,
        "PAGEINDEX_API_KEY": config.Auth.PAGEINDEX_API_KEY,
    }

    missing = [var for var, val in required_vars.items() if not val]
    if missing:
        logger.error(f"❌ Missing required environment variables: {', '.join(missing)}")
        logger.error("Please ensure they are exported in your ZSH environment (~/.zshrc).")
        exit(1)

    try:
        _preflight_check()
        _startup_indexing()
    except Exception as e:
        logger.error(f"❌ Pre-launch check or indexing failed: {e}")
        exit(1)

    logger.info("✅ Startup ready. Launching MindSpace Bot...")
    intents = discord.Intents.default()
    intents.message_content = True
    bot = MindSpaceBot(intents=intents)
    bot.run(config.Auth.DISCORD_TOKEN)
