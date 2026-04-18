import os
import re
import datetime
import asyncio
import discord
from mindspace.core import config
from mindspace.core.logger import logger
from mindspace.agent import prompts


def _view_rel_path(rel_folder: str) -> str:
    return os.path.join(rel_folder, "view.md") if rel_folder else "view.md"


def _scope_label(channel_name: str, rel_folder: str) -> str:
    return f"#{channel_name}/{rel_folder}" if rel_folder else f"#{channel_name}"


async def _run_distill_prompt(bot, channel_name: str, rel_folder: str,
                               current_view: str, local_context: str) -> str:
    prompt = prompts.DISTILL_LOCAL_VIEW_PROMPT.format(
        channel_name=channel_name,
        rel_folder=rel_folder or "<channel-root>",
        current_view=current_view or "(none yet)",
        local_context=local_context or "(empty)",
    )
    out = await bot.agent.brain.run_command_async(prompt)
    return (out or "").strip()


async def _run_conflict_prompt(bot, parent_scope: str, parent_view: str,
                                child_scope: str, child_view: str,
                                target_label: str) -> str:
    prompt = prompts.DETECT_VIEW_CONFLICT_PROMPT.format(
        parent_scope=parent_scope,
        parent_view=parent_view,
        child_scope=child_scope,
        child_view=child_view,
        target_label=target_label,
    )
    out = await bot.agent.brain.run_command_async(prompt)
    return (out or "").strip()


async def challenge_local_view(bot, channel, guild, channel_name: str, rel_folder: str):
    """Re-challenge a folder's local view against its evidence files. Emit a proposal
    on drift. Lazy-bootstraps a view if none exists yet but the folder has content."""
    local_context = bot.kb.read_folder_context(channel_name, rel_folder)
    if not local_context:
        return
    current_view = bot.kb.read_view(channel_name, rel_folder)
    try:
        proposed = await _run_distill_prompt(bot, channel_name, rel_folder, current_view, local_context)
    except Exception as e:
        logger.warning(f"challenge_local_view: LLM failure at {_scope_label(channel_name, rel_folder)}: {e}")
        return
    if not proposed or proposed == "VIEW_OK" or proposed == current_view.strip():
        return
    rel_path = _view_rel_path(rel_folder)
    pid = bot._create_proposal(
        channel_name=channel_name,
        rel_path=rel_path,
        existing_content=current_view,
        proposed_content=proposed,
        instruction=f"Agent challenge: reconcile local view at `{rel_folder or '<root>'}` with new evidence.",
        rationale=f"Local view drift detected in `{rel_folder or '<channel-root>'}`.",
    )
    await bot._send_proposal(channel, pid)
    logger.info(f"**VIEW_CHALLENGE**: proposal emitted for {rel_path} in #{channel_name}", guild)


async def check_upward_consistency(bot, channel, guild, channel_name: str, rel_folder: str):
    """After a view.md update, walk upward and emit a proposal per parent whose stance
    now conflicts with the updated descendant."""
    child_rel = (rel_folder or "").strip("/").strip()
    child_view = bot.kb.read_view(channel_name, child_rel)
    if not child_view:
        return
    child_scope = _scope_label(channel_name, child_rel)
    parent = os.path.dirname(child_rel) if child_rel else None
    while parent is not None:
        parent_view = bot.kb.read_view(channel_name, parent)
        if parent_view:
            parent_scope = _scope_label(channel_name, parent)
            target_label = f"the parent view at {parent_scope}"
            try:
                proposed = await _run_conflict_prompt(
                    bot,
                    parent_scope=parent_scope, parent_view=parent_view,
                    child_scope=child_scope, child_view=child_view,
                    target_label=target_label,
                )
            except Exception as e:
                logger.warning(f"check_upward_consistency: LLM failure at {parent_scope}: {e}")
                proposed = ""
            if proposed and proposed != "VIEW_OK" and proposed != parent_view.strip():
                rel_path = _view_rel_path(parent)
                pid = bot._create_proposal(
                    channel_name=channel_name,
                    rel_path=rel_path,
                    existing_content=parent_view,
                    proposed_content=proposed,
                    instruction=f"Consistency: align parent view at `{parent or '<root>'}` with descendant at `{child_rel or '<root>'}`.",
                    rationale=f"Conflict detected between {child_scope} and {parent_scope}.",
                )
                await bot._send_proposal(channel, pid)
                logger.info(f"**VIEW_CONSISTENCY**: parent proposal at {rel_path} for #{channel_name}", guild)
        if parent == "":
            break
        parent = os.path.dirname(parent)


