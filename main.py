import base64
import argparse
import json
import re
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL_NAME", "openrouter/free")

IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def read(path: str, offset: int = 1, limit: int = 2000) -> str | list[dict[str, str]]:
    target = Path(path)
    if target.suffix.lower() in IMAGE_TYPES:
        data = base64.b64encode(target.read_bytes()).decode()
        return [
            {
                "type": "input_image",
                "image_url": f"data:{IMAGE_TYPES[target.suffix.lower()]};base64,{data}",
            }
        ]
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(
        f"{number}: {line}"
        for number, line in enumerate(
            lines[max(offset - 1, 0) : max(offset - 1, 0) + max(limit, 0)],
            start=max(offset, 1),
        )
    )


def write(path: str, content: str) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {target}"


def edit(path: str, oldText: str, newText: str) -> str:
    target = Path(path)
    content = target.read_text(encoding="utf-8", errors="replace")
    if content.count(oldText) != 1:
        return "Error: oldText must occur exactly once"
    target.write_text(content.replace(oldText, newText, 1), encoding="utf-8")
    return f"Edited {target}"


def bash(command: str, timeout: int | None = None) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=os.getcwd(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            return f"[exit {result.returncode}]\n{result.stdout}"
        return result.stdout
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except OSError as e:
        return f"Error: {e}"


TOOLS = [
    {
        "type": "function",
        "name": "read",
        "description": "Read the contents of a file. Supports text files and images (jpg, png, gif, webp). For text files, output is numbered lines. Image files return a base64 data URL attachment.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to read"},
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-indexed)",
                    "default": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read",
                    "default": 2000,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "write",
        "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does. Automatically creates parent directories.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to write"},
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "edit",
        "description": "Edit a file by replacing exactly one occurrence of old text with new text. If oldText occurs zero or multiple times, no change is made and an error is returned.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to edit"},
                "oldText": {
                    "type": "string",
                    "description": "Exact text to replace (must occur exactly once)",
                },
                "newText": {
                    "type": "string",
                    "description": "Replacement text",
                },
            },
            "required": ["path", "oldText", "newText"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "bash",
        "description": "Execute a bash command in the current working directory. Returns combined stdout and stderr. On non-zero exit, output is prefixed with [exit N].",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Bash command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (optional)",
                    "default": None,
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

TOOL_HANDLERS = {
    "read": read,
    "write": write,
    "edit": edit,
    "bash": bash,
}

MAX_TOOL_OUTPUT_CHARS = 12_000

BANNER = "k\n\U0001f60e"

RESET = "\033[0m"
ANSWER = "\033[93m"
TOOL = "\033[94m"
ERROR = "\033[91m"
STATUS = "\033[37m"
CODE = "\033[37m"


def _inline(line: str) -> str:
    line = re.sub(r"`([^`]+)`", lambda m: f"{CODE}{m.group(1)}{ANSWER}", line)
    line = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"\033[1m{m.group(1)}{ANSWER}", line)
    line = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", lambda m: f"\033[3m{m.group(1)}{ANSWER}", line)
    return line


def format_markdown(text: str) -> str:
    lines = text.splitlines()
    in_fence = False
    result = []
    for line in lines:
        if line.startswith("```"):
            result.append(f"{ANSWER}{CODE}{line}{RESET}")
            in_fence = not in_fence
            continue
        if in_fence:
            result.append(f"{ANSWER}{CODE}{line}{RESET}")
            continue

        if m := re.match(r"^(#{1,6})\s+(.*)$", line):
            result.append(f"{ANSWER}\033[1m{_inline(m.group(2))}{ANSWER}{RESET}")
            continue
        if m := re.match(r"^\s*[-*+]\s+(.*)$", line):
            result.append(f"{ANSWER}• {_inline(m.group(1))}{RESET}")
            continue
        if m := re.match(r"^\s*(\d+)\.\s+(.*)$", line):
            result.append(f"{ANSWER}{m.group(1)}. {_inline(m.group(2))}{RESET}")
            continue
        if m := re.match(r"^>\s?(.*)$", line):
            result.append(f"{ANSWER}\033[37m│ {_inline(m.group(1))}{RESET}")
            continue
        if m := re.match(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$", line):
            result.append(f"{ANSWER}\033[37m{'─' * 3}{RESET}")
            continue

        result.append(f"{ANSWER}{_inline(line)}{RESET}")
    return "\n".join(result)


def render_banner() -> None:
    render("status", BANNER)


def k_dir() -> Path:
    return Path.home() / ".k"


def sessions_dir() -> Path:
    return k_dir() / "sessions"


def new_session_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid4().hex[:6]}"


def session_path(session_id: str) -> Path:
    if "/" in session_id or "\\" in session_id or session_id in {"", ".", ".."}:
        raise ValueError("invalid session id")
    return sessions_dir() / f"{session_id}.json"


def load_session(session_id: str) -> dict:
    return json.loads(session_path(session_id).read_text(encoding="utf-8"))


def save_session(session: dict) -> None:
    path = session_path(session["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=2), encoding="utf-8")


def compact_output(text: str, label: str, session_id: str | None = None) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    head = text[:6_000]
    tail = text[-2_000:]
    saved = ""
    if session_id:
        artifact_dir = sessions_dir() / f"{session_id}-artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact = artifact_dir / f"{label}-{datetime.now(timezone.utc).strftime('%H%M%S')}-{uuid4().hex[:6]}.txt"
        artifact.write_text(text, encoding="utf-8")
        saved = f"\nFull output saved: {artifact}"
    return f"[context-mode: output compacted from {len(text)} chars]\n{head}\n...\n{tail}{saved}"


SYSTEM_PROMPT = """You are an expert coding assistant. You help users with coding tasks by reading files, executing commands, editing code, and writing new files.

Available tools:
- read: Read file contents
- bash: Execute bash commands
- edit: Make surgical edits to files
- write: Create or overwrite files

Guidelines:
- Use bash for file operations like ls, grep, find
- Use read to examine files before editing
- Use edit for precise changes (old text must match exactly)
- Use write only for new files or complete rewrites
- When summarizing your actions, output plain text directly - do NOT use cat or bash to display what you did
- Be concise in your responses
- Show file paths clearly when working with files"""


def dispatch_tool(name: str, arguments: str, call_id: str, session_id: str | None = None) -> dict:
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError as e:
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": f"Error: invalid JSON in arguments: {e}",
        }
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": f"Error: Unknown tool: {name}",
        }
    try:
        result = handler(**args)
    except Exception as e:
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": f"Error: {e}",
        }
    if isinstance(result, list):
        return {"type": "function_call_output", "call_id": call_id, "output": result}
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": compact_output(str(result), name, session_id),
    }


