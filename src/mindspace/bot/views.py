import io
import difflib
import discord
import asyncio
from mindspace.core.logger import logger

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
    """Build a proposal message payload."""
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
        import os
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

        # Direction-gate the post-commit challenger: a view.md acceptance only
        # fires the upward consistency cascade; anything else fires the
        # per-folder local challenge.
        is_view_commit = os.path.basename(proposal["rel_path"]).lower() == "view.md"
        if is_view_commit:
            rel_folder = os.path.dirname(proposal["rel_path"])
            view_scope = (proposal["channel_name"], rel_folder)
        else:
            view_scope = None
        await self.bot.save_and_challenge(
            interaction.channel, interaction.guild, commit_msg, view_scope=view_scope
        )

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