async def check_downward_consistency(bot, channel, guild, channel_name: str, rel_folder: str):
    """After a parent view update, walk each descendant view and emit a proposal per
    child whose stance now conflicts."""
    parent_rel = (rel_folder or "").strip("/").strip()
    parent_view = bot.kb.read_view(channel_name, parent_rel)
    if not parent_view:
        return
    parent_scope = _scope_label(channel_name, parent_rel)
    root = os.path.join(bot.kb.channels_path, channel_name)
    if not os.path.isdir(root):
        return
    parent_prefix = parent_rel + os.sep if parent_rel else ""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        if "view.md" not in filenames:
            continue
        rel = os.path.relpath(dirpath, root)
        rel = "" if rel == "." else rel
        if rel == parent_rel:
            continue
        if parent_prefix and not rel.startswith(parent_prefix):
            continue
        child_view = bot.kb.read_view(channel_name, rel)
        if not child_view:
            continue
        child_scope = _scope_label(channel_name, rel)
        target_label = f"the child view at {child_scope}"
        try:
            proposed = await _run_conflict_prompt(
                bot,
                parent_scope=parent_scope, parent_view=parent_view,
                child_scope=child_scope, child_view=child_view,
                target_label=target_label,
            )
        except Exception as e:
            logger.warning(f"check_downward_consistency: LLM failure at {child_scope}: {e}")
            continue
        if proposed and proposed != "VIEW_OK" and proposed != child_view.strip():
            rel_path = _view_rel_path(rel)
            pid = bot._create_proposal(
                channel_name=channel_name,
                rel_path=rel_path,
                existing_content=child_view,
                proposed_content=proposed,
                instruction=f"Consistency: align child view at `{rel or '<root>'}` with parent at `{parent_rel or '<root>'}`.",
                rationale=f"Conflict detected between {parent_scope} and {child_scope}.",
            )
            await bot._send_proposal(channel, pid)
            logger.info(f"**VIEW_CONSISTENCY**: child proposal at {rel_path} for #{channel_name}", guild)


async def handle_walkthrough_views(bot, channel, guild, interaction=None):
    """Walk every content folder in the current channel: re-challenge local views and
    run a downward consistency sweep from the channel root."""
    channel_name = channel.name
    await bot.send_message_safe(
        channel, f"🌳 Walking view tree for #{channel_name}...", interaction=interaction
    )
    folders = bot.kb.list_subfolders_with_content(channel_name)
    if not folders:
        await bot.send_message_safe(
            channel, f"#{channel_name} has no content folders to challenge.", interaction=interaction
        )
        return
    for rel_folder in folders:
        await challenge_local_view(bot, channel, guild, channel_name, rel_folder)
    await check_downward_consistency(bot, channel, guild, channel_name, "")
    logger.info(f"**WALKTHROUGH**: scanned #{channel_name} ({len(folders)} folders)", guild)

def extract_title(markdown: str) -> str | None:
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return None

def slugify_subject(text: str, max_len: int = 50) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s[:max_len].rstrip("-") or "untitled"

