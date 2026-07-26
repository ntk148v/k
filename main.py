import base64
import mimetypes
import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

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

if __name__ == "__main__":
    main()
