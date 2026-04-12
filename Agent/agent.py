import os
import re
import asyncio
import subprocess
import json
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
import config
from abc import ABC, abstractmethod
from logger import logger

class LLMBrain(ABC):
    @abstractmethod
    def run_command(self, instruction: str, context: str = None) -> str:
        pass

    @abstractmethod
    async def run_command_async(self, instruction: str, context: str = None) -> str:
        pass

    @abstractmethod
    def chat(self, system_ctx: str, history: list, message: str, tools: list = None) -> str:
        """Multi-turn dialogue with persistent system context and conversation history."""
        pass

    async def achat(self, system_ctx: str, history: list, message: str,
                    tools: list = None, mcp_sessions: list = None) -> str:
        """Async chat entry point. Default: wrap sync `chat` via asyncio.to_thread.
        Subclasses override this for native async/MCP support.
        """
        if mcp_sessions:
            logger.warning(
                f"{type(self).__name__}: mcp_sessions provided but brain has no "
                "native MCP support; ignoring"
            )
        return await asyncio.to_thread(self.chat, system_ctx, history, message, tools)

class GoogleGenAIBrain(LLMBrain):
    def __init__(self, model=config.GEMINI_SDK_MODEL):
        self.model = model
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)

    def close(self):
        self.client.close()

    def run_command(self, instruction: str, context: str = None) -> str:
        full_prompt = f"System Context:\n{context}\n\nUser Message: {instruction}" if context else instruction
        logger.debug(f"GoogleGenAI.run_command prompt ({len(full_prompt)} chars):\n{full_prompt}")
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=full_prompt
            )
            return response.text.strip()
        except Exception as e:
            raise Exception(f"Google GenAI SDK Error: {str(e)}")

    async def run_command_async(self, instruction: str, context: str = None) -> str:
        full_prompt = f"System Context:\n{context}\n\nUser Message: {instruction}" if context else instruction
        logger.debug(f"GoogleGenAI.run_command_async prompt ({len(full_prompt)} chars):\n{full_prompt}")
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=full_prompt
            )
            return response.text.strip()
        except Exception as e:
            raise Exception(f"Google GenAI SDK Async Error: {str(e)}")

    def chat(self, system_ctx: str, history: list, message: str, tools: list = None) -> str:
        """Multi-turn call: system context via system_instruction, history as prior turns."""
        logger.debug(
            f"GoogleGenAI.chat system_ctx ({len(system_ctx or '')} chars):\n{system_ctx}\n"
            f"--- history ({len(history)} turns) ---\n"
            + "\n".join(f"[{r}] {c}" for r, c in history)
            + f"\n--- message ---\n{message}"
        )
        contents = []
        for role, content in history:
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": content}]})
        contents.append({"role": "user", "parts": [{"text": message}]})
        try:
            kwargs = {}
            if system_ctx:
                kwargs["system_instruction"] = system_ctx
            if tools:
                kwargs["tools"] = tools
                kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=False)

            cfg = types.GenerateContentConfig(**kwargs) if kwargs else None
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=cfg
            )
            return response.text.strip()
        except Exception as e:
            raise Exception(f"Google GenAI SDK Error: {str(e)}")

    async def achat(self, system_ctx: str, history: list, message: str,
                    tools: list = None, mcp_sessions: list = None, on_progress=None) -> str:
        """Async multi-turn call. MCP sessions are passed straight through —
        google-genai handles tool discovery + dispatch on ClientSession objects via AFC.
        `on_progress` is not used here; progress is emitted by tool wrappers at the call site."""
        logger.debug(
            f"GoogleGenAI.achat system_ctx ({len(system_ctx or '')} chars):\n{system_ctx}\n"
            f"--- history ({len(history)} turns) ---\n"
            + "\n".join(f"[{r}] {c}" for r, c in history)
            + f"\n--- message ---\n{message}"
        )
        contents = []
        for role, content in history:
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": content}]})
        contents.append({"role": "user", "parts": [{"text": message}]})

        combined_tools = list(tools or [])
        if mcp_sessions:
            # mcp_sessions is {name: ClientSession}; AFC consumes the ClientSession objects directly.
            combined_tools.extend(mcp_sessions.values())

        try:
            kwargs = {}
            if system_ctx:
                kwargs["system_instruction"] = system_ctx
            if combined_tools:
                kwargs["tools"] = combined_tools
                kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=False)

            cfg = types.GenerateContentConfig(**kwargs) if kwargs else None
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=cfg
            )
            return response.text.strip()
        except Exception as e:
            raise Exception(f"Google GenAI SDK Error: {str(e)}")