async def handle_organize(bot, channel, guild):
    await channel.send("🔄 Scanning channel folder...")
    channel_name = channel.name
    channel_path = bot.kb.get_channel_path(channel_name)

    rel_prefix = os.path.join("Channels", channel_name) + os.sep
    local_untracked = [
        f[len(rel_prefix):]
        for f in bot.kb.list_untracked_files()
        if f.startswith(rel_prefix)
    ]

    if not local_untracked:
        await channel.send("✅ No untracked files in this channel to organize.")
        return

    untracked_files = chr(10).join(f'  {f}' for f in local_untracked)
    prompt = prompts.ORGANIZE_PROMPT.format(channel_name=channel_name, untracked_files=untracked_files)

    handle = await bot.agent.stream(
        prompt=prompt,
        cwd=channel_path,
        channel_name=channel_name,
    )
    await bot._render_stream_to_channel(
        channel, header="🔄 Gemini CLI organizing...", handle=handle,
    )
    report = handle.get_full_response()

    commit_msg = await bot.agent.generate_commit_message(
        f"Organized #{channel_name} channel folder via Gemini CLI"
    )
    await bot.save_and_challenge(channel, guild, commit_msg)
    await bot.send_message_safe(channel, report or "No report generated.")
    logger.info(f"**ORGANIZE**: {channel_name} - {commit_msg}", guild)

async def handle_consolidate(bot, channel, guild):
    await channel.send("📑 Consolidating stream of consciousness...")
    channel_name = channel.name
    channel_path = bot.kb.get_channel_path(channel_name)
    stream_file = os.path.join(channel_path, "stream_of_conscious.md")

    with open(stream_file, "r") as f:
        content = f.read()

    prompt = prompts.CONSOLIDATE_PROMPT.format(content=content)
    synthesis = await bot.agent.run_command(prompt, channel_name=channel_name)
    
    subject = slugify_subject(extract_title(synthesis) or channel_name)
    filename = f"ARTICLE-{datetime.date.today()}-{subject}.md"
    file_path = os.path.join(channel_path, filename)
    bot.kb.write_file(file_path, synthesis)
    bot.kb.write_file(stream_file, f"# Stream of Consciousness: {channel_name}\n\n")

    commit_msg = await bot.agent.generate_commit_message(
        f"Consolidated thoughts in {channel_name} into {filename}"
    )
    await bot.save_and_challenge(channel, guild, commit_msg)

    await channel.send(f"✅ Consolidation complete. Saved to: {file_path}", file=discord.File(file_path))
    logger.info(f"**CONSOLIDATE**: {channel_name} -> `{filename}`", guild)

async def handle_research(bot, channel, guild, topic, interaction: discord.Interaction = None):
    logger.info(f"RESEARCH: starting — topic={topic!r}")
    await bot.send_message_safe(channel, f"🔬 Gathering KB context for: {topic}...", interaction=interaction)
    channel_name = channel.name
    channel_path = bot.kb.get_channel_path(channel_name)

    viking_context = await asyncio.to_thread(bot.kb.get_channel_context, channel_name, topic)
    deep_context = await asyncio.to_thread(bot.kb.get_deep_context, channel_name, topic)

    combined = ""
    if viking_context:
        combined += f"--- Semantic Overview (Viking) ---\n{viking_context}\n\n"
    if deep_context:
        combined += f"--- Deep Document Analysis (PageIndex) ---\n{deep_context}\n\n"

    prompt = prompts.RESEARCH_PROMPT.format(
        topic=topic, 
        channel_name=channel_name, 
        combined_context=combined or "(none — the KB had no relevant matches)"
    )

    await bot.send_message_safe(channel, f"🔬 Running Gemini CLI on: {topic}\n(watch console for live progress)", interaction=interaction)

    handle = await bot.agent.stream(
        prompt=prompt,
        cwd=channel_path,
        channel_name=channel_name,
    )
    header = f"🔬 Researching: **{topic}**"
    await bot._render_stream_to_channel(channel, header=header, handle=handle)

    if handle.returncode != 0:
        logger.error(f"RESEARCH: CLI failed with non-zero exit {handle.returncode}")
        await status(f"❌ Gemini CLI exited {handle.returncode}.")
        return

    report = handle.get_full_response()
    if not report:
        await status("⚠️ Research produced no output.")
        return

    subject = slugify_subject(topic)
    filename = f"RESEARCH-{datetime.date.today()}-{subject}.md"
    file_path = os.path.join(channel_path, filename)
    lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- URI: viking://{guild.name}/{channel_name}/{filename}"
    bot.kb.write_file(file_path, report + lineage)

    commit_msg = await bot.agent.generate_commit_message(
        f"Research synthesis on {topic}"
    )
    await bot.save_and_challenge(channel, guild, commit_msg)

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

