import os
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
    def chat(self, system_ctx: str, history: list, message: str) -> str:
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

    def chat(self, system_ctx: str, history: list, message: str) -> str:
        """Multi-turn call: system context via system_instruction, history as prior turns."""
        contents = []
        for role, content in history:
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": content}]})
        contents.append({"role": "user", "parts": [{"text": message}]})
        try:
            cfg = types.GenerateContentConfig(system_instruction=system_ctx) if system_ctx else None
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

    def chat(self, system_ctx: str, history: list, message: str) -> str:
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

class MindSpaceAgent:
    def __init__(self, brain_type=None):
        bt = brain_type or config.AGENT_BRAIN_TYPE
        if bt == "litellm":
            logger.info(f"🧠 MindSpaceAgent: Initializing LiteLLM Brain (Model: {config.LITELLM_MODEL})")
            self.brain = LiteLLMBrain()
        else:
            logger.info(f"🧠 MindSpaceAgent: Initializing Google GenAI SDK Brain (Model: {config.GEMINI_SDK_MODEL})")
            self.brain = GoogleGenAIBrain()

    def run_command(self, instruction: str, context: str = None) -> str:
        return self.brain.run_command(instruction, context)

    def engage_dialogue(self, user_message, channel_name, context=None, history: str = "", stream_content=""):
        system_parts = [f"You are a knowledge agent in Discord channel #{channel_name}."]
        if history:
            system_parts.append(
                f"--- Recent Conversation History ---\n"
                f"(Messages labeled 'assistant' are from this bot. "
                f"All other names are Discord display names of human users.)\n\n"
                f"{history}"
            )
        if context:
            system_parts.append(f"--- Channel Knowledge Base ---\n{context}")
        if stream_content:
            system_parts.append(f"--- Stream of Consciousness (extracted insights so far) ---\n{stream_content}")
        system_parts.append(
            "Reply naturally to the user. "
            "If the message contains a valuable insight worth recording, append 'THOUGHT: [summary]' at the end. "
            "Otherwise do not include a THOUGHT block."
        )
        system_ctx = "\n\n".join(system_parts)

        response = self.brain.chat(system_ctx, [], user_message)
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
        Returns (doc_id_or_None, analysis_text).
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
                return doc_id, self.run_command(prompt)
            except Exception:
                pass
        # Fallback: read raw content
        try:
            with open(file_path, "r", errors="ignore") as f:
                raw = f.read(5000)
        except Exception:
            raw = ""
        return None, self.run_command(f"Analyze this file content and summarize it:\n\n{raw}")

    def process_url(self, url, channel_name):
        """Fetch URL content and use LLM to summarize it."""
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
            return self.run_command(prompt)
        except Exception as e:
            return f"Error fetching URL: {str(e)}"

    def generate_commit_message(self, action_description):
        prompt = f"Generate a one-sentence Git commit message explaining the intent behind this action: {action_description}"
        return self.run_command(prompt)