class GeminiCLIBrain(LLMBrain):
    """
    Delegates to the Gemini CLI (gemini -y).
    Capabilities beyond the direct API: web search, file I/O, multi-step agentic loops.
    Used for all commands (!organize, !consolidate, !research, !omni).
    """

    def __init__(self, yolo: bool = True, model: str = None):
        self.yolo = yolo
        self.model = model or config.GEMINI_CLI_MODEL
        self.env = {**os.environ, "GEMINI_CLI_HOME": config.GEMINI_CLI_HOME_DIR}

    def build_args(self) -> list[str]:
        args = ["gemini", "-y", "-o", "stream-json"]
        if self.model:
            args.extend(["-m", self.model])
        return args

    def _build_prompt(self, instruction: str, context: str = None,
                      system_ctx: str = None, history: list = None) -> str:
        parts = []
        if system_ctx:
            parts.append(f"[System]\n{system_ctx}")
        if history:
            for role, content in history:
                parts.append(f"[{role}]\n{content}")
        if context:
            parts.append(f"[Context]\n{context}")
        parts.append(f"[User]\n{instruction}")
        return "\n\n".join(parts)

    def run_command(self, instruction: str, context: str = None) -> str:
        prompt = self._build_prompt(instruction, context)
        logger.debug(f"GeminiCLI.run_command prompt ({len(prompt)} chars):\n{prompt}")
        result = subprocess.run(
            self.build_args(),
            input=prompt,
            capture_output=True, text=True, timeout=300,
            env=self.env,
        )
        parts = []
        for line in result.stdout.splitlines():
            try:
                data = json.loads(line)
                if data.get("role") == "assistant":
                    parts.append(data.get("content", ""))
            except: continue
        return "".join(parts).strip()

    async def run_command_async(self, instruction: str, context: str = None) -> str:
        prompt = self._build_prompt(instruction, context)
        logger.debug(f"GeminiCLI.run_command_async prompt ({len(prompt)} chars):\n{prompt}")
        
        proc = await asyncio.create_subprocess_exec(
            *self.build_args(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )
        
        stdout, stderr = await proc.communicate(input=prompt.encode())
        
        if proc.returncode != 0:
            logger.warning(f"GeminiCLI.run_command_async failed with code {proc.returncode}: {stderr.decode()}")

        parts = []
        for line in stdout.decode().splitlines():
            try:
                data = json.loads(line)
                if data.get("role") == "assistant":
                    parts.append(data.get("content", ""))
            except: continue
        return "".join(parts).strip()

    async def stream(self, prompt: str, cwd: str) -> "CliStream":
        args = self.build_args()
        logger.debug(
            f"GeminiCLI.stream args={args} cwd={cwd} prompt ({len(prompt)} chars):\n{prompt}"
        )
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=self.env,
        )
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()
        return CliStream(proc)

    def chat(self, system_ctx: str, history: list, message: str, tools: list = None) -> str:
        # tools ignored — Gemini CLI manages its own tool loop internally
        prompt = self._build_prompt(message, system_ctx=system_ctx, history=history)
        return self.run_command(prompt)

class CliStream:
    def __init__(self, proc):
        self._proc = proc
        self.returncode: int | None = None
        self.result: dict | None = None
        self._parts: list[str] = []

    def get_full_response(self) -> str:
        return "".join(self._parts).strip()

    def __aiter__(self):
        return self._iter()

    async def _read_stderr(self):
        try:
            async for raw in self._proc.stderr:
                line = raw.decode(errors="replace").strip()
                if line:
                    logger.debug(f"CLI STDERR | {line}")
        except Exception:
            pass

    async def _iter(self):
        stderr_task = asyncio.create_task(self._read_stderr())
        try:
            async for raw in self._proc.stdout:
                try:
                    data = json.loads(raw.decode(errors="replace"))
                    if data.get("role") == "assistant":
                        content = data.get("content", "")
                        if content:
                            self._parts.append(content)
                            yield content
                    if data.get("type") == "result":
                        self.result = data
                except: continue
        finally:
            self.returncode = await self._proc.wait()
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass

