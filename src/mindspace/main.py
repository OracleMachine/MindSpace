import os
import sys
import select
import asyncio
import discord
from mindspace.core import config
from mindspace.core.logger import logger
from mindspace.bot.client import MindSpaceBot

REINDEX_PROMPT_TIMEOUT = 5

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

def _prompt_reindex_confirmation(timeout: int = REINDEX_PROMPT_TIMEOUT) -> bool:
    """Ask the operator to opt in to a full KB re-index. Default on timeout / non-TTY is skip,
    since embedding API calls are billed per token."""
    if not sys.stdin.isatty():
        logger.info("Startup Indexing: no TTY attached; skipping re-index.")
        return False

    print(f"\n[Startup] Press ENTER to run full KB re-index (otherwise skip to save tokens).", flush=True)
    for remaining in range(timeout, 0, -1):
        print(f"  ...skipping in {remaining}s ", end="\r", flush=True)
        ready, _, _ = select.select([sys.stdin], [], [], 1)
        if ready:
            sys.stdin.readline()
            print("\n  -> confirmed; proceeding with re-index.", flush=True)
            return True
    print("\n  -> timeout; skipping re-index.", flush=True)
    return False

def _startup_indexing():
    """Perform initial indexing, gated on operator confirmation."""
    if not _prompt_reindex_confirmation():
        logger.info("Startup Indexing: skipped.")
        return

    logger.info("Startup Indexing: initializing...")
    from mindspace.knowledgebase.viking import VikingContextManager
    from mindspace.knowledgebase.pageindex import PageIndexManager

    viking = VikingContextManager(config.Paths.CHANNELS)
    pi = PageIndexManager()
    viking.rebuild_index()
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
