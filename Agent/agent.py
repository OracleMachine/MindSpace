import os
import requests
from bs4 import BeautifulSoup
from litellm import completion
from google import genai
import config
from abc import ABC, abstractmethod
from logger import logger

class LLMBrain(ABC):
    @abstractmethod
    def run_command(self, instruction, context_files=None):
        pass

class GoogleGenAIBrain(LLMBrain):
    def __init__(self, model=config.GEMINI_SDK_MODEL):
        self.model = model
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)

    def run_command(self, instruction, context_files=None):
        """Invoke Gemini SDK with optional context files."""
        # Handle context files
        context_content = ""
        if context_files:
            for f_path in context_files:
                try:
                    if os.path.exists(f_path):
                        with open(f_path, "r") as f:
                            content = f.read()
                            context_content += f"\n--- Content of {f_path} ---\n{content}\n"
                except Exception as e:
                    logger.error(f"Error reading context file {f_path}: {e}")

        # Construct full prompt
        full_prompt = instruction
        if context_content:
            full_prompt = f"System Context (Files):\n{context_content}\n\nUser Message: {instruction}"

        # API Call
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=full_prompt
            )
            return response.text.strip()
        except Exception as e:
            raise Exception(f"Google GenAI SDK Error: {str(e)}")

class LiteLLMBrain(LLMBrain):
    def __init__(self, model=config.LITELLM_MODEL):
        self.model = model
        if config.GEMINI_API_KEY:
            os.environ["GEMINI_API_KEY"] = config.GEMINI_API_KEY

    def run_command(self, instruction, context_files=None):
        """Invoke LLM using litellm with optional context files."""
        messages = []
        context_content = ""
        if context_files:
            for f_path in context_files:
                try:
                    if os.path.exists(f_path):
                        with open(f_path, "r") as f:
                            content = f.read()
                            context_content += f"\n--- Content of {f_path} ---\n{content}\n"
                except Exception as e:
                    logger.error(f"Error reading context file {f_path}: {e}")

        if context_content:
            messages.append({"role": "system", "content": f"You have access to the following context files to help answer the user prompt:\n{context_content}"})
        
        messages.append({"role": "user", "content": instruction})

        try:
            response = completion(
                model=self.model,
                messages=messages
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            raise Exception(f"LiteLLM Error: {str(e)}")

class MindSpaceAgent:
    def __init__(self, brain_type=None):
        # Default to brain type from config if not provided
        bt = brain_type or config.AGENT_BRAIN_TYPE
        
        if bt == "litellm":
            logger.info(f"🧠 MindSpaceAgent: Initializing LiteLLM Brain (Model: {config.LITELLM_MODEL})")
            self.brain = LiteLLMBrain()
        else:
            logger.info(f"🧠 MindSpaceAgent: Initializing Google GenAI SDK Brain (Model: {config.GEMINI_SDK_MODEL})")
            self.brain = GoogleGenAIBrain()

    def run_command(self, instruction, context_files=None):
        return self.brain.run_command(instruction, context_files)

    def engage_dialogue(self, user_message, channel_name, context_files=None):
        prompt = (
            f"Context: Channel #{channel_name}. "
            f"1. Reply to this user message naturally: '{user_message}'. "
            f"2. Separately, if this message contains a valuable insight or note worth recording, "
            f"provide a 'THOUGHT: [summary]' at the end of your response. Otherwise, do not include a THOUGHT block."
        )
        response = self.run_command(prompt, context_files)
        reply = response
        thought = None
        if "THOUGHT:" in response:
            parts = response.split("THOUGHT:")
            reply = parts[0].strip()
            thought = parts[1].strip()
        return reply, thought

    def process_url(self, url, channel_name):
        """Fetch URL content and use LLM to summarize it."""
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            for script_or_style in soup(["script", "style"]):
                script_or_style.decompose()
            text = soup.get_text(separator='\n')[:10000]
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
