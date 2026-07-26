import re
import subprocess
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()


def query_lm(messages):
    response = client.responses.create(model="openrouter/free", input=messages)
    return response.output_text


client = OpenAI()  # or set OPENAI_API_KEY env var


def parse_action(lm_output: str) -> str:
    """Take LM output, return action"""
    matches = re.findall(r"```bash-action\s*\n(.*?)\n```", lm_output, re.DOTALL)
    return matches[0].strip() if matches else ""


def execute_action(command: str) -> str:
    """Execute action, return output"""
    result = subprocess.run(
        command,
        shell=True,
        text=True,
        env=os.environ,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    return result.stdout


# Main agent loop
messages = [
    {
        "role": "system",
        "content": "You are a helpful assistant. When you want to run a command, wrap it in ```bash-action\n<command>\n```. To finish, run the exit command.",
    },
    {"role": "user", "content": "List the files in the current directory"},
]

while True:
    lm_output = query_lm(messages)
    print("LM output", lm_output)
    messages.append(
        {"role": "assistant", "content": lm_output}
    )  # remember what the LM said
    action = parse_action(lm_output)  # separate the action from output
    print("Action", action)
    if action == "exit":
        break
    output = execute_action(action)
    print("Output", output)
    messages.append({"role": "user", "content": output})  # send command output back
