import argparse
import base64
import copy
import difflib
import fnmatch
import json
import re
import readline
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
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
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

MAX_TEXT_FILE_BYTES = 1_000_000
MAX_IMAGE_FILE_BYTES = 5_000_000
DEFAULT_BASH_TIMEOUT = 60
MAX_AGENT_TOOL_TURNS = 20
MAX_HISTORY_ITEMS = 200
GREP_IGNORED_DIRS = {".git", ".venv", "node_modules", "dist", "__pycache__"}
MAX_GREP_FILES = 2_000
MAX_GREP_MATCHES = 2_000
PROJECT_INSTRUCTIONS_MAX_BYTES = 16 * 1024
PROJECT_INSTRUCTIONS_TOTAL_BYTES = 48 * 1024
READ_STAMPS: dict[Path, tuple[int, int]] = {}


def load_config() -> dict[str, object]:
    path = Path.home() / ".k" / "config.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    defaults = data.get("defaults", {})
    presets = data.get("presets", {})
    if not isinstance(defaults, dict) or not isinstance(presets, dict):
        return {}
    valid_presets = {name: values for name, values in presets.items() if isinstance(name, str) and isinstance(values, dict)}
    return {"defaults": defaults, "presets": valid_presets}


def project_path(path: str) -> Path:
    root = Path.cwd().resolve()
    target = Path(path).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError("path is outside the project") from None
    return target


def file_stamp(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def display_project_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def read(path: str, offset: int = 1, limit: int = 2000) -> str | list[dict[str, str]]:
    if offset < 1:
        return "Error: offset must be at least 1"
    if limit < 1:
        return "Error: limit must be at least 1"
    try:
        target = project_path(path)
        if target.suffix.lower() in IMAGE_TYPES:
            if target.stat().st_size > MAX_IMAGE_FILE_BYTES:
                return f"Error: image exceeds {MAX_IMAGE_FILE_BYTES}-byte limit"
            data = base64.b64encode(target.read_bytes()).decode()
            return [
                {
                    "type": "input_image",
                    "image_url": f"data:{IMAGE_TYPES[target.suffix.lower()]};base64,{data}",
                }
            ]
        if target.stat().st_size > MAX_TEXT_FILE_BYTES:
            return f"Error: file exceeds {MAX_TEXT_FILE_BYTES}-byte limit"
        text = target.read_text(encoding="utf-8", errors="replace")
        READ_STAMPS[target] = file_stamp(target)
        lines = text.splitlines()
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
        target = project_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        READ_STAMPS.pop(target, None)
        return f"Wrote {target}"
    except (OSError, ValueError) as error:
        return f"Error: cannot write {path}: {error}"


def edit(path: str, oldText: str, newText: str) -> str:
    try:
        target = project_path(path)
        recorded = READ_STAMPS.get(target)
        if recorded is not None and file_stamp(target) != recorded:
            return "Error: file changed since read; re-read before editing"
        # surrogateescape keeps invalid UTF-8 and original line endings byte for byte.
        content = target.read_bytes().decode("utf-8", "surrogateescape")
        # read() hides CR from the model, so match its LF text to a CRLF file.
        if "\r\n" in content and "\r" not in oldText + newText:
            oldText = oldText.replace("\n", "\r\n")
            newText = newText.replace("\n", "\r\n")
        if content.count(oldText) != 1:
            return "Error: oldText must occur exactly once"
        updated = content.replace(oldText, newText, 1)
        target.write_bytes(updated.encode("utf-8", "surrogateescape"))
        READ_STAMPS.pop(target, None)
        label = display_project_path(target)
        diff = "".join(
            difflib.unified_diff(
                content.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{label}",
                tofile=f"b/{label}",
            )
        )
        # Lone surrogates cannot be sent to the provider as JSON.
        return diff.encode("utf-8", "replace").decode("utf-8")
    except (OSError, ValueError) as error:
        return f"Error: cannot edit {path}: {error}"


def grep(pattern: str, include: str = "*") -> str:
    try:
        matcher = re.compile(pattern)
    except re.error as error:
        return f"Error: invalid regex: {error}"

    matches = []
    files = 0
    root = Path.cwd().resolve()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in GREP_IGNORED_DIRS for part in relative.parts):
            continue
        if not path.is_file() or not fnmatch.fnmatch(path.name, include):
            continue
        files += 1
        if files > MAX_GREP_FILES:
            return "\n".join(matches + ["[truncated: file limit reached]"])
        try:
            project_path(str(path))
            if path.stat().st_size > MAX_TEXT_FILE_BYTES:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, ValueError):
            continue
        for number, line in enumerate(lines, 1):
            if matcher.search(line):
                matches.append(f"{relative}:{number}: {line}")
                if len(matches) >= MAX_GREP_MATCHES:
                    return "\n".join(matches + ["[truncated: match limit reached]"])
    return "\n".join(matches) or "No matches."


