"""MCP integration bridge.

Two entry points, one config source (`config.MCP.SERVERS`):

- `sync_cli_settings()` renders `mcpServers` into the Gemini CLI's
  settings.json so the command brain (!research, !omni)
  picks them up via the rerooted GEMINI_CLI_HOME.

- `MCPSessionPool` opens live `mcp.ClientSession`s over streamable HTTP
  and exposes them as `.sessions` — passed directly into
  `google.genai` tools so the dialogue brain (passive chat) can invoke
  MCP tools during automatic function calling.
"""
import asyncio
import json
import os
from contextlib import AsyncExitStack
from typing import Optional

from mindspace.core import config
from mindspace.core.logger import logger


def sync_cli_settings() -> None:
    if not config.MCP.SERVERS:
        logger.debug("MCP: no servers configured, skipping settings.json sync")
        return

    settings_path = os.path.join(config.Paths.GEMINI_CLI_HOME, ".gemini", "settings.json")
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)

    existing: dict = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r") as f:
                existing = json.load(f)
        except Exception as e:
            logger.warning(f"MCP: existing {settings_path} unparseable, rewriting: {e}")

    existing["mcpServers"] = config.MCP.SERVERS

    with open(settings_path, "w") as f:
        json.dump(existing, f, indent=2)

    logger.info(f"MCP: synced {len(config.MCP.SERVERS)} server(s) into {settings_path}")


class MCPSessionPool:
    """Live pool of `mcp.ClientSession`s, opened over streamable HTTP.

    Lifetime is tied to the bot: `connect()` in setup_hook, `close()` in
    the bot's `close()`. `sessions` is a dict {name: ClientSession}.
    """

    def __init__(self, servers: dict):
        self.servers = servers or {}
        self.sessions: dict = {}
        # Per-server cached tool list from list_tools() at connect time.
        # {server_name: [tool_name, ...]}. Consumed by preflight for reporting.
        self.tool_lists: dict = {}
        self._exit_stack: Optional[AsyncExitStack] = None

    async def connect(self) -> None:
        if not self.servers:
            logger.debug("MCP: no servers configured, pool stays empty")
            return
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError:
            logger.warning("MCP: 'mcp' package not installed; pool disabled")
            return

        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()

        for name, cfg in self.servers.items():
            url = cfg.get("url")
            if not url:
                logger.warning(f"MCP: server '{name}' has no url, skipping")
                continue
            headers = cfg.get("headers", {})
            try:
                transport = await self._exit_stack.enter_async_context(
                    streamablehttp_client(url, headers=headers)
                )
                read_stream, write_stream, _ = transport
                session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                self.sessions[name] = session
                try:
                    tools_resp = await session.list_tools()
                    tool_names = [t.name for t in tools_resp.tools]
                    self.tool_lists[name] = tool_names
                    logger.info(f"MCP: connected → {name} ({url}) — {len(tool_names)} tool(s)")
                except Exception as e:
                    self.tool_lists[name] = []
                    logger.warning(f"MCP: connected {name} but list_tools failed: {e!r}")
            except (Exception, asyncio.CancelledError) as e:
                # CancelledError propagates from anyio cancel scopes when the
                # underlying HTTP stream errors out (e.g., connection refused).
                # Treat it like any other connect failure — isolate to this
                # server so the rest of the pool still comes up.
                logger.warning(f"MCP: failed to connect {name}: {e!r}")

    async def close(self) -> None:
        if self._exit_stack is not None:
            try:
                await self._exit_stack.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"MCP: error closing session pool: {e}")
        self._exit_stack = None
        self.sessions = {}
        self.tool_lists = {}
