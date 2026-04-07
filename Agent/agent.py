import os
import subprocess
import requests
from bs4 import BeautifulSoup
from litellm import completion
import config
from abc import ABC, abstractmethod

class LLMBrain(ABC):
    @abstractmethod
    def run_command(self, instruction, context_files=None):
        pass

class GeminiCLIBrain(LLMBrain):
    def __init__(self):
        # Isolate the CLI configuration by setting GEMINI_CLI_HOME
        if hasattr(config, "GEMINI_CLI_HOME"):
            os.environ["GEMINI_CLI_HOME"] = config.GEMINI_CLI_HOME
            # Ensure the directory exists
            os.makedirs(config.GEMINI_CLI_HOME, exist_ok=True)

    def run_command(self, instruction, context_files=None):
        """Invoke Gemini CLI with optional context files."""
        if context_files:
            file_context = " ".join([f"@{f}" for f in context_files])
            instruction = f"{file_context}\n\n{instruction}"

        # Explicitly use gemini-3-flash as requested
        command = [config.GEMINI_CLI_COMMAND, "-m", "gemini-3-flash", "-y", "-p", instruction]
        
        # subprocess.run inherits os.environ by default, which now contains GEMINI_CLI_HOME
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
        raise Exception(f"Gemini CLI Error: {result.stderr}")

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
                    print(f"Error reading context file {f_path}: {e}")

        if context_content:
            messages.append({"role": "system", "content": f"You have access to the following context files to help answer the user prompt:\n{context_content}"})
        
        messages.append({"role": "user", "content": instruction})

        response = completion(
            model=self.model,
            messages=messages
        )
        return response.choices[0].message.content.strip()

class MindSpaceAgent:
    def __init__(self, brain_type=None):
        # Default to brain type from config if not provided
        bt = brain_type or getattr(config, "AGENT_BRAIN_TYPE", "cli")
        
        if bt == "litellm":
            self.brain = LiteLLMBrain()
        else:
            self.brain = GeminiCLIBrain()

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