def stop_process_group(process: subprocess.Popen) -> str:
    """Stop the shell and its children, because children keep stdout open."""
    for stop_signal in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, stop_signal)
        except OSError:
            pass
        try:
            return process.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            continue
    return ""  # A child left the process group and still holds stdout.


def bash(command: str, timeout: int | None = None) -> str:
    effective_timeout = DEFAULT_BASH_TIMEOUT if timeout is None else timeout
    if not isinstance(effective_timeout, int) or not 1 <= effective_timeout <= DEFAULT_BASH_TIMEOUT:
        return f"Error: timeout must be between 1 and {DEFAULT_BASH_TIMEOUT}s"
    process = None
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=os.getcwd(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, _ = process.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            output = stop_process_group(process)
            return f"Error: command timed out after {effective_timeout}s\n{output}".rstrip()
        except KeyboardInterrupt:
            stop_process_group(process)
            return "Error: command interrupted"
        if process.returncode != 0:
            return f"[exit {process.returncode}]\n{stdout}"
        return stdout
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
                    "type": ["integer", "null"],
                    "description": "Line number to start reading from (1-indexed). Null means 1.",
                },
                "limit": {
                    "type": ["integer", "null"],
                    "description": "Maximum number of lines to read. Null means 2000.",
                },
            },
            "required": ["path", "offset", "limit"],
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
        "name": "grep",
        "description": "Search project text files with a regular expression. Skips generated and dependency directories.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression to search for"},
                "include": {"type": ["string", "null"], "description": "Filename glob, such as *.py. Null means all files."},
            },
            "required": ["pattern", "include"],
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
                    "type": ["integer", "null"],
                    "description": "Timeout in seconds. Null means 60.",
                },
            },
            "required": ["command", "timeout"],
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
    "grep": grep,
    "bash": bash,
}

MAX_TOOL_OUTPUT_CHARS = 12_000

USER_SKILLS_DIR = Path.home() / ".agents" / "skills"


def project_instruction_paths() -> list[Path]:
    cwd = Path.cwd().resolve()
    paths = [Path.home() / ".k" / "AGENTS.md"]
    root_text = _git_root()
    if root_text:
        root = Path(root_text).resolve()
        if cwd == root or root in cwd.parents:
            paths.extend(root / relative / "AGENTS.md" for relative in reversed(cwd.relative_to(root).parents))
            paths.append(cwd / "AGENTS.md")
            return list(dict.fromkeys(paths))
    paths.append(cwd / "AGENTS.md")
    return list(dict.fromkeys(paths))


def project_instructions() -> str:
    blocks = []
    total = 0
    for path in project_instruction_paths():
        try:
            if not path.is_file() or path.stat().st_size > PROJECT_INSTRUCTIONS_MAX_BYTES:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        block = f"\n## {path}\n\n{content}"
        size = len(block.encode())
        if total + size > PROJECT_INSTRUCTIONS_TOTAL_BYTES:
            break
        blocks.append(block)
        total += size
    return "\n\n## Project instructions\n" + "".join(blocks) if blocks else ""


def available_skills() -> dict[str, Path]:
    return {path.parent.name: path for path in USER_SKILLS_DIR.rglob("SKILL.md")}


def skill_instructions(names: list[str]) -> tuple[str, list[str]]:
    skills = available_skills()
    missing = [name for name in names if name not in skills]
    active = [f"### {name}\n{skills[name].read_text(encoding='utf-8')}" for name in names if name in skills]
    suffix = "\n\n## Active skills\n\n" + "\n\n".join(active) if active else ""
    return SYSTEM_PROMPT + project_instructions() + suffix, missing


