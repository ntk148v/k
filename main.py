import base64
import argparse
import json
import re
import readline  # noqa: F401
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

# pydantic-core, used by the OpenAI SDK, can crash under pre-release CPython.
# Fail before importing it so `uv run main.py` gives an actionable error.
if __name__ == "__main__" and sys.version_info.releaselevel != "final":
    print(
        f"Error: k requires a stable Python release; found {sys.version.split()[0]}. Recreate .venv with `uv python install 3.12 && uv sync`.",
        file=sys.stderr,
    )
    raise SystemExit(1)

from openai import OpenAI

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL_NAME", "openrouter/free")
API_MODE = os.getenv("K_API_MODE", "responses")

# Ctrl+J is normally another name for Enter. Quote it so readline inserts a
# literal newline instead; Enter (Ctrl+M) still submits the prompt.
MULTILINE_INPUT_BINDING = r'"\C-j": "\C-v\C-j"'

IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def read(path: str, offset: int = 1, limit: int = 2000) -> str | list[dict[str, str]]:
    if offset < 1:
        return "Error: offset must be at least 1"
    if limit < 1:
        return "Error: limit must be at least 1"
    try:
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
                lines[offset - 1 : offset - 1 + limit],
                start=offset,
            )
        )
    except (OSError, ValueError) as error:
        return f"Error: cannot read {path}: {error}"


def write(path: str, content: str) -> str:
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {target}"
    except (OSError, ValueError) as error:
        return f"Error: cannot write {path}: {error}"


def edit(path: str, oldText: str, newText: str) -> str:
    try:
        target = Path(path)
        content = target.read_text(encoding="utf-8", errors="replace")
        if content.count(oldText) != 1:
            return "Error: oldText must occur exactly once"
        target.write_text(content.replace(oldText, newText, 1), encoding="utf-8")
        return f"Edited {target}"
    except (OSError, ValueError) as error:
        return f"Error: cannot edit {path}: {error}"


def is_dangerous_bash(command: str) -> bool:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False

    def is_dangerous_simple(simple: list[str]) -> bool:
        if not simple:
            return False
        end = simple.index("--") if "--" in simple else len(simple)
        options = [token for token in simple[1:end] if token.startswith("-")]
        short_options = "".join(token[1:] for token in options if not token.startswith("--"))
        if simple[0] == "rm":
            return ("r" in short_options or "R" in short_options or "--recursive" in options) and ("f" in short_options or "--force" in options)
        if simple[:2] == ["git", "reset"]:
            return "--hard" in options
        if simple[:2] == ["git", "clean"]:
            return "f" in short_options or "--force" in options
        if simple[:2] == ["git", "push"]:
            return "f" in short_options or "--force" in options or "--force-with-lease" in options
        return False

    simple: list[str] = []
    for token in [*tokens, ";"]:
        if token and set(token) <= set(";&|()"):
            if is_dangerous_simple(simple):
                return True
            simple = []
        else:
            simple.append(token)
    return False


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

CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
            "strict": tool["strict"],
        },
    }
    for tool in TOOLS
]

TOOL_HANDLERS = {
    "read": read,
    "write": write,
    "edit": edit,
    "bash": bash,
}

MAX_TOOL_OUTPUT_CHARS = 12_000

USER_SKILLS_DIR = Path.home() / ".agents" / "skills"


def available_skills() -> dict[str, Path]:
    return {path.parent.name: path for path in USER_SKILLS_DIR.rglob("SKILL.md")}


def skill_instructions(names: list[str]) -> tuple[str, list[str]]:
    skills = available_skills()
    missing = [name for name in names if name not in skills]
    active = [f"### {name}\n{skills[name].read_text(encoding='utf-8')}" for name in names if name in skills]
    suffix = "\n\n## Active skills\n\n" + "\n\n".join(active) if active else ""
    return SYSTEM_PROMPT + suffix, missing


