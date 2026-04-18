import os
import asyncio
import subprocess
import json
from abc import ABC, abstractmethod
from google import genai
from google.genai import types

from mindspace.core import config
from mindspace.core.logger import logger

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
    def __init__(self, model=config.Brains.GEMINI_SDK_MODEL):
        self.model = model
        self.client = genai.Client(api_key=config.Auth.GEMINI_API_KEY)

    def close(self):
        self.client.close()

    @staticmethod
    def _build_run_prompt(instruction: str, context: str = None) -> str:
        return f"System Context:\n{context}\n\nUser Message: {instruction}" if context else instruction

    @staticmethod
    def _build_contents(history: list, message: str) -> list:
        contents = []
        for role, content in history:
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": content}]})
        contents.append({"role": "user", "parts": [{"text": message}]})
        return contents

    @staticmethod
    def _build_config(system_ctx: str, tools: list):
        kwargs = {}
        if system_ctx:
            kwargs["system_instruction"] = system_ctx
        
        # Inject Google Search tool if enabled in config
        if config.Brains.ENABLE_GOOGLE_SEARCH:
            merged_tools = list(tools) if tools else []
            merged_tools.append({"google_search": {}})
            kwargs["tools"] = merged_tools
            kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=False)
            # Required when mixing built-in server-side tools (google_search) with function calls.
            kwargs["tool_config"] = types.ToolConfig(include_server_side_tool_invocations=True)
        elif tools:
            kwargs["tools"] = tools
            kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=False)

        return types.GenerateContentConfig(**kwargs)

    def run_command(self, instruction: str, context: str = None) -> str:
        full_prompt = self._build_run_prompt(instruction, context)
        logger.debug(f"GoogleGenAI.run_command prompt ({len(full_prompt)} chars):\n{full_prompt}")
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=full_prompt
            )
            return (response.text or "").strip()
        except Exception as e:
            raise Exception(f"Google GenAI SDK Error: {str(e)}")

    async def run_command_async(self, instruction: str, context: str = None) -> str:
        full_prompt = self._build_run_prompt(instruction, context)
        logger.debug(f"GoogleGenAI.run_command_async prompt ({len(full_prompt)} chars):\n{full_prompt}")
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=full_prompt
            )
            return (response.text or "").strip()
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
        contents = self._build_contents(history, message)
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=self._build_config(system_ctx, tools),
            )
            return (response.text or "").strip()
        except Exception as e:
            raise Exception(f"Google GenAI SDK Error: {str(e)}")

    async def achat(self, system_ctx: str, history: list, message: str,
                    tools: list = None, mcp_sessions: list = None) -> str:
        """Async multi-turn call. MCP sessions are passed straight through —
        google-genai handles tool discovery + dispatch on ClientSession objects via AFC."""
        logger.debug(
            f"GoogleGenAI.achat system_ctx ({len(system_ctx or '')} chars):\n{system_ctx}\n"
            f"--- history ({len(history)} turns) ---\n"
            + "\n".join(f"[{r}] {c}" for r, c in history)
            + f"\n--- message ---\n{message}"
        )
        contents = self._build_contents(history, message)

        combined_tools = list(tools or [])
        if mcp_sessions:
            # mcp_sessions is {name: ClientSession}; AFC consumes the ClientSession objects directly.
            combined_tools.extend(mcp_sessions.values())

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=self._build_config(system_ctx, combined_tools),
            )
            return (response.text or "").strip()
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
        self.model = model or config.Brains.GEMINI_CLI_MODEL
        self.env = {**os.environ, "GEMINI_CLI_HOME": config.Paths.GEMINI_CLI_HOME}

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