RESET = "\033[0m"
PALETTES = {
    "light": {
        "text": "#1f2328",
        "accent": "#5a8080",
        "muted": "#6c6c6c",
        "tool": "#547da7",
        "error": "#aa5555",
        "info": "#4f8f4f",
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
        "info": "#7fd787",
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
    path = k_dir() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def new_session_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid4().hex[:6]}"


def new_session_from(session: dict) -> dict:
    return {
        "id": new_session_id(),
        "history": [],
        "transcript": [],
        "skills": list(session.get("skills", [])),
        "api_mode": session.get("api_mode", API_MODE),
        "permission_mode": session.get("permission_mode", "review"),
        "display_mode": session.get("display_mode", "formatted"),
        "model": session.get("model", MODEL),
        "title": "",
        "forked_from": None,
        "undo": None,
    }


def fork_session(session: dict) -> dict:
    fork = new_session_from(session)
    fork["history"] = copy.deepcopy(session.get("history", []))
    fork["transcript"] = copy.deepcopy(session.get("transcript", []))
    fork["forked_from"] = session.get("id")
    return fork


def compact_session(session: dict, client: OpenAI, focus: str = "") -> str:
    prompt = "Summarize this coding session into Goal, Constraints, Progress, Files, and Next Steps. Return only the summary."
    if focus:
        prompt += f"\nFocus: {focus}"
    history = list(session.get("history", [])) + [{"role": "user", "content": prompt}]
    if session.get("api_mode", API_MODE) == "responses":
        response = client.responses.create(model=session["model"], instructions=SYSTEM_PROMPT, input=_trim_history(history))
        summary = response.output_text
    else:
        response = client.chat.completions.create(
            model=session["model"],
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *history],
        )
        summary = response.choices[0].message.content
    if not summary:
        raise RuntimeError("provider returned an empty summary")
    session["history"] = [{"role": "user", "content": "[compacted]"}, {"role": "assistant", "content": summary}]
    return summary


def session_path(session_id: str) -> Path:
    if "/" in session_id or "\\" in session_id or session_id in {"", ".", ".."}:
        raise ValueError("invalid session id")
    return sessions_dir() / f"{session_id}.json"


def ensure_session_defaults(session: dict) -> dict:
    session.setdefault("skills", [])
    session.setdefault("api_mode", API_MODE)
    session.setdefault("permission_mode", "review")
    session.setdefault("display_mode", "formatted")
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


def load_resume_session(session_id: str, api_mode: str | None = None) -> dict:
    """Load a session and validate that its history format matches the active API mode."""
    session = load_session(session_id)
    active_mode = api_mode or API_MODE
    if session.get("api_mode", "responses") != active_mode:
        raise ValueError(f"cannot resume a {session.get('api_mode', 'responses')} session in {active_mode} mode")
    return session


def write_private_text(path: Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def save_session(session: dict) -> None:
    ensure_session_defaults(session)
    if not session["cwd"]:
        session["cwd"] = str(Path.cwd())
    session["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_private_text(session_path(session["id"]), json.dumps(session, indent=2, default=lambda value: value.to_dict()))


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
        write_private_text(artifact, text)
        saved = f"\nFull output saved: {artifact}"
    return f"[context-mode: output compacted from {len(text)} chars]\n{head}\n...\n{tail}{saved}"


SYSTEM_PROMPT = """You are an expert coding assistant. You help users with coding tasks by reading files, executing commands, editing code, and writing new files.

Available tools:
- read: Read file contents
- grep: Search project files with a regular expression
- bash: Execute bash commands
- edit: Make surgical edits to files
- write: Create or overwrite files

Guidelines:
- Use grep to search file contents
- Use bash for file operations like ls, find
- Use read to examine files before editing
- Use edit for precise changes (old text must match exactly)
- Use write only for new files or complete rewrites
- When summarizing your actions, output plain text directly - do NOT use cat or bash to display what you did
- Be concise in your responses
- Show file paths clearly when working with files"""


def dispatch_tool(name: str, arguments: str, call_id: str, session_id: str | None = None, *, confirm_bash: Callable[[str], bool] | None = None, on_activity: Callable[[str], None] | None = None) -> dict:
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError as e:
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": f"Error: invalid JSON in arguments: {e}",
        }
    command = args.get("command") if isinstance(args, dict) else None
    if name == "bash":
        if not isinstance(command, str) or confirm_bash is None or not confirm_bash(command):
            return {
                "type": "function_call_output",
                "call_id": call_id,
                "output": "Error: bash command rejected by user",
            }
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": f"Error: Unknown tool: {name}",
        }
    if on_activity:
        on_activity(name)
    try:
        # Strict schemas send null for an unused optional argument.
        result = handler(**{key: value for key, value in args.items() if value is not None})
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