async def handle_omni(bot, channel, guild, query):
    await channel.send(f"🌐 Gathering global KB context for: {query}...")
    channel_name = channel.name
    channel_path = bot.kb.get_channel_path(channel_name)

    global_context = await asyncio.to_thread(bot.kb.get_global_context, query)

    prompt = prompts.OMNI_PROMPT.format(
        query=query,
        global_context=global_context or "(no KB matches — rely on web search)"
    )

    handle = await bot.agent.stream(
        prompt=prompt,
        cwd=config.Paths.CHANNELS,
        channel_name=channel.name,
    )
    await bot._render_stream_to_channel(
        channel, header=f"🌐 Gemini CLI synthesizing: {query}...", handle=handle,
    )
    report = handle.get_full_response()

    if not report.strip():
        await channel.send("⚠️ Omni synthesis produced no output.")
        return

    subject = slugify_subject(query)
    filename = f"OMNI-{datetime.date.today()}-{subject}.md"
    file_path = os.path.join(channel_path, filename)
    lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- URI: viking://{guild.name}/omni/{filename}"
    bot.kb.write_file(file_path, report + lineage)

    commit_msg = await bot.agent.generate_commit_message(
        f"Omni query synthesis: {query}"
    )
    await bot.save_and_challenge(channel, guild, commit_msg)
    await channel.send(f"✅ Omni search complete.", file=discord.File(file_path))
    logger.info(f"**OMNI**: {query}", guild)

async def handle_change_my_view(bot, channel, guild, instruction, interaction=None):
    channel_name = channel.name
    current_view = bot.kb.get_view(channel_name)
    
    if not instruction or not instruction.strip():
        # User just wants to view the current mindset
        msg = (
            f"**Current Mindset (view.md) for #{channel_name}:**\n\n"
            f"{current_view if current_view else '(empty)'}\n\n"
            f"---\n"
            f"💡 *To update this view, use the command again with an instruction. For example:*\n"
            f"`/change_my_view instruction: Emphasize local-first development`"
        )
        await bot.send_message_safe(channel, msg, interaction=interaction)
        return

    status_msg = None
    if not interaction:
        status_msg = await channel.send(f"🔄 Thinking about your mindset change: {instruction}...")

    prompt = prompts.CHANGE_VIEW_PROMPT.format(
        channel_name=channel_name,
        current_view=current_view if current_view else "(empty)",
        instruction=instruction
    )
    
    new_view = await bot.agent.run_command(prompt, channel_name=channel_name)

    new_view = new_view.strip()
    new_view = re.sub(r"^\s*```[a-zA-Z]*\n?", "", new_view)
    new_view = re.sub(r"\n?\s*```\s*$", "", new_view)
    new_view = new_view.strip()

    if current_view.strip() == new_view.strip():
        msg = "The LLM did not produce any changes to the current view. Try rephrasing your instruction."
        await bot.send_message_safe(channel, msg, interaction=interaction)
        return

    proposal_id = bot._create_proposal(
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

    await bot._send_proposal(channel, proposal_id, interaction=interaction)
    logger.info(f"**CHANGE_MY_VIEW**: Proposed update for #{channel_name}: {instruction}", guild)