RESET = "\033[0m"
PALETTES = {
    "light": {
        "text": "#1f2328",
        "accent": "#5a8080",
        "muted": "#6c6c6c",
        "tool": "#547da7",
        "error": "#aa5555",
        "heading": "#9a7326",
        "code": "#5a8080",
        "code_block": "#588458",
        "list": "#588458",
    },
    "dark": {
        "text": "#d4d4d4",
        "accent": "#8abeb7",
        "muted": "#808080",
        "tool": "#5f87ff",
        "error": "#cc6666",
        "heading": "#f0c674",
        "code": "#8abeb7",
        "code_block": "#b5bd68",
        "list": "#8abeb7",
    },
}
COLORS: dict[str, str] = {}


def set_theme(name: str) -> None:
    palette = PALETTES.get(name, PALETTES["light"])
    COLORS.clear()
    COLORS.update({key: f"\033[38;2;{int(value[1:3], 16)};{int(value[3:5], 16)};{int(value[5:7], 16)}m" for key, value in palette.items()})


def color(name: str) -> str:
    return COLORS[name]


set_theme(os.getenv("K_THEME", "light"))


def _inline(line: str) -> str:
    line = re.sub(r"`([^`]+)`", lambda m: f"{color('code')}{m.group(1)}{color('text')}", line)
    line = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"\033[1m{m.group(1)}{RESET}{color('text')}", line)
    line = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", lambda m: f"\033[3m{m.group(1)}{RESET}{color('text')}", line)
    return line


def format_markdown(text: str) -> str:
    in_fence = False
    result = []
    for line in text.splitlines():
        if line.startswith("```"):
            result.append(f"{color('muted')}{line}{RESET}")
            in_fence = not in_fence
        elif in_fence:
            result.append(f"{color('code_block')}{line}{RESET}")
        elif m := re.match(r"^(#{1,6})\s+(.*)$", line):
            result.append(f"{color('heading')}\033[1m{_inline(m.group(2))}{RESET}")
        elif m := re.match(r"^\s*[-*+]\s+(.*)$", line):
            result.append(f"{color('list')}• {color('text')}{_inline(m.group(1))}{RESET}")
        elif m := re.match(r"^\s*(\d+)\.\s+(.*)$", line):
            result.append(f"{color('list')}{m.group(1)}. {color('text')}{_inline(m.group(2))}{RESET}")
        elif m := re.match(r"^>\s?(.*)$", line):
            result.append(f"{color('muted')}│ {m.group(1)}{RESET}")
        elif re.match(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$", line):
            result.append(f"{color('muted')}{'─' * 3}{RESET}")
        else:
            result.append(f"{color('text')}{_inline(line)}{RESET}")
    return "\n".join(result)


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


def ensure_session_defaults(session: dict) -> dict:
    session.setdefault("skills", [])
    session.setdefault("api_mode", API_MODE)
    session.setdefault("model", MODEL)
    session.setdefault("title", "")
    session.setdefault("cwd", "")
    session.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    session.setdefault("updated_at", session["created_at"])
    session.setdefault("undo", None)
    return session


def load_session(session_id: str) -> dict:
    if "/" in session_id or "\\" in session_id or session_id in {"", ".", ".."}:
        raise ValueError("cannot load session: invalid id")
    try:
        session = json.loads(session_path(session_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot load session: {error}") from None
    if not isinstance(session, dict):
        raise ValueError("cannot load session: expected an object")
    if (
        not isinstance(session.get("id"), str)
        or session["id"] != session_id
        or not isinstance(session.get("history"), list)
        or not isinstance(session.get("transcript"), list)
        or ("skills" in session and not isinstance(session["skills"], list))
        or any(field in session and not isinstance(session[field], str) for field in ("model", "title", "cwd", "created_at", "updated_at"))
        or ("undo" in session and session["undo"] is not None and not isinstance(session["undo"], dict))
    ):
        raise ValueError("cannot load session: invalid session data")
    return ensure_session_defaults(session)


def save_session(session: dict) -> None:
    ensure_session_defaults(session)
    if not session["cwd"]:
        session["cwd"] = str(Path.cwd())
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = session_path(session["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=2, default=lambda value: value.to_dict()), encoding="utf-8")


MAX_SESSION_LISTING = 20


def list_sessions(limit: int = MAX_SESSION_LISTING) -> list[dict[str, str]]:
    summaries = []
    for path in sessions_dir().glob("*.json"):
        try:
            session = load_session(path.stem)
        except (ValueError, OSError):
            continue
        summaries.append(
            {
                "id": session["id"],
                "title": session["title"] or "(untitled)",
                "cwd": session["cwd"],
                "updated_at": session["updated_at"],
                "model": session["model"],
            }
        )
    return sorted(summaries, key=lambda summary: summary["updated_at"], reverse=True)[:limit]


def format_session_list(sessions: list[dict[str, str]]) -> str:
    if not sessions:
        return "No saved sessions."
    lines = ["Recent sessions:"]
    for s in sessions:
        parts = [s["id"], s["title"], s["model"]]
        if s["cwd"]:
            parts.append(s["cwd"])
        if s["updated_at"]:
            parts.append(s["updated_at"][:19])
        lines.append("  " + " | ".join(parts))
    return "\n".join(lines)


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


def dispatch_tool(
    name: str, arguments: str, call_id: str, session_id: str | None = None, *, confirm_dangerous: object = None, on_activity: object = None
) -> dict:
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError as e:
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": f"Error: invalid JSON in arguments: {e}",
        }
    command = args.get("command") if isinstance(args, dict) else None
    if name == "bash" and isinstance(command, str) and is_dangerous_bash(command):
        if confirm_dangerous is None or not confirm_dangerous(command):
            return {
                "type": "function_call_output",
                "call_id": call_id,
                "output": "Error: dangerous bash command rejected by user",
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


def _git(args: list[str], data: str | None = None) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=Path.cwd(),
            text=True,
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _git_root() -> str | None:
    root = _git(["rev-parse", "--show-toplevel"])
    return str(Path(root.strip()).resolve()) if root else None


def begin_undo_checkpoint() -> dict | None:
    if _git(["status", "--porcelain"]) != "":
        return None
    root = _git_root()
    patch = _git(["diff", "--binary", "HEAD"])
    untracked = _git(["ls-files", "--others", "--exclude-standard"])
    if root is None or patch is None or untracked is None:
        return None
    return {"root": root, "patch": patch, "untracked": sorted(set(untracked.splitlines()))}


def finish_undo_checkpoint(checkpoint: dict | None) -> dict | None:
    if not isinstance(checkpoint, dict):
        return None
    root = checkpoint.get("root")
    baseline_patch = checkpoint.get("patch")
    baseline_untracked = checkpoint.get("untracked")
    if not isinstance(root, str) or not isinstance(baseline_patch, str) or not isinstance(baseline_untracked, list):
        return None
    if _git_root() != root:
        return None
    patch = _git(["diff", "--binary", "HEAD"])
    index_patch = _git(["diff", "--binary", "--cached", "HEAD"])
    untracked = _git(["ls-files", "--others", "--exclude-standard"])
    if patch is None or index_patch is None or untracked is None:
        return None
    post_untracked = set(untracked.splitlines())
    if patch == baseline_patch and post_untracked == set(baseline_untracked):
        return None
    return {"root": root, "patch": patch, "index_patch": index_patch, "untracked": sorted(post_untracked - set(baseline_untracked))}


def apply_undo(record: dict | None) -> str:
    if not isinstance(record, dict):
        return "Undo unavailable: no checkpoint."
    root = record.get("root")
    patch = record.get("patch")
    index_patch = record.get("index_patch")
    untracked = record.get("untracked")
    if (
        not isinstance(root, str)
        or not isinstance(patch, str)
        or not isinstance(index_patch, str)
        or not isinstance(untracked, list)
        or not all(isinstance(path, str) for path in untracked)
    ):
        return "Undo unavailable: no checkpoint."
    if _git_root() != root:
        return "Undo unavailable: checkpoint is for a different repository."
    current_patch = _git(["diff", "--binary", "HEAD"])
    current_index_patch = _git(["diff", "--binary", "--cached", "HEAD"])
    current_untracked = _git(["ls-files", "--others", "--exclude-standard"])
    if current_patch is None or current_index_patch is None or current_untracked is None:
        return "Undo unavailable: no checkpoint."
    if current_patch != patch or current_index_patch != index_patch or set(current_untracked.splitlines()) != set(untracked):
        return "Undo refused: worktree changed since the turn."
    if _git(["apply", "--reverse", "--binary"], patch) is None:
        return "Undo failed: patch could not be applied."
    if index_patch and _git(["apply", "--reverse", "--binary", "--cached"], index_patch) is None:
        return "Undo failed: patch could not be applied."
    root_path = Path(root).resolve()
    for name in untracked:
        path = root_path / name
        try:
            path.resolve().relative_to(root_path)
        except ValueError:
            continue
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
            parent = path.parent
            while parent != root_path:
                parent.rmdir()
                parent = parent.parent
        except OSError:
            continue
    return "Undo complete."


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


def status_line(total_tokens: int | str, context_tokens: int | None, model: str = MODEL, activity: str = "") -> str:
    parts = ["k", str(Path.cwd())]
    if branch := git_branch():
        parts.append(branch)
    context = context_tokens if context_tokens is not None else "?"
    parts.extend([f"model: {model}", f"tokens: {total_tokens}", f"context: {context}"])
    if activity:
        parts.append(activity)
    return " | ".join(parts)


def render(kind: str, text: str) -> None:
    is_tty = sys.stdout.isatty()
    if kind == "tool":
        if is_tty:
            sys.stdout.write(f"  {color('tool')}{text}{RESET}\n")
        else:
            sys.stdout.write(f"  {text}\n")
    elif kind == "output":
        if is_tty:
            sys.stdout.write(f"{color('error')}{text}{RESET}\n")
        else:
            sys.stdout.write(f"{text}\n")
    elif kind == "status":
        if is_tty:
            lines = [
                f"\033[7m{color('accent')} k {RESET}{color('muted')}{line[1:]}{RESET}" if line.startswith("k") else f"{color('muted')}{line}{RESET}"
                for line in text.splitlines()
            ]
            sys.stdout.write("\n".join(lines) + "\n")
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


def handle_skill_command(text: str, session: dict) -> str | None:
    if text == "/skills":
        names = ", ".join(available_skills()) or "(none)"
        return f"Available skills: {names}"
    if not text.startswith("/skill "):
        return None
    name = text.removeprefix("/skill ").strip()
    if name == "clear":
        session["skills"] = []
        return "Cleared skills"
    if not name or "/" in name or "\\" in name:
        return "Invalid skill name"
    if name not in available_skills():
        return f"Unknown skill: {name}"
    active = session.setdefault("skills", [])
    if name in active:
        return f"Skill already loaded: {name}"
    active.append(name)
    return f"Loaded skill: {name}"


def handle_command(text: str) -> str | None:
    if not text.startswith("/"):
        return None
    if text == "/quit":
        return "quit"
    return f"Unknown command: {text.split()[0]}"


def run_agent(
    task: str,
    client: OpenAI,
    history: list[object] | None = None,
    session_id: str | None = None,
    skills: list[str] | None = None,
    *,
    model: str = MODEL,
    confirm_dangerous: object = None,
    on_activity: object = None,
) -> tuple[str, int, int | None, list[object]]:
    instructions, missing = skill_instructions(skills or [])
    for name in missing:
        render("output", f"Warning: skill '{name}' not found, skipping")
    if API_MODE == "responses":
        return run_responses_agent(task, client, instructions, history, session_id, model=model, confirm_dangerous=confirm_dangerous, on_activity=on_activity)
    if API_MODE == "chat-completions":
        return run_chat_agent(task, client, instructions, history, session_id, model=model, confirm_dangerous=confirm_dangerous, on_activity=on_activity)
    raise RuntimeError("K_API_MODE must be 'responses' or 'chat-completions'")


def run_responses_agent(
    task: str,
    client: OpenAI,
    instructions: str,
    history: list[object] | None,
    session_id: str | None,
    *,
    model: str = MODEL,
    confirm_dangerous: object = None,
    on_activity: object = None,
) -> tuple[str, int, int | None, list[object]]:
    history = list(history or [])
    history.append({"role": "user", "content": task})
    total_tokens = 0
    context_tokens = None
    while True:
        if on_activity:
            on_activity("thinking")
        response = client.responses.create(model=model, instructions=instructions, input=list(history), tools=TOOLS)
        output = getattr(response, "output", None)
        if not isinstance(output, list):
            raise RuntimeError("provider response is malformed")
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        total_tokens += (input_tokens or 0) + (output_tokens or 0)
        if input_tokens is not None:
            context_tokens = input_tokens
        calls = [item for item in output if item.type == "function_call"]
        if any(not all(isinstance(getattr(call, field, None), str) for field in ("name", "arguments", "call_id")) for call in calls):
            raise RuntimeError("provider response is malformed")
        if not calls:
            history.extend(output)
            return response.output_text, total_tokens, context_tokens, history
        history.extend(output)
        for call in calls:
            if on_activity:
                on_activity(call.name)
        history.extend(
            dispatch_tool(call.name, call.arguments, call.call_id, session_id, confirm_dangerous=confirm_dangerous, on_activity=on_activity) for call in calls
        )
        for call in calls:
            render("tool", f"{call.name}({call.arguments})")


def run_chat_agent(
    task: str,
    client: OpenAI,
    instructions: str,
    history: list[object] | None,
    session_id: str | None,
    *,
    model: str = MODEL,
    confirm_dangerous: object = None,
    on_activity: object = None,
) -> tuple[str, int, int | None, list[object]]:
    history = list(history or [])
    # Older k versions persisted empty tool_calls arrays on ordinary assistant
    # messages. OpenAI-compatible providers can reject those histories.
    for item in history:
        if isinstance(item, dict) and item.get("role") == "assistant" and item.get("tool_calls") == []:
            item.pop("tool_calls")
    history.append({"role": "user", "content": task})
    total_tokens = 0
    context_tokens = None
    while True:
        if on_activity:
            on_activity("thinking")
        response = client.chat.completions.create(model=model, messages=[{"role": "system", "content": instructions}, *history], tools=CHAT_TOOLS)
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("provider response is malformed")
        message = getattr(choices[0], "message", None)
        calls = getattr(message, "tool_calls", None)
        if message is None or (calls is not None and not isinstance(calls, list)):
            raise RuntimeError("provider response is malformed")
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        total_tokens += (input_tokens or 0) + (output_tokens or 0)
        if input_tokens is not None:
            context_tokens = input_tokens
        calls = calls or []
        if any(
            not isinstance(getattr(call, "id", None), str)
            or not all(isinstance(getattr(getattr(call, "function", None), field, None), str) for field in ("name", "arguments"))
            for call in calls
        ):
            raise RuntimeError("provider response is malformed")
        assistant_message = {"role": "assistant", "content": getattr(message, "content", None)}
        if calls:
            assistant_message["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}} for call in calls
            ]
        history.append(assistant_message)
        if not calls:
            return message.content or "", total_tokens, context_tokens, history
        for call in calls:
            if on_activity:
                on_activity(call.function.name)
            result = dispatch_tool(
                call.function.name, call.function.arguments, call.id, session_id, confirm_dangerous=confirm_dangerous, on_activity=on_activity
            )
            history.append({"role": "tool", "tool_call_id": call.id, "content": result["output"]})
            render("tool", f"{call.function.name}({call.function.arguments})")


def configure_multiline_input() -> None:
    """Make Ctrl+J insert a newline in an interactive readline prompt."""
    readline.parse_and_bind(MULTILINE_INPUT_BINDING)


def main() -> None:
    parser = argparse.ArgumentParser(prog="k")
    parser.add_argument("--resume", metavar="ID")
    parser.add_argument("--theme", choices=PALETTES, default=os.getenv("K_THEME", "light"))
    args, _ = parser.parse_known_args()
    set_theme(args.theme)
    session = {"id": new_session_id(), "history": [], "transcript": [], "skills": [], "api_mode": API_MODE, "model": MODEL, "title": ""}
    if args.resume:
        try:
            session = load_session(args.resume)
            if session.get("api_mode", "responses") != API_MODE:
                raise ValueError("cannot resume a session created with a different K_API_MODE")
        except ValueError as error:
            render("output", f"Error: {error}")
    ensure_session_defaults(session)
    client = OpenAI()

    def confirm_dangerous(command: str) -> bool:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return False
        return input("Allow dangerous command? [y/N] ").strip().lower() == "y"

    def on_activity(activity: str) -> None:
        render_pinned_status(status_line("?", None, session["model"], activity))

    try:
        if sys.stdout.isatty():
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
        render_pinned_status(status_line("?", None, session["model"]), move_cursor=True)
        configure_multiline_input()
        while True:
            try:
                task = input("\u276f ").strip()
            except EOFError:
                return
            if not task:
                return
            skill_result = handle_skill_command(task, session)
            if skill_result:
                render("output", skill_result)
                save_session(session)
                render_pinned_status(status_line("?", None, session["model"]))
                continue
            if task == "/quit":
                return
            if task.startswith("/"):
                parts = task.split()
                command = parts[0]
                if command == "/resume":
                    if len(parts) == 1:
                        render("output", format_session_list(list_sessions()))
                    elif len(parts) == 2:
                        try:
                            resumed = load_session(parts[1])
                            if resumed.get("api_mode", "responses") != API_MODE:
                                raise ValueError("cannot resume a session created with a different K_API_MODE")
                        except ValueError as error:
                            render("output", f"Error: {error}")
                        else:
                            session = resumed
                            session["cwd"] = str(Path.cwd())
                            save_session(session)
                            render("output", f"Resumed {session['id']}")
                            render_pinned_status(status_line("?", None, session["model"]))
                    else:
                        render("output", "Usage: /resume [session-id]")
                    continue
                if command == "/model":
                    model = task.removeprefix("/model").strip()
                    if not model:
                        render("output", "Usage: /model <model-name>")
                    else:
                        session["model"] = model
                        save_session(session)
                        render("output", f"Model set to {model}")
                        render_pinned_status(status_line("?", None, session["model"]))
                    continue
                if task == "/undo":
                    result = apply_undo(session.get("undo"))
                    if result == "Undo complete.":
                        session["undo"] = None
                        save_session(session)
                    render("output", result)
                    render_pinned_status(status_line("?", None, session["model"]))
                    continue
                render("output", f"Unknown command: {command}")
                continue
            if not session["title"]:
                session["title"] = " ".join(task.split())[:72]
            checkpoint = begin_undo_checkpoint()
            try:
                answer, total_tokens, context_tokens, history = run_agent(
                    task,
                    client,
                    session["history"],
                    session["id"],
                    session.get("skills", []),
                    model=session["model"],
                    confirm_dangerous=confirm_dangerous,
                    on_activity=on_activity,
                )
                session["history"] = history
                session["transcript"].append({"prompt": task, "answer": answer})
                session["undo"] = finish_undo_checkpoint(checkpoint)
                save_session(session)
            except Exception as e:
                render("output", f"Error: {e}")
                render_pinned_status(status_line("?", None, session["model"]))
                continue
            render("answer", answer)
            render_pinned_status(status_line(total_tokens, context_tokens, session["model"]))
    except KeyboardInterrupt:
        return
    finally:
        restore_terminal()
        save_session(session)
        render("output", f"To resume this session: {sys.argv[0]} --resume {session['id']}")


if __name__ == "__main__":
    main()
