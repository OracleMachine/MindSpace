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
from mindspace.bot import services
from mindspace.agent import prompts
import mindspace.agent.mcp as mcp_bridge

# CRITICAL: Suppress litellm background logging workers BEFORE they can start.
try:
    import litellm
    litellm._suppress_logging_worker = True
    litellm.suppress_debug_info = True
except ImportError:
    pass

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
        await self.save_and_challenge(channel, channel.guild, commit_msg)
        return commit_msg

    async def save_and_challenge(self, channel, guild, message: str,
                                  view_scope: tuple[str, str] | None = None,
                                  cascade_mode: str = "default") -> dict:
        """Commit and fire the view-tree challenger in the background.

        - view_scope=None: content commit. For each folder touched in this
          commit within `channel`'s KB folder, fire a local-view challenge.
        - view_scope=(channel_name, rel_folder): a view.md was just committed
          at that scope. Fire the upward consistency cascade (cascade_mode
          "default"), and additionally the downward sweep when the commit
          represents direct user intent (cascade_mode "both") — e.g. an
          accepted /change_my_view proposal.

        Returns the save_state result dict.
        """
        result = await asyncio.to_thread(self.kb.save_state, message)
        touched = result.get("touched", set())
        try:
            if view_scope is not None:
                vchan, vrel = view_scope
                asyncio.create_task(
                    services.check_upward_consistency(self, channel, guild, vchan, vrel)
                )
                if cascade_mode == "both":
                    asyncio.create_task(
                        services.check_downward_consistency(self, channel, guild, vchan, vrel)
                    )
            else:
                for (chan_name, rel_folder) in touched:
                    if chan_name != channel.name:
                        continue
                    asyncio.create_task(
                        services.challenge_local_view(self, channel, guild, chan_name, rel_folder)
                    )
        except Exception as e:
            logger.warning(f"save_and_challenge: scheduling challenger failed: {e}")
        return result

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
            await services.handle_organize(self, interaction.channel, interaction.guild)

        @self.tree.command(name="consolidate", description="Synthesize stream of consciousness into a structured article.")
        async def cmd_consolidate(interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            await services.handle_consolidate(self, interaction.channel, interaction.guild)

        @self.tree.command(name="research", description="Deep-dive research on a topic using KB context.")
        @app_commands.describe(topic="The specific topic to research")
        async def cmd_research(interaction: discord.Interaction, topic: str):
            await interaction.response.defer(thinking=True)
            await services.handle_research(self, interaction.channel, interaction.guild, topic, interaction=interaction)

        @self.tree.command(name="change_my_view", description="Update or view the static mindset (view.md) for this channel.")
        @app_commands.describe(instruction="How to update the core mindset (e.g., 'Emphasize local-first development')")
        async def cmd_change_my_view(interaction: discord.Interaction, instruction: str = None):
            await interaction.response.defer(thinking=True)
            await services.handle_change_my_view(self, interaction.channel, interaction.guild, instruction, interaction=interaction)

        @self.tree.command(name="omni", description="Cross-KB synthesis across all channels.")
        @app_commands.describe(query="The broad query to search across the entire knowledge base")
        async def cmd_omni(interaction: discord.Interaction, query: str):
            await interaction.response.defer(thinking=True)
            await services.handle_omni(self, interaction.channel, interaction.guild, query)

        @self.tree.command(name="walkthrough_views", description="Walk the view tree for this channel: challenge each subfolder's view and check parent/child consistency.")
        async def cmd_walkthrough_views(interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            await services.handle_walkthrough_views(self, interaction.channel, interaction.guild, interaction=interaction)

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
                          instruction: str, rationale: str,
                          cascade: str = "default") -> str:
        """Create a pending proposal.

        cascade selects the post-accept consistency sweep for view.md proposals:
          - "default": upward only (used by challenger/conflict-detector proposals)
          - "both": upward + downward (used by /change_my_view, since that is
                    direct user intent that should propagate to descendants)
        Ignored for non-view proposals.
        """
        proposal_id = uuid.uuid4().hex[:8]
        self._pending_proposals[proposal_id] = {
            "channel_name": channel_name,
            "rel_path": rel_path,
            "existing_content": existing_content,
            "proposed_content": proposed_content,
            "rationale": rationale,
            "instruction": instruction,
            "cascade": cascade,
        }
        return proposal_id

    async def _send_proposal(self, channel, proposal_id: str, interaction: discord.Interaction = None):
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
        if interaction:
            try:
                await interaction.edit_original_response(
                    content=content,
                    embed=embed,
                    attachments=[file] if file else [],
                    view=view
                )
            except discord.HTTPException as e:
                logger.warning(f"Failed to edit interaction response: {e}")
                await channel.send(content=content, embed=embed, view=view, file=file)
        else:
            await channel.send(content=content, embed=embed, view=view, file=file)

    async def handle_propose_update(self, channel_name: str, rel_path: str, instruction: str, rationale: str):
        # view.md at any depth is managed exclusively by the view-tree challenger
        # and /change_my_view. Dialogue tools cannot propose updates to it directly.
        if os.path.basename(rel_path).lower() == "view.md":
            return "Error: `view.md` files are managed by the view challenger and `/change_my_view` — dialogue cannot propose updates to them directly."
        
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
            prompt = prompts.PROPOSE_UPDATE_EXISTING_PROMPT.format(instruction=instruction, existing_content=existing_content)
        else:
            prompt = prompts.PROPOSE_UPDATE_NEW_PROMPT.format(instruction=instruction, rel_path=rel_path)
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
        await self.save_and_challenge(message.channel, message.guild, commit_msg)

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
        subject = services.slugify_subject(services.extract_title(draft_content) or attachment_name.rsplit(".", 1)[0])
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
        bot_name = self.user.display_name
        try:
            async for message in channel.history(limit=50, oldest_first=False):
                if not message.content.strip(): continue
                role = f"{bot_name} (AI)" if message.author == self.user else message.author.display_name
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

    async def send_message_safe(self, channel, content, interaction: discord.Interaction = None):
        """Send `content` to Discord, chunked under the 2000-char per-message limit.

        If `interaction` is given, the first chunk edits its deferred response and
        any remaining chunks are sent as followups (falling back to `channel` on
        failure). Without an interaction, all chunks go to `channel.send`.
        """
        if not content: return

        chunks = []
        remaining = content
        while len(remaining) > 2000:
            split_at = remaining.rfind('\n', 0, 2000)
            if split_at == -1: split_at = 2000
            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        if remaining: chunks.append(remaining)
        if not chunks: return

        first, *rest = chunks
        if interaction is not None:
            try:
                await interaction.edit_original_response(content=first)
            except discord.HTTPException as e:
                logger.warning(f"send_message_safe: interaction edit failed, falling back to channel.send: {e}")
                await channel.send(first)
            for chunk in rest:
                try:
                    await interaction.followup.send(chunk)
                except discord.HTTPException as e:
                    logger.warning(f"send_message_safe: followup.send failed, falling back to channel.send: {e}")
                    await channel.send(chunk)
        else:
            for chunk in chunks:
                await channel.send(chunk)

    async def on_message(self, message):
        if message.author == self.user or message.channel.name in self.RESERVED_CHANNELS: return
        for handler in self.message_handlers:
            if await handler.handle(message, self): break