def git_branch() -> str | None:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=Path.cwd(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() or None


def status_line(total_tokens: int | str, context_tokens: int | None) -> str:
    parts = ["k", str(Path.cwd())]
    if branch := git_branch():
        parts.append(branch)
    context = context_tokens if context_tokens is not None else "?"
    parts.extend(
        [f"model: {MODEL}", f"tokens: {total_tokens}", f"context: {context}"]
    )
    return " | ".join(parts)


def render(kind: str, text: str) -> None:
    is_tty = sys.stdout.isatty()
    if kind == "tool":
        if is_tty:
            sys.stdout.write(f"  {TOOL}{text}{RESET}\n")
        else:
            sys.stdout.write(f"  {text}\n")
    elif kind == "output":
        if is_tty:
            sys.stdout.write(f"{ERROR}{text}{RESET}\n")
        else:
            sys.stdout.write(f"{text}\n")
    elif kind == "status":
        if is_tty and text.startswith("k"):
            sys.stdout.write(f"{ANSWER}k{STATUS}{text[1:]}{RESET}\n")
        elif is_tty:
            sys.stdout.write(f"{STATUS}{text}{RESET}\n")
        else:
            sys.stdout.write(f"{text}\n")
    elif kind == "answer":
        if is_tty:
            sys.stdout.write(f"{format_markdown(text)}\n")
        else:
            sys.stdout.write(f"{text}\n")
    sys.stdout.flush()


def render_pinned_status(text: str, *, move_cursor: bool = False) -> bool:
    if not sys.stdout.isatty():
        render("status", text)
        return False
    rows = shutil.get_terminal_size(fallback=(80, 24)).lines
    if rows < 3:
        render("status", text)
        return False
    if not move_cursor:
        sys.stdout.write("\033[s")
    sys.stdout.write(f"\033[2;{rows}r\033[1;1H\033[2K")
    render("status", text)
    sys.stdout.write("\033[2;1H" if move_cursor else "\033[u")
    sys.stdout.flush()
    return True


def restore_terminal() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\033[r\033[0m\n")
        sys.stdout.flush()


def handle_command(text: str) -> str | None:
    if not text.startswith("/"):
        return None
    if text == "/quit":
        return "quit"
    return f"Unknown command: {text.split()[0]}"


def run_agent(task: str, client: OpenAI, history: list[object] | None = None, session_id: str | None = None) -> tuple[str, int, int | None, list[object]]:
    history = list(history or [])
    history.append({"role": "user", "content": task})
    response = client.responses.create(
        model=MODEL,
        instructions=SYSTEM_PROMPT,
        input=list(history),
        tools=TOOLS,
    )
    total_tokens = 0
    context_tokens = None
    while True:
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        total_tokens += (input_tokens or 0) + (output_tokens or 0)
        if input_tokens is not None:
            context_tokens = input_tokens

        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            history.extend(response.output)
            return response.output_text, total_tokens, context_tokens, history

        tool_outputs = [
            dispatch_tool(call.name, call.arguments, call.call_id, session_id) for call in calls
        ]
        for call in calls:
            render("tool", f"{call.name}({call.arguments})")
        history.extend(response.output)
        history.extend(tool_outputs)
        response = client.responses.create(
            model=MODEL,
            instructions=SYSTEM_PROMPT,
            input=list(history),
            tools=TOOLS,
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="k")
    parser.add_argument("--resume", metavar="ID")
    args, _ = parser.parse_known_args()
    session = load_session(args.resume) if args.resume else {"id": new_session_id(), "history": [], "transcript": []}
    client = OpenAI()
    try:
        if sys.stdout.isatty():
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
        render_banner()
        render_pinned_status(status_line("?", None), move_cursor=True)
        while True:
            try:
                task = input("\u276f ").strip()
            except EOFError:
                return
            if not task:
                return
            command = handle_command(task)
            if command == "quit":
                return
            if command:
                render("output", command)
                continue
            try:
                answer, total_tokens, context_tokens, history = run_agent(task, client, session["history"], session["id"])
                session["transcript"].append({"prompt": task, "answer": answer})
                save_session(session)
            except Exception as e:
                render("output", f"Error: {e}")
                render_pinned_status(status_line("?", None))
                continue
            render("answer", answer)
            render_pinned_status(status_line(total_tokens, context_tokens))
    except KeyboardInterrupt:
        return
    finally:
        restore_terminal()


if __name__ == "__main__":
    main()
