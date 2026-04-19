import os
import asyncio
import discord
from mindspace.core import config
from mindspace.core.logger import logger
from mindspace.bot.client import MindSpaceBot

def _preflight_check():
    """Verify dependencies and API keys."""
    logger.info("Preflight: checking dependencies...")
    try:
        from pageindex import PageIndexClient
        import openviking as ov
        import git
    except ImportError as e:
        raise RuntimeError(f"Missing dependency: {e}")

    try:
        logger.info("Preflight: validating PageIndex API key...")
        pi_client = PageIndexClient(api_key=config.Auth.PAGEINDEX_API_KEY)
        pi_client.list_documents(limit=1)

        logger.info("Preflight: validating OpenViking...")
        os.environ.setdefault("OPENVIKING_CONFIG_FILE", config.Paths.VIKING_CONF)
        ov_client = ov.SyncOpenViking(path=config.Paths.VIKING_DATA)
        ov_client.initialize()
        ov_client.find("preflight check", limit=1)
        ov_client.close()
    except Exception as e:
        raise RuntimeError(f"API key validation failed: {e}")

    if config.MCP.SERVERS:
        logger.info(f"Preflight: probing {len(config.MCP.SERVERS)} MCP server(s)...")
        from mindspace.agent.mcp import MCPSessionPool
        async def _probe():
            pool = MCPSessionPool(config.MCP.SERVERS)
            await pool.connect()
            await pool.close()
        asyncio.run(_probe())

def _startup_indexing():
    """Perform initial indexing. By default the viking sync runs file-only
    (no summary regeneration, 0 LLM tokens) — summaries are refreshed lazily
    when files actually change via `index_file` (record_thought, proposals,
    active commands), or explicitly via the `/sync` Discord command.
    Set `MINDSPACE_FORCE_REINDEX=1` in the environment to re-seed everything
    from scratch including summaries."""
    force = os.environ.get("MINDSPACE_FORCE_REINDEX") == "1"
    if force:
        logger.info("Startup Indexing: MINDSPACE_FORCE_REINDEX=1 — forcing full rebuild.")
    logger.info("Startup Indexing: initializing...")
    from mindspace.knowledgebase.viking import VikingContextManager
    from mindspace.knowledgebase.pageindex import PageIndexManager

    viking = VikingContextManager(config.Paths.CHANNELS)
    pi = PageIndexManager()
    viking.rebuild_index(force=force)
    pi.rebuild_index(config.Paths.CHANNELS)
    viking.close()
    logger.info("Startup Indexing: complete.")

def main():
    logger.info("========== Launching MindSpace ==========")
    
    required = {
        "DISCORD_TOKEN": config.Auth.DISCORD_TOKEN,
        "GEMINI_API_KEY": config.Auth.GEMINI_API_KEY,
        "PAGEINDEX_API_KEY": config.Auth.PAGEINDEX_API_KEY,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        return

    try:
        _preflight_check()
        _startup_indexing()
    except Exception as e:
        logger.error(f"Startup failed: {e}")
        return

    intents = discord.Intents.default()
    intents.message_content = True
    bot = MindSpaceBot(intents=intents)
    bot.run(config.Auth.DISCORD_TOKEN)

if __name__ == "__main__":
    main()
