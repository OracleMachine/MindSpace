import os
import subprocess
import requests
from bs4 import BeautifulSoup
from litellm import completion
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
    def chat(self, system_ctx: str, history: list, message: str, tools: list = None) -> str:
        """Multi-turn dialogue with persistent system context and conversation history."""
        pass

class GoogleGenAIBrain(LLMBrain):
    def __init__(self, model=config.GEMINI_SDK_MODEL):
        self.model = model
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)

    def run_command(self, instruction: str, context: str = None) -> str:
        full_prompt = f"System Context:\n{context}\n\nUser Message: {instruction}" if context else instruction
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=full_prompt
            )
            return response.text.strip()
        except Exception as e:
            raise Exception(f"Google GenAI SDK Error: {str(e)}")

    def chat(self, system_ctx: str, history: list, message: str, tools: list = None) -> str:
        """Multi-turn call: system context via system_instruction, history as prior turns."""
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

class LiteLLMBrain(LLMBrain):
    def __init__(self, model=config.LITELLM_MODEL):
        self.model = model
        if config.GEMINI_API_KEY:
            os.environ["GEMINI_API_KEY"] = config.GEMINI_API_KEY

    def run_command(self, instruction: str, context: str = None) -> str:
        messages = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": instruction})
        try:
            response = completion(model=self.model, messages=messages)
            return response.choices[0].message.content.strip()
        except Exception as e:
            raise Exception(f"LiteLLM Error: {str(e)}")

    def chat(self, system_ctx: str, history: list, message: str, tools: list = None) -> str:
        """Multi-turn call: system context as system message, history as prior turns."""
        messages = []
        if system_ctx:
            messages.append({"role": "system", "content": system_ctx})
        for role, content in history:
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})
        try:
            response = completion(model=self.model, messages=messages)
            return response.choices[0].message.content.strip()
        except Exception as e:
            raise Exception(f"LiteLLM Error: {str(e)}")

class GeminiCLIBrain(LLMBrain):
    """
    Delegates to the Gemini CLI (gemini -y).
    Capabilities beyond the direct API: web search, file I/O, multi-step agentic loops.
    Used for all commands (!organize, !consolidate, !research, !omni).
    """

    def __init__(self, yolo: bool = True):
        self.yolo = yolo

    def build_args(self) -> list[str]:
        """Return subprocess args. Prompt is passed via stdin to avoid OS arg length limits."""
        args = ["gemini"]
        if self.yolo:
            args.append("-y")
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
        result = subprocess.run(
            self.build_args(),
            input=prompt,
            capture_output=True, text=True, timeout=300
        )
        return result.stdout.strip() or result.stderr.strip()

    def chat(self, system_ctx: str, history: list, message: str, tools: list = None) -> str:
        # tools ignored — Gemini CLI manages its own tool loop internally
        prompt = self._build_prompt(message, system_ctx=system_ctx, history=history)
        return self.run_command(prompt)


class MindSpaceAgent:
    def __init__(self, brain_type=None):
        # Dialogue brain: passive chat, URL/file analysis, commit messages (fast API calls)
        dt = brain_type or config.DIALOGUE_BRAIN_TYPE
        if dt == "litellm":
            logger.info(f"🧠 MindSpaceAgent: Dialogue brain → LiteLLM (Model: {config.LITELLM_MODEL})")
            self.brain = LiteLLMBrain()
        elif dt == "GoogleGenAISdk":
            logger.info(f"🧠 MindSpaceAgent: Dialogue brain → Google GenAI SDK (Model: {config.GEMINI_SDK_MODEL})")
            self.brain = GoogleGenAIBrain()
        else:
            raise ValueError(f"Unknown DIALOGUE_BRAIN_TYPE: '{dt}'. Valid options: 'GoogleGenAISdk', 'litellm'")

        # Command brain: !organize, !consolidate, !research, !omni (agentic, file I/O, web search)
        logger.info("🖥️  MindSpaceAgent: Command brain → Gemini CLI (YOLO mode)")
        self.cli_brain = GeminiCLIBrain()

    def run_command(self, instruction: str, context: str = None) -> str:
        """Execute a command via the Gemini CLI brain (file I/O, web search, agentic tasks)."""
        return self.cli_brain.run_command(instruction, context)

    def engage_dialogue(self, user_message, channel_name, history: str = "", stream_content="", tools: list = None):
        system_parts = [f"You are a knowledge agent in Discord channel #{channel_name}."]
        if history:
            system_parts.append(
                f"--- Recent Conversation History ---\n"
                f"(Messages labeled 'assistant' are from this bot. "
                f"All other names are Discord display names of human users.)\n\n"
                f"{history}"
            )
        if stream_content:
            system_parts.append(f"--- Stream of Consciousness (extracted insights so far) ---\n{stream_content}")
        
        system_parts.append(
            "You have access to the channel's knowledge base and the global repository via tools. "
            "If the user asks a question or makes a statement that requires factual information from the repository, "
            "use the search tools autonomously before answering.\n\n"
            "Reply naturally to the user. "
            "If the message contains a valuable insight worth recording, append 'THOUGHT: [summary]' at the end. "
            "Otherwise do not include a THOUGHT block."
        )
        system_ctx = "\n\n".join(system_parts)

        response = self.brain.chat(system_ctx, [], user_message, tools=tools)
        reply = response
        thought = None
        if "THOUGHT:" in response:
            parts = response.split("THOUGHT:")
            reply = parts[0].strip()
            thought = parts[1].strip()
        return reply, thought

    def analyze_file(self, file_path: str, pageindex) -> tuple:
        """
        Analyze an uploaded file. Uses PageIndex for PDFs; reads raw content for other types.
        Returns (doc_id_or_None, analysis_text). Uses dialogue brain (fast API call).
        """
        ext = os.path.splitext(file_path)[1].lower()
        channel_name = os.path.basename(os.path.dirname(file_path))
        if ext == ".pdf":
            try:
                doc_id = pageindex.index_document(file_path, channel_name)
                tree = pageindex.get_tree(doc_id)
                prompt = (
                    f"This PDF has been indexed. Its document tree structure:\n\n{tree}\n\n"
                    f"Provide a one-sentence description of the document's content and purpose."
                )
                return doc_id, self.brain.run_command(prompt)
            except Exception:
                pass
        # Fallback: read raw content
        try:
            with open(file_path, "r", errors="ignore") as f:
                raw = f.read(5000)
        except Exception:
            raw = ""
        return None, self.brain.run_command(f"Analyze this file content and summarize it:\n\n{raw}")

    def process_url(self, url, channel_name):
        """Fetch URL content and summarize via dialogue brain (fast API call)."""
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=10)
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
            return self.brain.run_command(prompt)
        except Exception as e:
            return f"Error fetching URL: {str(e)}"

    def generate_commit_message(self, action_description):
        """Generate a commit message via dialogue brain (lightweight, fast API call)."""
        prompt = f"Generate a one-sentence Git commit message explaining the intent behind this action: {action_description}"
        return self.brain.run_command(prompt)