class MindSpaceAgent:
    def __init__(self, brain_type=None):
        # Always use the native Google GenAI SDK for dialogue; runtime fallback disabled.
        # brain_type is ignored; hardcoded to GoogleGenAIBrain.
        logger.info(f"🧠 MindSpaceAgent: Dialogue brain → Google GenAI SDK (Model: {config.GEMINI_SDK_MODEL})")
        self.brain = GoogleGenAIBrain()

        # Command brain: !organize, !consolidate, !research, !omni
        logger.info(f"🖥️  MindSpaceAgent: Command brain → Gemini CLI (YOLO mode, model={config.GEMINI_CLI_MODEL or 'default'})")
        self.cli_brain = GeminiCLIBrain()

    async def run_command(self, instruction: str, context: str = None) -> str:
        return await self.cli_brain.run_command(instruction, context)

    def close(self):
        close_fn = getattr(self.brain, 'close', None)
        if callable(close_fn):
            close_fn()

    async def engage_dialogue(self, user_message, channel_name, history: str = "", tools: list = None, mcp_sessions: list = None, on_progress=None):
        system_parts = [f"You are a knowledge agent in Discord channel #{channel_name}."]
        if history:
            system_parts.append(
                f"--- Recent Conversation History ---\n"
                f"(Messages labeled 'assistant' are from this bot. "
                f"All other names are Discord display names of human users.)\n\n"
                f"{history}"
            )

        system_parts.append(
            "You do NOT have any pre-loaded knowledge about this channel. "
            "All channel-specific information — prior insights, news digests, research notes, "
            "stream-of-consciousness entries — is stored in the knowledge base and MUST be "
            "retrieved via tools.\n\n"
            "When the user asks a factual question, references prior discussion, or requests "
            "information about topics in this channel, ALWAYS call `search_channel_knowledge_base` "
            "first. Use `search_global_knowledge_base` for cross-channel queries. "
            "Use `list_channel_files` to see what's stored.\n\n"
            "If the user's message contains a valuable insight, analysis, or conclusion, you MUST "
            "persist it to the knowledge base. Choose the appropriate tool:\n\n"
            "1. **`record_thought(summary)`**: Use this for general observations, interesting data "
            "points, or transitory thoughts that should be added to the channel's running log. "
            "This action is SILENT and background-only. Do NOT mention it in your reply.\n\n"
            "2. **`propose_update(path, instruction, rationale)`**: Use this when an insight "
            "belongs in a structured file — either updating an existing one (e.g., a research "
            "report, a faction model) or creating a new document. This will generate a Git-style "
            "diff for the user's approval. This is the PREFERRED way to maintain high-quality "
            "structured knowledge.\n\n"
            "**CRITICAL RULES:**\n"
            "- You MUST execute the tool call itself. Do NOT output '錄入建議' (Entry Suggestion) "
            "or 'record_thought(...)' as text in your reply. The tool call is the ACTION.\n"
            "- Your text response to the user should be a natural continuation of the "
            "conversation. It should NOT mention that you are recording a thought or "
            "proposing an update, as the UI handles those notifications automatically.\n\n"
            "**THOUGHT PARTNER ROLE:**\n"
            "You are a high-level strategic advisor, not just a passive recorder. In your "
            "dialogue response, do not merely agree with the user's latest insight. "
            "Actively challenge their logic: provide a steel-manned counter-argument, "
            "suggest critical blindspots in their current model, and identify missing "
            "variables that might invalidate their hypothesis. Your value lies in providing "
            "constructive friction to deepen the research.\n\n"
            "Reply naturally to the user."
        )
        system_ctx = "\n\n".join(system_parts)

        response = await self.brain.achat(system_ctx, [], user_message, tools=tools, mcp_sessions=mcp_sessions, on_progress=on_progress)
        return response.strip()

    async def analyze_file(self, file_path: str, pageindex) -> tuple:
        ext = os.path.splitext(file_path)[1].lower()
        channel_name = os.path.basename(os.path.dirname(file_path))
        if ext == ".pdf":
            try:
                doc_id = await asyncio.to_thread(pageindex.index_document, file_path, channel_name)
                tree = await asyncio.to_thread(pageindex.get_tree, doc_id)
                prompt = (
                    f"This PDF has been indexed. Its document tree structure:\n\n{tree}\n\n"
                    f"Provide a one-sentence description of the document's content and purpose."
                )
                return doc_id, await self.brain.run_command_async(prompt)
            except Exception:
                pass
        try:
            with open(file_path, "r", errors="ignore") as f:
                raw = f.read(5000)
        except Exception:
            raw = ""
        return None, await self.brain.run_command_async(f"Analyze this file content and summarize it:\n\n{raw}")

    async def process_url(self, url, channel_name):
        import httpx
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                headers = {'User-Agent': 'Mozilla/5.0'}
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, 'html.parser')
                for script_or_style in soup(["script", "style"]):
                    script_or_style.decompose()
                text = soup.get_text(separator='\n')
                prompt = (
                    f"Extract the main article content from this raw webpage text:\n\n{text}\n\n"
                    f"Format it as a clean Markdown file with a human-readable title. "
                    f"Include the original URL ({url}) at the top."
                )
                return await self.brain.run_command_async(prompt)
        except Exception as e:
            return f"Error fetching URL: {str(e)}"

    async def generate_commit_message(self, action_description):
        prompt = f"Generate a one-sentence Git commit message explaining the intent behind this action: {action_description}"
        return await self.brain.run_command_async(prompt)
