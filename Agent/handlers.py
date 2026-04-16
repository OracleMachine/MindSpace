import os
import re
import asyncio
import functools
import inspect
import datetime
from typing import Protocol, List, TYPE_CHECKING
import discord
from logger import logger

if TYPE_CHECKING:
    from Agent.bot import MindSpaceBot

class MessageHandler(Protocol):
    async def handle(self, message: discord.Message, bot: 'MindSpaceBot') -> bool:
        ...

class ActiveCommandHandler:
    async def handle(self, message: discord.Message, bot: 'MindSpaceBot') -> bool:
        if not message.content.startswith('!'):
            return False

        parts = message.content[1:].split(' ', 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "help":
            await self._delete_quietly(message)
            await bot.handle_help(message.guild)
            return True

        # Registry for simple routing
        commands = {
            "organize": (bot.handle_organize, False, None),
            "consolidate": (bot.handle_consolidate, False, None),
            "research": (bot.handle_research, True, "Usage: `!research [topic]`"),
            "omni": (bot.handle_omni, True, "Usage: `!omni [query]`"),
            "sync": (bot.handle_sync, False, "Usage: `!sync` (takes no arguments)"),
            "change_my_view": (bot.handle_change_my_view, True, "Usage: `!change_my_view [instruction]`"),
        }

        if cmd not in commands:
            return False

        method, needs_args, usage = commands[cmd]
        
        if needs_args and not args:
            await message.channel.send(usage)
        elif cmd == "sync" and args:
            await message.channel.send(usage)
        else:
            if cmd == "research":
                await method(message.channel, message.guild, args, message.id)
            elif needs_args:
                await method(message.channel, message.guild, args)
            else:
                await method(message.channel, message.guild)
        return True

    async def _delete_quietly(self, message):
        try: await message.delete()
        except: pass

class KnowledgeIngestionHandler:
    async def handle(self, message: discord.Message, bot: 'MindSpaceBot') -> bool:
        if "http://" in message.content or "https://" in message.content:
            await bot.handle_url_ingest(message)
            return True

        if message.attachments:
            await bot.handle_attachment_ingest(message)
            return True

        return False

class PassiveDialogueHandler:
    async def handle(self, message: discord.Message, bot: 'MindSpaceBot') -> bool:
        channel_name = message.channel.name
        proposal_ids = []

        async def on_propose(c, p, i, r):
            res = await bot.handle_propose_update(c, p, i, r)
            if not res.startswith("Error") and len(res) <= 8:
                proposal_ids.append(res)
                return f"Proposal for `{p}` generated and queued for your review."
            return res

        status_msg = await message.channel.send("🧠 **Thinking...**")

        async def on_progress(text: str):
            try: await status_msg.edit(content=f"🧠 **Thinking...**\n{text}")
            except: pass

        # Delegate tool wrapping to bot
        available_tools = bot.tools.get_tools(channel_name, on_propose_update=on_propose)
        wrapped_tools = [bot.wrap_tool_with_progress(t, on_progress) for t in available_tools]
        
        mcp_sessions = bot.wrap_mcp_with_progress(on_progress) if bot.mcp_pool else None

        try:
            reply = await bot.agent.engage_dialogue(
                message.content,
                channel_name,
                history=bot.kb.get_history(channel_name),
                tools=wrapped_tools,
                mcp_sessions=mcp_sessions,
            )
        finally:
            if bot.mcp_pool:
                bot.unwrap_mcp()

        try: await status_msg.delete()
        except: pass

        bot.kb.append_history(channel_name, message.author.display_name, message.content)
        bot.kb.append_history(channel_name, "assistant", reply)
        await bot.send_message_safe(message.channel, reply)

        for pid in proposal_ids:
            await bot._send_proposal(message.channel, pid)
        return True
