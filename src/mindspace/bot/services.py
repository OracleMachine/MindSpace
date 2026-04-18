import os
import datetime
import asyncio
import discord
from mindspace.core import config
from mindspace.core.logger import logger
from mindspace.agent import prompts

def _extract_title(markdown: str) -> str | None:
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return None

def _slugify_subject(text: str, max_len: int = 50) -> str:
    import re
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
    await asyncio.to_thread(bot.kb.save_state, commit_msg)
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
    
    subject = _slugify_subject(_extract_title(synthesis) or channel_name)
    filename = f"ARTICLE-{datetime.date.today()}-{subject}.md"
    file_path = os.path.join(channel_path, filename)
    bot.kb.write_file(file_path, synthesis)
    bot.kb.write_file(stream_file, f"# Stream of Consciousness: {channel_name}\n\n")

    commit_msg = await bot.agent.generate_commit_message(
        f"Consolidated thoughts in {channel_name} into {filename}"
    )
    await asyncio.to_thread(bot.kb.save_state, commit_msg)

    await channel.send(f"✅ Consolidation complete. Saved to: {file_path}", file=discord.File(file_path))
    logger.info(f"**CONSOLIDATE**: {channel_name} -> `{filename}`", guild)

async def handle_research(bot, channel, guild, topic, interaction: discord.Interaction = None):
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

    await status(f"🔬 Running Gemini CLI on: {topic}\n(watch console for live progress)")

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

    subject = _slugify_subject(topic)
    filename = f"RESEARCH-{datetime.date.today()}-{subject}.md"
    file_path = os.path.join(channel_path, filename)
    lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- URI: viking://{guild.name}/{channel_name}/{filename}"
    bot.kb.write_file(file_path, report + lineage)

    commit_msg = await bot.agent.generate_commit_message(
        f"Research synthesis on {topic}"
    )
    await asyncio.to_thread(bot.kb.save_state, commit_msg)

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

    subject = _slugify_subject(query)
    filename = f"OMNI-{datetime.date.today()}-{subject}.md"
    file_path = os.path.join(channel_path, filename)
    lineage = f"\n\n---\n**Lineage:**\n- Path: {file_path}\n- URI: viking://{guild.name}/omni/{filename}"
    bot.kb.write_file(file_path, report + lineage)

    commit_msg = await bot.agent.generate_commit_message(
        f"Omni query synthesis: {query}"
    )
    await asyncio.to_thread(bot.kb.save_state, commit_msg)
    await channel.send(f"✅ Omni search complete.", file=discord.File(file_path))
    logger.info(f"**OMNI**: {query}", guild)

async def handle_change_my_view(bot, channel, guild, instruction, interaction=None):
    channel_name = channel.name
    current_view = bot.kb.get_view(channel_name)
    
    if not instruction or not instruction.strip():
        # User just wants to view the current mindset
        msg = f"**Current Mindset (view.md) for #{channel_name}:**\n\n{current_view if current_view else '(empty)'}"
        if interaction:
            try:
                await interaction.edit_original_response(content=msg)
            except discord.HTTPException:
                await channel.send(msg)
        else:
            await channel.send(msg)
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
    
    import re
    new_view = re.sub(r"^```[a-zA-Z]*\n?", "", new_view)
    new_view = re.sub(r"\n?```$", "", new_view)
    new_view = new_view.strip()

    if current_view.strip() == new_view.strip():
        new_view += "\n\n*(Updated based on instruction: " + instruction + ")*"

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
