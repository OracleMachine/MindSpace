import re
import json
from bs4 import BeautifulSoup

from mindspace.core import config
from mindspace.core.logger import logger
from mindspace.agent.brain import GoogleGenAIBrain, GeminiCLIBrain

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
        from mindspace.agent.brain import CliStream
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
        ".html", ".css", ".sql", ".sh", ".bat", ".c", ".cpp", ".h",
        ".java", ".go", ".rs", ".rb", ".php", ".swift", ".kt"
    }

    def is_text_ext(self, ext: str) -> bool:
        return ext.lower() in self._TEXT_EXTS

    async def process_url(self, url: str, channel_name: str) -> str:
        """Fetch a URL and return a clean Markdown representation."""
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Simple heuristic for readability
            for s in soup(["script", "style", "nav", "footer", "header", "aside"]):
                s.decompose()
            
            # Title extraction
            title = soup.title.string if soup.title else "Untitled Page"
            
            # Very basic markdown conversion (improve with html2text if needed)
            content = soup.get_text(separator="\n", strip=True)
            return f"# {title}\n\nURL: {url}\n\n{content}"

    async def generate_commit_message(self, context: str) -> str:
        """Generate a concise, high-quality Git commit message based on context."""
        instruction = (
            "Generate a concise, conventional-commits style Git commit message (e.g., 'feat: ...' or 'fix: ...') "
            "based on the provided context. Output ONLY the message text."
        )
        return await self.brain.run_command_async(instruction, context)

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
        """Second stage of Path B — generate the final markdown content.
        For updates, the LLM performs the merge. For new files, it cleans up the draft.
        Returns the new document content."""
        if existing_content:
            prompt = (
                f"MERGE TASK: A user dropped a markdown draft to update an existing KB file "
                f"in channel #{channel_name}.\n\n"
                f"TARGET FILE: {target_rel_path}\n"
                f"USER ADVICE: {advice or '(none)'}\n\n"
                f"EXISTING CONTENT:\n---\n{existing_content[:8000]}\n---\n\n"
                f"DROPPED DRAFT:\n---\n{draft_content[:8000]}\n---\n\n"
                f"RULES:\n"
                f"- Merge all relevant information from the draft into the existing file.\n"
                f"- Maintain the existing file's tone and structure.\n"
                f"- If the target file is a poor fit for this draft, output ONLY the sentinel "
                f"string 'NEW_FILE_INSTEAD' and nothing else.\n"
                f"- Otherwise, output ONLY the complete, merged markdown content."
            )
        else:
            prompt = (
                f"NEW FILE TASK: A user dropped a markdown draft into channel #{channel_name}.\n\n"
                f"TARGET PATH: {target_rel_path}\n"
                f"USER ADVICE: {advice or '(none)'}\n\n"
                f"DROPPED DRAFT:\n---\n{draft_content[:12000]}\n---\n\n"
                f"RULES:\n"
                f"- Polish the draft into a high-quality KB article.\n"
                f"- Ensure proper H1 title and structure.\n"
                f"- Output ONLY the complete markdown content."
            )
        return await self.brain.run_command_async(prompt)

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

    def _extract_json(self, text: str) -> dict:
        """Grab the first {...} block so stray prose around JSON is ignored."""
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return {}
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return {}