def _git(args: list[str], data: str | None = None, cwd: str | None = None) -> str | None:
    # Bytes mode with surrogateescape keeps CRLF and non-UTF-8 patch content intact.
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or Path.cwd(),
            input=None if data is None else data.encode("utf-8", "surrogateescape"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    return result.stdout.decode("utf-8", "surrogateescape") if result.returncode == 0 else None


def _git_root() -> str | None:
    root = _git(["rev-parse", "--show-toplevel"])
    return str(Path(root.strip()).resolve()) if root else None


def begin_undo_checkpoint() -> dict | None:
    root = _git_root()
    if root is None or _git(["status", "--porcelain"], cwd=root) != "":
        return None
    patch = _git(["diff", "--binary", "HEAD"], cwd=root)
    untracked = _git(["ls-files", "--others", "--exclude-standard"], cwd=root)
    if patch is None or untracked is None:
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
    patch = _git(["diff", "--binary", "HEAD"], cwd=root)
    index_patch = _git(["diff", "--binary", "--cached", "HEAD"], cwd=root)
    untracked = _git(["ls-files", "--others", "--exclude-standard"], cwd=root)
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
    current_patch = _git(["diff", "--binary", "HEAD"], cwd=root)
    current_index_patch = _git(["diff", "--binary", "--cached", "HEAD"], cwd=root)
    current_untracked = _git(["ls-files", "--others", "--exclude-standard"], cwd=root)
    if current_patch is None or current_index_patch is None or current_untracked is None:
        return "Undo unavailable: no checkpoint."
    if current_patch != patch or current_index_patch != index_patch or set(current_untracked.splitlines()) != set(untracked):
        return "Undo refused: worktree changed since the turn."
    if patch and _git(["apply", "--reverse", "--binary"], patch, cwd=root) is None:
        return "Undo failed: patch could not be applied."
    if index_patch and _git(["apply", "--reverse", "--binary", "--cached"], index_patch, cwd=root) is None:
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
    branch = _git(["branch", "--show-current"])
    return branch.strip() or None if branch else None


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
    elif kind == "info":
        if is_tty:
            sys.stdout.write(f"{color('info')}{text}{RESET}\n")
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


def validate_permission_mode(mode: object) -> str:
    if mode not in {"review", "yolo"}:
        raise ValueError("permission_mode must be 'review' or 'yolo'")
    return str(mode)


def validate_display_mode(mode: object) -> str:
    if mode not in {"formatted", "stream"}:
        raise ValueError("display must be 'formatted' or 'stream'")
    return str(mode)


def confirm_bash(command: str, mode: str) -> bool:
    if validate_permission_mode(mode) == "yolo":
        return True
    sys.stdout.write(f"\n{color('heading')}Allow bash command?{color('text')}\n{color('tool')}$ {command}{color('text')}\n")
    sys.stdout.flush()
    return input("[y/N] ").strip().lower() == "y"


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
    skills = available_skills()
    if text == "/skills":
        names = ", ".join(skills) or "(none)"
        return f"Available skills: {names}"
    if not text.startswith("/skill "):
        return None
    name = text.removeprefix("/skill ").strip()
    if name == "clear":
        session["skills"] = []
        return "Cleared skills"
    if not name or "/" in name or "\\" in name:
        return "Invalid skill name"
    if name not in skills:
        return f"Unknown skill: {name}"
    active = session.setdefault("skills", [])
    if name in active:
        return f"Skill already loaded: {name}"
    active.append(name)
    return f"Loaded skill: {name}"


COMMAND_HELP = (
    ("/help", "/help", "Show this command list"),
    ("/compact", "/compact [focus]", "Summarize the current session"),
    ("/display", "/display <formatted|stream>", "Set response display mode"),
    ("/fork", "/fork", "Fork the current session"),
    ("/mode", "/mode <review|yolo>", "Set bash permission mode"),
    ("/model", "/model <model-name>", "Set the session model"),
    ("/new", "/new", "Start a fresh session"),
    ("/preset", "/preset <name>", "Select a configured provider preset"),
    ("/quit", "/quit", "Exit k"),
    ("/resume", "/resume [session-id]", "List or resume a session"),
    ("/skills", "/skills", "List available skills"),
    ("/skill", "/skill <name|clear>", "Load or clear a skill"),
    ("/undo", "/undo", "Undo the last completed turn"),
)


def command_help() -> str:
    width = max(len(usage) for _, usage, _ in COMMAND_HELP)
    return "Available commands:\n" + "\n".join(f"  {usage:<{width}}  {description}" for _, usage, description in COMMAND_HELP)


class StreamAssembler:
    def __init__(self) -> None:
        self._text_parts: list[str] = []
        self._calls: dict[int, dict[str, str]] = {}

    def consume(self, event: dict[str, object]) -> None:
        kind = event.get("type")
        if kind == "text_delta":
            self._text_parts.append(str(event.get("text", "")))
        elif kind == "tool_start":
            index = int(event.get("index", 0))
            # Some providers repeat the id or name in later deltas, so keep the arguments.
            call = self._calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            call["id"] = str(event.get("id") or call["id"])
            call["name"] = str(event.get("name") or call["name"])
        elif kind == "tool_arguments_delta":
            index = int(event.get("index", 0))
            call = self._calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            call["arguments"] += str(event.get("arguments", ""))

    def text(self) -> str:
        return "".join(self._text_parts)

    def tool_calls(self) -> list[dict[str, str]]:
        return [{**self._calls[index], "id": self._calls[index]["id"] or f"call_{uuid4().hex}"} for index in sorted(self._calls)]


def run_agent(
    task: str,
    client: OpenAI,
    history: list[object] | None = None,
    session_id: str | None = None,
    skills: list[str] | None = None,
    *,
    model: str = MODEL,
    confirm_bash: Callable[[str], bool] | None = None,
    on_activity: Callable[[str], None] | None = None,
    quiet: bool = False,
    api_mode: str | None = None,
    stream: bool = False,
    on_text: Callable[[str], None] | None = None,
) -> tuple[str, int, int | None, list[object]]:
    instructions, missing = skill_instructions(skills or [])
    selected_api_mode = api_mode or API_MODE
    for name in missing:
        if not quiet:
            render("info", f"Warning: skill '{name}' not found, skipping")
    if selected_api_mode == "responses":
        return run_responses_agent(
            task, client, instructions, history, session_id, model=model, confirm_bash=confirm_bash, on_activity=on_activity, quiet=quiet, stream=stream, on_text=on_text
        )
    if selected_api_mode == "chat-completions":
        return run_chat_agent(
            task, client, instructions, history, session_id, model=model, confirm_bash=confirm_bash, on_activity=on_activity, quiet=quiet, stream=stream, on_text=on_text
        )
    raise RuntimeError("K_API_MODE must be 'responses' or 'chat-completions'")


def _trim_history(history: list) -> list:
    trimmed = history[-MAX_HISTORY_ITEMS:]

    # Also runs on short histories, to repair sessions saved with a leading orphan.
    def is_orphan(item: object) -> bool:
        if isinstance(item, dict):
            return item.get("role") == "tool" or item.get("type") in {"function_call", "function_call_output"}
        return getattr(item, "type", None) == "function_call"

    while trimmed and is_orphan(trimmed[0]):
        trimmed = trimmed[1:]
    return trimmed


def _stream_responses_turn(
    client: OpenAI, model: str, instructions: str, history: list[object], on_text: Callable[[str], None] | None = None
) -> tuple[str, list[dict[str, str]]]:
    assembler = StreamAssembler()
    events = client.responses.create(model=model, instructions=instructions, input=list(_trim_history(history)), tools=TOOLS, stream=True)
    for event in events:
        kind = getattr(event, "type", "")
        if kind == "response.output_text.delta":
            delta = getattr(event, "delta", "")
            assembler.consume({"type": "text_delta", "text": delta})
            if on_text:
                on_text(delta)
        elif kind == "response.output_item.added":
            item = getattr(event, "item", None)
            if getattr(item, "type", "") == "function_call":
                assembler.consume(
                    {"type": "tool_start", "index": getattr(event, "output_index", 0), "id": getattr(item, "call_id", ""), "name": getattr(item, "name", "")}
                )
        elif kind == "response.function_call_arguments.delta":
            assembler.consume({"type": "tool_arguments_delta", "index": getattr(event, "output_index", 0), "arguments": getattr(event, "delta", "")})
    return assembler.text(), assembler.tool_calls()


def _stream_chat_turn(
    client: OpenAI, model: str, instructions: str, history: list[object], on_text: Callable[[str], None] | None = None
) -> tuple[str, list[dict[str, str]]]:
    assembler = StreamAssembler()
    stream_response = client.chat.completions.create(
        model=model, messages=[{"role": "system", "content": instructions}, *_trim_history(history)], tools=CHAT_TOOLS, stream=True
    )
    for chunk in stream_response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        text = getattr(delta, "content", None)
        if text:
            assembler.consume({"type": "text_delta", "text": text})
            if on_text:
                on_text(text)
        for call in getattr(delta, "tool_calls", None) or []:
            index = getattr(call, "index", 0)
            function = getattr(call, "function", None)
            if getattr(call, "id", None) or getattr(function, "name", None):
                assembler.consume({"type": "tool_start", "index": index, "id": getattr(call, "id", ""), "name": getattr(function, "name", "")})
            if getattr(function, "arguments", None):
                assembler.consume({"type": "tool_arguments_delta", "index": index, "arguments": function.arguments})
    return assembler.text(), assembler.tool_calls()


def run_responses_agent(
    task: str,
    client: OpenAI,
    instructions: str,
    history: list[object] | None,
    session_id: str | None,
    *,
    model: str = MODEL,
    confirm_bash: Callable[[str], bool] | None = None,
    on_activity: Callable[[str], None] | None = None,
    quiet: bool = False,
    stream: bool = False,
    on_text: Callable[[str], None] | None = None,
) -> tuple[str, int, int | None, list[object]]:
    history = list(history or [])
    history.append({"role": "user", "content": task})
    total_tokens = 0
    context_tokens = None
    tool_turns = 0
    while True:
        if on_activity:
            on_activity("thinking")
        if stream:
            text, streamed_calls = _stream_responses_turn(client, model, instructions, history, on_text)
            if not streamed_calls:
                history.append({"role": "assistant", "content": text})
                return text, total_tokens, context_tokens, _trim_history(history)
            tool_turns += 1
            if tool_turns > MAX_AGENT_TOOL_TURNS:
                raise RuntimeError(f"agent exceeded {MAX_AGENT_TOOL_TURNS} tool turns")
            for call in streamed_calls:
                history.append({"type": "function_call", "call_id": call["id"], "name": call["name"], "arguments": call["arguments"]})
                history.append(dispatch_tool(call["name"], call["arguments"], call["id"], session_id, confirm_bash=confirm_bash, on_activity=on_activity))
            continue
        response = client.responses.create(model=model, instructions=instructions, input=list(_trim_history(history)), tools=TOOLS)
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
            return response.output_text, total_tokens, context_tokens, _trim_history(history)
        tool_turns += 1
        if tool_turns > MAX_AGENT_TOOL_TURNS:
            raise RuntimeError(f"agent exceeded {MAX_AGENT_TOOL_TURNS} tool turns")
        history.extend(output)
        history.extend(dispatch_tool(call.name, call.arguments, call.call_id, session_id, confirm_bash=confirm_bash, on_activity=on_activity) for call in calls)
        if not quiet:
            for call in calls:
                render("tool", f"{call.name}({call.arguments})")


def _append_chat_results(history: list[object], results: list[dict]) -> None:
    # Chat tool messages hold only text. Images go in one user message after all
    # results, because tool messages must directly follow the assistant message.
    images = []
    for result in results:
        output = result["output"]
        if isinstance(output, list):
            images.extend({"type": "image_url", "image_url": {"url": part["image_url"]}} for part in output)
            output = "Image attached in the next message."
        history.append({"role": "tool", "tool_call_id": result["call_id"], "content": output})
    if images:
        history.append({"role": "user", "content": images})


def run_chat_agent(
    task: str,
    client: OpenAI,
    instructions: str,
    history: list[object] | None,
    session_id: str | None,
    *,
    model: str = MODEL,
    confirm_bash: Callable[[str], bool] | None = None,
    on_activity: Callable[[str], None] | None = None,
    quiet: bool = False,
    stream: bool = False,
    on_text: Callable[[str], None] | None = None,
) -> tuple[str, int, int | None, list[object]]:
    saved_history = list(history or [])
    history = []
    # Older k versions persisted empty tool_calls arrays on ordinary assistant
    # messages, and some providers returned assistant messages with neither
    # content nor tool calls. Both forms are invalid for Chat Completions.
    for item in saved_history:
        if isinstance(item, dict) and item.get("role") == "assistant":
            content = item.get("content")
            tool_calls = item.get("tool_calls")
            if not content and not tool_calls:
                continue
            if tool_calls == []:
                item = {key: value for key, value in item.items() if key != "tool_calls"}
        history.append(item)
    history.append({"role": "user", "content": task})
    total_tokens = 0
    context_tokens = None
    tool_turns = 0
    while True:
        if on_activity:
            on_activity("thinking")
        if stream:
            text, streamed_calls = _stream_chat_turn(client, model, instructions, history, on_text)
            if not streamed_calls:
                history.append({"role": "assistant", "content": text})
                return text, total_tokens, context_tokens, _trim_history(history)
            tool_turns += 1
            if tool_turns > MAX_AGENT_TOOL_TURNS:
                raise RuntimeError(f"agent exceeded {MAX_AGENT_TOOL_TURNS} tool turns")
            assistant_calls = [
                {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["arguments"]}} for call in streamed_calls
            ]
            history.append({"role": "assistant", "content": None, "tool_calls": assistant_calls})
            _append_chat_results(
                history,
                [dispatch_tool(call["name"], call["arguments"], call["id"], session_id, confirm_bash=confirm_bash, on_activity=on_activity) for call in streamed_calls],
            )
            continue
        response = client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": instructions}, *_trim_history(history)], tools=CHAT_TOOLS
        )
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
        content = getattr(message, "content", None)
        if not calls and not content:
            raise RuntimeError("provider response is malformed")
        assistant_message = {"role": "assistant", "content": content}
        if calls:
            assistant_message["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}} for call in calls
            ]
        history.append(assistant_message)
        if not calls:
            return content, total_tokens, context_tokens, _trim_history(history)
        tool_turns += 1
        if tool_turns > MAX_AGENT_TOOL_TURNS:
            raise RuntimeError(f"agent exceeded {MAX_AGENT_TOOL_TURNS} tool turns")
        _append_chat_results(
            history,
            [dispatch_tool(call.function.name, call.function.arguments, call.id, session_id, confirm_bash=confirm_bash, on_activity=on_activity) for call in calls],
        )
        if not quiet:
            for call in calls:
                render("tool", f"{call.function.name}({call.function.arguments})")


