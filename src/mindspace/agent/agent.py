import re
import json
import asyncio
import os
from bs4 import BeautifulSoup

from mindspace.core import config
from mindspace.core.logger import logger
from mindspace.agent.brain import GoogleGenAIBrain, GeminiCLIBrain
from mindspace.agent import prompts

class MindSpaceAgent:
    def __init__(self, brain_type=None):
        logger.info(f"🧠 MindSpaceAgent: Dialogue brain → Google GenAI SDK (Model: {config.Brains.GEMINI_SDK_MODEL})")
        self.brain = GoogleGenAIBrain()
        logger.info(f"🖥️  MindSpaceAgent: Command brain → Gemini CLI (YOLO mode, model={config.Brains.GEMINI_CLI_MODEL or 'default'})")
        self.cli_brain = GeminiCLIBrain()
        self.kb = None

    def set_kb(self, kb):
        self.kb = kb

    def _inject_view(self, text: str, channel_name: str) -> str:
        if not self.kb or not channel_name:
            return text
        view = self.kb.get_view(channel_name)
        if not view:
            return text
        return f"--- Static Mindset (view.md) ---\n{view}\n\n{text}"

    async def run_command(self, instruction: str, context: str = None, channel_name: str = None) -> str:
        injected_context = self._inject_view(context or "", channel_name) if channel_name else context
        return await self.cli_brain.run_command_async(instruction, injected_context)

    async def stream(self, prompt: str, cwd: str, channel_name: str = None):
        injected_prompt = self._inject_view(prompt, channel_name) if channel_name else prompt
        return await self.cli_brain.stream(injected_prompt, cwd)

    def close(self):
        close_fn = getattr(self.brain, 'close', None)
        if callable(close_fn):
            close_fn()

    async def engage_dialogue(self, user_message, channel_name, history: str = "", tools: list = None, mcp_sessions: list = None):
        history_block = f"\n\n--- Recent Conversation History ---\n(Messages labeled 'assistant' are from this bot. All other names are Discord display names of human users.)\n\n{history}" if history else ""
        system_ctx = prompts.ENGAGE_DIALOGUE_SYSTEM_PROMPT.format(channel_name=channel_name, history_block=history_block)
        injected_ctx = self._inject_view(system_ctx, channel_name)
        response = await self.brain.achat(injected_ctx, [], user_message, tools=tools, mcp_sessions=mcp_sessions)
        return response.strip()

    _TEXT_EXTS = {
        ".md", ".markdown", ".txt", ".rst", ".json", ".yaml", ".yml",
        ".toml", ".csv", ".tsv", ".log", ".py", ".js", ".ts",
        ".html", ".css", ".sql", ".sh", ".bat", ".c", ".cpp", ".h",
        ".java", ".go", ".rs", ".rb", ".php", ".swift", ".kt"
    }

    def is_text_ext(self, ext: str) -> bool:
        return ext.lower() in self._TEXT_EXTS

    async def generate_commit_message(self, context: str) -> str:
        """Generate a concise, high-quality Git commit message based on context."""
        instruction = prompts.COMMIT_MESSAGE_PROMPT
        return await self.brain.run_command_async(instruction, context)

    async def route_file(self, filename: str, snippet: str, tree_listing: str,
                           channel_name: str, advice: str = "") -> tuple[str, str]:
        ext = os.path.splitext(filename)[1].lower()
        advice_block = f"USER ADVICE (steering from the @mention): {advice}\n\n" if advice else ""
        prompt = prompts.ROUTE_FILE_PROMPT.format(
            channel_name=channel_name,
            advice_block=advice_block,
            filename=filename,
            ext=ext or "(none)",
            tree_listing=tree_listing,
            snippet=snippet or "(no preview available — use filename as hint)"
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
        if advice:
            advice_block = f"USER ADVICE: {advice}\n\n"
        else:
            advice_block = (
                "USER ADVICE: (none — the user @mentioned the bot without extra text, "
                "requesting a reviewed save. Infer intent from the draft and the channel "
                "structure.)\n\n"
            )
        prompt = prompts.PLAN_FILE_PROPOSAL_PROMPT.format(
            channel_name=channel_name,
            advice_block=advice_block,
            draft_content=draft_content[:4000],
            kb_context=kb_context or "(empty)",
            tree_listing=tree_listing
        )
        try:
            raw = await self.brain.run_command_async(prompt)
            data = self._extract_json(raw)
            mode = data.get("mode", "new")
            if mode not in ("new", "update"):
                mode = "new"
            return {
                "mode": mode,
                "target_rel_path": str(data.get("target_rel_path", "NEW-FILE.md")),
                "rationale": str(data.get("rationale", "No rationale provided.")),
            }
        except Exception as e:
            logger.warning(f"plan_file_proposal: LLM/parse failure ({e}); falling back to new file")
            return {"mode": "new", "target_rel_path": "INGESTED-DRAFT.md", "rationale": "Parsing failed."}

    async def merge_file_proposal(self, draft_content: str, existing_content: str,
                                   target_rel_path: str, advice: str, channel_name: str) -> str:
        if existing_content:
            prompt = prompts.MERGE_FILE_UPDATE_PROMPT.format(
                channel_name=channel_name,
                target_rel_path=target_rel_path,
                advice=advice or "(none)",
                existing_content=existing_content[:8000],
                draft_content=draft_content[:8000]
            )
        else:
            prompt = prompts.MERGE_FILE_NEW_PROMPT.format(
                channel_name=channel_name,
                target_rel_path=target_rel_path,
                advice=advice or "(none)",
                draft_content=draft_content[:12000]
            )
        return await self.brain.run_command_async(prompt)

    @staticmethod
    def _read_text_snippet(file_path: str, max_chars: int = 5000) -> str:
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
                prompt = prompts.ANALYZE_PDF_PROMPT.format(tree=tree)
                return doc_id, await self.brain.run_command_async(prompt)
            except Exception as e:
                logger.error(f"analyze_file: PDF indexing failed: {e}")
        raw = self._read_text_snippet(file_path)
        return None, await self.brain.run_command_async(prompts.ANALYZE_TEXT_PROMPT.format(raw=raw))

    def _extract_json(self, text: str) -> dict:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return {}
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return {}
