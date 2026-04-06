import subprocess
import config

class GeminiAgent:
    def __init__(self, model="gemini-1.5-pro"):
        self.model = model

    def run_command(self, instruction, context_files=None):
        """Invoke Gemini CLI with optional context files and -y for non-interactive mode."""
        # -p/--prompt is required for non-interactive (headless) mode
        command = [config.GEMINI_CLI_COMMAND, "-y", "-p", instruction]
        if context_files:
            # Files are passed as positional arguments following the prompt
            command.extend(context_files)
        
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
        raise Exception(f"Gemini CLI Error: {result.stderr}")

    def engage_dialogue(self, user_message, channel_name, context_files=None):
        """
        Handle Active Thought Dialogue.
        Returns (reply_to_user, extracted_thought_or_none)
        """
        # We ask Gemini to perform two tasks in one reasoning cycle
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
        """Use Gemini CLI to fetch, parse, and summarize a URL."""
        prompt = (
            f"Access this URL: {url}. Extract the main article content and "
            f"format it as a clean Markdown file with a human-readable title. "
            f"Include the original URL at the top."
        )
        return self.run_command(prompt)

    def generate_commit_message(self, action_description):
        """Ask Gemini for a high-level intent message for Git."""
        prompt = f"Generate a one-sentence Git commit message explaining the intent behind this action: {action_description}"
        return self.run_command(prompt)