def configure_multiline_input() -> None:
    """Make Ctrl+J insert a newline in an interactive readline prompt."""
    readline.parse_and_bind(MULTILINE_INPUT_BINDING)


def main() -> None:
    parser = argparse.ArgumentParser(prog="k")
    parser.add_argument("--resume", metavar="ID")
    parser.add_argument("-p", "--print", dest="print_prompt", nargs="?", const="")
    parser.add_argument("--theme", choices=PALETTES, default=os.getenv("K_THEME", "light"))
    parser.add_argument("--mode", choices=("review", "yolo"), help="bash permission mode")
    parser.add_argument("--display", choices=("formatted", "stream"), help="response display mode")
    args, extra = parser.parse_known_args()
    set_theme(args.theme)
    config = load_config()
    defaults = config.get("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
    selected_model = os.getenv("OPENAI_MODEL_NAME") or defaults.get("model") or MODEL
    selected_api_mode = os.getenv("K_API_MODE") or defaults.get("api_mode") or API_MODE
    try:
        selected_permission_mode = validate_permission_mode(args.mode or defaults.get("permission_mode") or "review")
        selected_display_mode = validate_display_mode(args.display or defaults.get("display") or "formatted")
    except ValueError as error:
        parser.error(str(error))
    if selected_api_mode not in {"responses", "chat-completions"}:
        parser.error("api_mode must be 'responses' or 'chat-completions'")
    session = {"id": new_session_id(), "history": [], "transcript": []}
    is_resumed = False
    if args.resume:
        try:
            session = load_resume_session(args.resume, selected_api_mode)
            is_resumed = True
        except ValueError as error:
            render("output", f"Error: {error}")
    ensure_session_defaults(session)
    base_url = os.getenv("OPENAI_BASE_URL") or defaults.get("base_url")
    client = OpenAI(base_url=base_url) if isinstance(base_url, str) and base_url else OpenAI()
    if not is_resumed:
        session["model"] = selected_model
    session["api_mode"] = selected_api_mode
    session["permission_mode"] = selected_permission_mode
    session["display_mode"] = selected_display_mode

    if args.print_prompt is not None:
        prompt = args.print_prompt or " ".join(extra) or (sys.stdin.read() if not sys.stdin.isatty() else "")
        if not prompt.strip():
            parser.error("-p requires a prompt or piped stdin")
        answer, _, _, _ = run_agent(
            prompt,
            client,
            session["history"],
            session["id"],
            session.get("skills", []),
            model=session["model"],
            quiet=True,
            api_mode=session["api_mode"],
            confirm_bash=(lambda _: True) if args.mode == "yolo" else None,
        )
        print(answer)
        return

    def allow_bash(command: str) -> bool:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return args.mode == "yolo" and session["permission_mode"] == "yolo"
        return confirm_bash(command, session["permission_mode"])

    def on_activity(activity: str) -> None:
        render_pinned_status(status_line("?", None, session["model"], activity))

    def apply_setting(task: str, prefix: str, key: str, label: str, validate, usage: str) -> None:
        value = task.removeprefix(prefix).strip()
        if not value:
            render("info", f"{label}: {session[key]}")
            return
        try:
            session[key] = validate(value)
        except ValueError:
            render("output", usage)
        else:
            save_session(session)
            render("info", f"{label} set to {value}")

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
                render("info" if not skill_result.startswith(("Invalid", "Unknown")) else "output", skill_result)
                save_session(session)
                render_pinned_status(status_line("?", None, session["model"]))
                continue
            if task == "/quit":
                return
            if task.startswith("/"):
                parts = task.split()
                command = parts[0]
                if command == "/help":
                    render("info", command_help())
                    continue
                if command == "/resume":
                    if len(parts) == 1:
                        render("info", format_session_list(list_sessions()))
                    elif len(parts) == 2:
                        try:
                            resumed = load_resume_session(parts[1], session["api_mode"])
                        except ValueError as error:
                            render("output", f"Error: {error}")
                        else:
                            # A saved session must not turn off shell approval.
                            resumed["permission_mode"] = session["permission_mode"]
                            session = resumed
                            session["cwd"] = str(Path.cwd())
                            save_session(session)
                            render("info", f"Resumed {session['id']}")
                            render_pinned_status(status_line("?", None, session["model"]))
                    else:
                        render("output", "Usage: /resume [session-id]")
                    continue
                if command == "/mode":
                    apply_setting(task, "/mode", "permission_mode", "Permission mode", validate_permission_mode, "Usage: /mode <review|yolo>")
                    continue
                if command == "/display":
                    apply_setting(task, "/display", "display_mode", "Display mode", validate_display_mode, "Usage: /display <formatted|stream>")
                    continue
                if command == "/model":
                    model = task.removeprefix("/model").strip()
                    if not model:
                        render("output", "Usage: /model <model-name>")
                    else:
                        session["model"] = model
                        save_session(session)
                        render("info", f"Model set to {model}")
                        render_pinned_status(status_line("?", None, session["model"]))
                    continue
                if command == "/preset":
                    name = task.removeprefix("/preset").strip()
                    presets = config.get("presets", {})
                    preset = presets.get(name) if isinstance(presets, dict) else None
                    if not name or not isinstance(preset, dict):
                        render("output", "Usage: /preset <name>" if not name else f"Unknown preset: {name}")
                    elif preset.get("api_mode", session.get("api_mode")) not in {"responses", "chat-completions"}:
                        render("output", "Preset api_mode must be 'responses' or 'chat-completions'")
                    elif session["history"] and preset.get("api_mode", session["api_mode"]) != session["api_mode"]:
                        render("output", "Preset uses a different API mode, which cannot read this history. Use /new first.")
                    else:
                        session["model"] = preset.get("model", session["model"])
                        session["api_mode"] = preset.get("api_mode", session.get("api_mode", API_MODE))
                        save_session(session)
                        render("info", f"Preset set to {name}")
                    continue
                if command == "/new":
                    session = new_session_from(session)
                    save_session(session)
                    render("info", f"Started {session['id']}")
                    continue
                if command == "/fork":
                    session = fork_session(session)
                    save_session(session)
                    render("info", f"Forked {session['id']}")
                    continue
                if command == "/compact":
                    try:
                        summary = compact_session(session, client, task.removeprefix("/compact").strip())
                        session["transcript"].append({"prompt": "/compact", "answer": summary})
                        save_session(session)
                        render("answer", summary)
                    except Exception as error:
                        render("output", f"Error: {error}")
                    continue
                if task == "/undo":
                    result = apply_undo(session.get("undo"))
                    if result == "Undo complete.":
                        session["undo"] = None
                        save_session(session)
                        render("info", result)
                    else:
                        render("output", result)
                    render_pinned_status(status_line("?", None, session["model"]))
                    continue
                render("output", f"Unknown command: {command}")
                continue
            if not session["title"]:
                session["title"] = " ".join(task.split())[:72]
            checkpoint = begin_undo_checkpoint()
            streaming = session["display_mode"] == "stream"
            stream_state = [False]

            def on_stream_text(text: str) -> None:
                if not stream_state[0]:
                    sys.stdout.write(f"\n{color('accent')}│ {color('text')}")
                    stream_state[0] = True
                sys.stdout.write(text)
                sys.stdout.flush()

            try:
                answer, total_tokens, context_tokens, history = run_agent(
                    task,
                    client,
                    session["history"],
                    session["id"],
                    session.get("skills", []),
                    model=session["model"],
                    confirm_bash=allow_bash,
                    api_mode=session.get("api_mode"),
                    on_activity=on_activity,
                    stream=streaming,
                    on_text=on_stream_text if streaming else None,
                )
                if streaming and stream_state[0]:
                    sys.stdout.write(f"{RESET}\n")
                    sys.stdout.flush()
                session["history"] = history
                session["transcript"].append({"prompt": task, "answer": answer})
                session["undo"] = finish_undo_checkpoint(checkpoint)
                save_session(session)
            except Exception as e:
                render("output", f"Error: {e}")
                render_pinned_status(status_line("?", None, session["model"]))
                continue
            if not streaming:
                render("answer", answer)
            render_pinned_status(status_line(total_tokens, context_tokens, session["model"]))
    except KeyboardInterrupt:
        return
    finally:
        restore_terminal()
        save_session(session)
        render("info", f"To resume this session: {sys.argv[0]} --resume {session['id']}")


if __name__ == "__main__":
    main()
