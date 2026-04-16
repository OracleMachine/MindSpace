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
        if tools:
            kwargs["tools"] = tools
            kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=False)
        return types.GenerateContentConfig(**kwargs) if kwargs else None

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

class MindSpaceAgent:
    def __init__(self, brain_type=None):
        # Always use the native Google GenAI SDK for dialogue; runtime fallback disabled.
        # brain_type is ignored; hardcoded to GoogleGenAIBrain.
        logger.info(f"🧠 MindSpaceAgent: Dialogue brain → Google GenAI SDK (Model: {config.Brains.GEMINI_SDK_MODEL})")
        self.brain = GoogleGenAIBrain()

        # Command brain: !organize, !consolidate, !research, !omni
        logger.info(f"🖥️  MindSpaceAgent: Command brain → Gemini CLI (YOLO mode, model={config.Brains.GEMINI_CLI_MODEL or 'default'})")
        self.cli_brain = GeminiCLIBrain()
        self.kb = None

    def set_kb(self, kb):
        """Attach the knowledge base manager for systematic context injection."""
        self.kb = kb

    def _inject_view(self, text: str, channel_name: str) -> str:
        """Systematically prepend view.md context if available."""
        if not self.kb or not channel_name:
            return text
        view = self.kb.get_view(channel_name)
        if not view:
            return text
        return f"--- Static Mindset (view.md) ---\n{view}\n\n{text}"

    async def run_command(self, instruction: str, context: str = None, channel_name: str = None) -> str:
        """Run a command via the CLI brain, with optional systematic view injection."""
        injected_context = self._inject_view(context or "", channel_name) if channel_name else context
        return await self.cli_brain.run_command_async(instruction, injected_context)

    async def stream(self, prompt: str, cwd: str, channel_name: str = None) -> "CliStream":
        """Stream a CLI command, with optional systematic view injection."""
        injected_prompt = self._inject_view(prompt, channel_name) if channel_name else prompt
        return await self.cli_brain.stream(injected_prompt, cwd)

    def close(self):
        close_fn = getattr(self.brain, 'close', None)
        if callable(close_fn):
            close_fn()

    async def engage_dialogue(self, user_message, channel_name, history: str = "", tools: list = None, mcp_sessions: list = None):
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
            "This action is COMPLETELY SILENT and background-only. Do NOT mention it in your reply. "
            "The user will NOT be notified.\n\n"
            "2. **`propose_update(path, instruction, rationale)`**: Use this when an insight "
            "belongs in a structured file (existing or new). This DOES NOT update the KB immediately. "
            "It generates a diff that the user MUST manually approve. "
            "Since the user sees the proposal UI, you MUST NOT mention that you are proposing an "
            "update or that you have 'updated' the KB. If you say you 'updated' the KB when it "
            "requires approval, you are LYING to the user.\n\n"
            "**CRITICAL RULES:**\n"
            "- You MUST execute the tool call itself. Do NOT output '錄入建議' or 'record_thought(...)' as text.\n"
            "- Your text response MUST be a natural continuation of the conversation.\n"
            "- NEVER mention tool execution, thought recording, or proposed updates in your reply. "
            "The UI handles all notifications. If your reply contains 'I have recorded...', 'I have proposed...', "
            "'Updating KB...', or similar, you have FAILED the task.\n\n"
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
        injected_ctx = self._inject_view(system_ctx, channel_name)

        response = await self.brain.achat(injected_ctx, [], user_message, tools=tools, mcp_sessions=mcp_sessions)
        return response.strip()

    _TEXT_EXTS = {
        ".md", ".markdown", ".txt", ".rst", ".json", ".yaml", ".yml",
        ".toml", ".csv", ".tsv", ".log", ".py", ".js", ".ts",
    }

    @classmethod
    def is_text_ext(cls, ext: str) -> bool:
        """True if the extension names a text-ish file we can preview."""
        return ext.lower() in cls._TEXT_EXTS

    @staticmethod
    def _read_text_snippet(file_path: str, max_chars: int = 5000) -> str:
        """Best-effort read of the first N chars of a text-ish file. Empty on failure."""
        try:
            with open(file_path, "r", errors="ignore") as f:
                return f.read(max_chars)
        except Exception:
            return ""

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
        raw = self._read_text_snippet(file_path)
        return None, await self.brain.run_command_async(f"Analyze this file content and summarize it:\n\n{raw}")

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Parse a JSON object out of an LLM response, tolerating ``` fences
        and trailing prose. Returns {} on failure."""
        if not text:
            return {}
        text = text.strip()
        # Strip markdown code fences if present.
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        # Grab the first {...} block so stray prose around JSON is ignored.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return {}
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return {}

    async def route_file(self, filename: str, snippet: str, tree_listing: str,
                          channel_name: str, advice: str = "") -> tuple[str, str]:
        """Decide the target subfolder and renamed filename for a dropped file.

        Returns (subfolder, new_filename). The subfolder is relative to the
        channel folder (empty string means channel root). Falls back to
        ("", filename) on any LLM / parse error — the caller still sanitizes.
        `advice` is optional free-text steering from the user (non-empty only
        when they @mentioned the bot with a non-md file).
        """
        ext = os.path.splitext(filename)[1].lower()
        advice_block = f"USER ADVICE (steering from the @mention): {advice}\n\n" if advice else ""
        prompt = (
            f"You are organizing a file dropped into the Discord channel #{channel_name} "
            f"of a knowledge base. Pick the correct subfolder within the channel folder "
            f"and a content-based filename.\n\n"
            f"{advice_block}"
            f"ORIGINAL FILENAME: {filename}\n"
            f"FILE EXTENSION: {ext or '(none)'}\n\n"
            f"CHANNEL FOLDER CONTENTS (relative paths, '/' = directory):\n"
            f"{tree_listing}\n\n"
            f"FILE CONTENT PREVIEW (may be empty for binary files):\n"
            f"---\n{snippet or '(no preview available — use filename as hint)'}\n---\n\n"
            f"RULES:\n"
            f"- subfolder is RELATIVE to the channel folder. Use '' (empty) for channel root.\n"
            f"- Prefer reusing an existing subfolder from the listing over inventing a new one.\n"
            f"- filename MUST preserve the original extension ({ext or 'any'}).\n"
            f"- filename MUST NOT be 'view.md'.\n"
            f"- Follow the TYPE-DATE-SUBJECT convention where possible. Use kebab-case. "
            f"Date format: YYYY-MM-DD. Example: NOTE-2026-04-14-grpo-vs-ppo.md\n"
            f"- Do NOT include path separators in the filename.\n"
            f"- If USER ADVICE is present, let it override content-based guesses.\n\n"
            f"Respond with STRICT JSON only — no prose, no code fences:\n"
            f'{{"subfolder": "...", "filename": "..."}}'
        )
        try:
            raw = await self.brain.run_command_async(prompt)
            data = self._extract_json(raw)
            subfolder = str(data.get("subfolder", "")).strip()
            new_name = str(data.get("filename", "")).strip()
            if not new_name:
                return "", filename
            return subfolder, new_name
        except Exception as e:
            logger.warning(f"route_file: LLM/parse failure ({e}); falling back to original name")
            return "", filename

    async def plan_file_proposal(self, draft_content: str, advice: str,
                                  kb_context: str, tree_listing: str,
                                  channel_name: str) -> dict:
        """First stage of Path B — decide whether the dropped .md should update
        an existing KB file or land as a new one. Returns a dict with keys
        `mode` ("new"|"update"), `target_rel_path`, `rationale`. Falls back to
        a "new" verdict with a content-derived path on any error.
        `advice` may be empty: the user @mentioned the bot without extra text,
        requesting a review-before-save without specific steering."""
        if advice:
            advice_block = f"USER ADVICE: {advice}\n\n"
        else:
            advice_block = (
                "USER ADVICE: (none — the user @mentioned the bot without extra text, "
                "requesting a reviewed save. Infer intent from the draft and the channel "
                "structure.)\n\n"
            )
        prompt = (
            f"A user dropped a markdown draft into Discord channel #{channel_name} and asked "
            f"for a reviewed save. Decide whether to MERGE the draft into an existing KB "
            f"file, or save it as a NEW file.\n\n"
            f"{advice_block}"
            f"DROPPED DRAFT (first section):\n---\n{draft_content[:4000]}\n---\n\n"
            f"CHANNEL KB SEMANTIC CONTEXT (what's already indexed):\n{kb_context or '(empty)'}\n\n"
            f"CHANNEL FOLDER LAYOUT:\n{tree_listing}\n\n"
            f"RULES:\n"
            f"- target_rel_path MUST NOT be 'view.md'.\n"
            f"- Pick mode='update' ONLY if there is a clearly-matching existing file whose "
            f"topic overlaps the draft and where merging makes editorial sense.\n"
            f"- Otherwise pick mode='new'.\n"
            f"- target_rel_path is relative to the channel folder, must end in .md, and for "
            f"'new' should follow TYPE-DATE-SUBJECT kebab-case (e.g. NOTE-2026-04-14-topic.md) "
            f"placed in an appropriate subfolder from the layout (or channel root if none fit).\n"
            f"- rationale is one short sentence, shown to the user.\n\n"
            f"Respond with STRICT JSON only — no prose, no code fences:\n"
            f'{{"mode": "new|update", "target_rel_path": "...", "rationale": "..."}}'
        )
        try:
            raw = await self.brain.run_command_async(prompt)
            data = self._extract_json(raw)
            mode = data.get("mode", "new")
            if mode not in ("new", "update"):
                mode = "new"
            path = str(data.get("target_rel_path", "")).strip()
            rationale = str(data.get("rationale", "")).strip() or "User-submitted draft transformed per instruction."
            if not path:
                return {"mode": "new", "target_rel_path": "", "rationale": rationale}
            return {"mode": mode, "target_rel_path": path, "rationale": rationale}
        except Exception as e:
            logger.warning(f"plan_file_proposal: LLM/parse failure ({e})")
            return {"mode": "new", "target_rel_path": "", "rationale": "User-submitted draft."}

    async def merge_file_proposal(self, draft_content: str, existing_content: str,
                                    advice: str, mode: str) -> str:
        """Second stage of Path B — produce the final markdown. For `mode='update'`,
        the prompt allows the model to abort with sentinel `NEW_FILE_INSTEAD\\n...`
        if the target turns out to be a poor fit after seeing the fresh content.
        Caller handles the sentinel. `advice` may be empty."""
        if advice:
            advice_block = f"USER ADVICE: {advice}\n\n"
        else:
            advice_block = (
                "USER ADVICE: (none — treat the draft as-is, clean it up lightly, and "
                "save/merge without aggressive rewriting.)\n\n"
            )
        if mode == "update":
            prompt = (
                f"You are merging a user-submitted markdown draft into an existing Knowledge "
                f"Base file.\n\n"
                f"{advice_block}"
                f"DROPPED DRAFT:\n---\n{draft_content}\n---\n\n"
                f"EXISTING TARGET FILE:\n---\n{existing_content}\n---\n\n"
                f"TASK:\n"
                f"- Produce the complete, final markdown for the target file after applying the merge.\n"
                f"- Preserve the existing tone, structure, and headings where sensible.\n"
                f"- If after reading BOTH the draft AND the existing file you believe the draft "
                f"does NOT belong in this target (topic mismatch, would corrupt structure), "
                f"return the literal first line `NEW_FILE_INSTEAD` followed by a newline and then "
                f"the cleaned-up draft — the caller will save it as a new file instead.\n"
                f"- Otherwise, output ONLY the merged markdown document. No commentary."
            )
        else:
            prompt = (
                f"You are saving a user-submitted markdown draft as a new Knowledge Base file.\n\n"
                f"{advice_block}"
                f"DROPPED DRAFT:\n---\n{draft_content}\n---\n\n"
                f"TASK:\n"
                f"- Produce the complete, final markdown for the new file.\n"
                f"- Keep a clear H1 title. Use standard markdown conventions.\n"
                f"- Output ONLY the complete markdown document. No commentary."
            )
        return await self.brain.run_command_async(prompt)

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
