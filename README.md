<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/agentk.jpg">
    <img src="assets/agentk.jpg" alt="Agent K" width="250">
  </picture>
</p>

<p align="center">
  <em>A minimal coding agent named after Agent K from <i>Men in Black</i>.</em>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#tutorial">Tutorial</a> •
  <a href="#how-k-was-built">How k Was Built</a> •
  <a href="#build-your-own">Build Your Own</a> •
  <a href="#commands">Commands</a> •
  <a href="#reference">Reference</a>
</p>

---

<p align="center">
  <img src="assets/k-demo.gif" alt="k demo" width="720">
</p>

## Features

- **Minimal.** One file, stdlib, no frameworks.
- **OpenAI Responses and Chat Completions APIs.** Native function calling with self-contained request history; select the API your provider supports.
- **Tool loop.** `read`, `write`, `edit`, `grep`, `bash` — the small set that covers most coding tasks.
- **Headless mode.** `k -p "task"` prints a final answer for scripts and CI.
- **Streaming.** Responses and tool arguments arrive incrementally, behind `formatted`/`stream` display modes.
- **Sessions.** Auto-saved to `~/.k/sessions/<id>.json` with title, model, and cwd metadata; `/resume` lists and switches sessions, `/model` persists a session's model, and `/new`, `/fork`, `/compact` keep experiments reversible.
- **Provider presets.** `[defaults]` and `[presets.*]` sections in `~/.k/config.toml`; `/preset <name>` switches model and API mode mid-session.
- **Context-mode compaction.** Large tool outputs are truncated to a head/tail preview; the full text is saved to disk alongside the session.
- **Permission modes.** `review` (default) approves every `bash` command interactively; `yolo` runs without prompts. Noninteractive runs reject shell commands unless `--mode yolo` is explicit.
- **Project-bound file tools.** `read`, `write`, and `edit` only touch files inside the current working directory, including symlink escapes; edits return a unified diff and refuse files changed since K read them.
- **One-turn Git undo.** `/undo` reverts the last completed agent turn from a clean worktree.
- **Skills & project instructions.** Markdown skills from `~/.agents/skills/`; layered `AGENTS.md` instructions from `~/.k/AGENTS.md` down through the repo root to the current directory.
- **Terminal UI.** Pi-inspired truecolor light/dark themes, pinned status line (model, tokens, git branch, cwd), colored Markdown rendering, multiline prompts.
- **Portable.** Works with OpenAI-compatible providers that support either API — OpenAI, OpenRouter, xAI, local LLMs, and OpenCode Zen.

## Quick Start

```bash
# Prerequisites: Python ≥3.12, uv (or pip)
uv sync
cp .env.example .env   # add OPENAI_API_KEY
uv run python main.py
```

```bash
# Try it instantly (no clone needed):
uvx --from mini-agent-k k

# Or install permanently via pip/uv:
pip install mini-agent-k
# then:
k
```

> PyPI distribution: [`mini-agent-k`](https://pypi.org/project/mini-agent-k/). The executable is named `k`.

Resume a session:

```bash
uv run python main.py --resume 20260727-120000-ab12cd
```

## Tutorial

### Using `k` as a Coding Agent

When you run `k`, you get a prompt (`❯`). Type a coding task and the agent will reason over it, call tools (`read`, `write`, `edit`, `grep`, `bash`), and return an answer. Press **Ctrl+J** to add a line break; press Enter to submit the complete prompt.

**Example session:**

```
❯ read the main.py file and summarize the architecture
k | /home/user/project | main | model: openrouter/free | tokens: 1234 | context: 567
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The project is a single-file coding agent with:
• OpenAI Responses API loop
• 5 tools: read, write, edit, grep, bash
• TTY-aware terminal UI with light/dark themes
• Session persistence under ~/.k/sessions/
• Skills system for injecting system instructions
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

You can chain multiple tasks in the same session — the conversation history is preserved.

**Passing images:** `k`'s `read` tool detects image files (jpg, png, gif, webp) and sends them as base64 attachments to the model. The model can inspect screenshots, diagrams, or UI mockups.

### Working with Skills

Skills are Markdown files in `~/.agents/skills/<name>/SKILL.md`. They act as additional system instructions injected per session.

```
~/.agents/skills/
  python/
    SKILL.md   # "Follow PEP 8, use type hints..."
  review/
    SKILL.md   # "Focus on security and edge cases..."
```

**In-session commands:** run `/help` for the full list — sessions (`/resume`, `/new`, `/fork`, `/compact`, `/undo`), model and modes (`/model`, `/mode`, `/display`, `/preset`), and skills (`/skills`, `/skill`).

### Resuming Sessions

Sessions are saved automatically after every completed answer. Use `/resume` to list recent sessions (title, model, cwd, update time) or `/resume <id>` to switch; resume at launch with:

```bash
uv run python main.py --resume <session-id>
```

The defaults are `review` permission mode (each shell command is shown and requires approval) and `formatted` display. While the agent works, the pinned status temporarily shows `thinking` and the active tool name.

**Safety:** The `read`, `write`, and `edit` tools resolve every path against the current working directory and reject anything outside it, including symlink escapes. `bash` is not sandboxed. In `review` mode, each shell command displays the exact command and requires approval; `yolo` mode skips approval. Noninteractive runs reject shell commands unless `--mode yolo` is explicit. K is not a container or OS sandbox — run it only in environments you trust.

**Undo:** `/undo` works once, only after a task that began with a clean Git worktree. It reverses the turn's tracked changes (staged changes included), deletes files the turn created, preserves everything that existed before, and declines if the worktree changed since the turn.

### Themes

```bash
uv run python main.py --theme dark --mode review --display formatted
uv run python main.py --mode yolo --display stream  # live output, no bash prompts
OPENAI_MODEL_NAME=gpt-4o uv run python main.py  # custom model
```

For OpenCode Zen, configure its OpenAI-compatible endpoint and use Chat Completions mode:

```bash
OPENAI_BASE_URL=https://opencode.ai/zen/v1 \
OPENAI_API_KEY="$OPENCODE_API_KEY" \
OPENAI_MODEL_NAME=<zen-model-id> \
K_API_MODE=chat-completions \
uv run python main.py
```

Set defaults via environment variables or `~/.k/config.toml`:

```toml
[defaults]
model = "openrouter/free"
api_mode = "responses"
permission_mode = "review"
display = "formatted"
base_url = "https://openrouter.ai/api/v1"

[presets.fast]
model = "provider/fast-model"
api_mode = "chat-completions"
```

Use `/preset fast` during a session. All environment variables are listed in the [Reference](#reference).

### Context-mode for Large Outputs

When a tool call returns more than 12,000 characters, `k` truncates it to a 6,000-char head and 2,000-char tail with a notice:

```
[context-mode: output compacted from 45000 chars]
<first 6000 chars>
...
<last 2000 chars>
Full output saved: ~/.k/sessions/<id>-artifacts/bash-142530-abc123.txt
```

The full output is always saved to an artifact file next to the session, so nothing is lost even though the model only sees the preview.

### Limits

| Boundary                 | Limit                             |
| ------------------------ | --------------------------------- |
| Text file read           | 1 MB (`MAX_TEXT_FILE_BYTES`)      |
| Image read               | 5 MB (`MAX_IMAGE_FILE_BYTES`)     |
| Shell command duration   | 60 s max (`DEFAULT_BASH_TIMEOUT`) |
| Tool turns per prompt    | 20 (`MAX_AGENT_TOOL_TURNS`)       |
| Retained history records | 200 (`MAX_HISTORY_ITEMS`)         |
| Grep files scanned       | 2,000 (`MAX_GREP_FILES`)          |
| Grep matches returned    | 2,000 (`MAX_GREP_MATCHES`)        |

Tool output larger than 12,000 characters is still captured and written to a local artifact before it is compacted; the byte limits above only bound what the model reads.

## How `k` Was Built

`k` started as a 50-line experiment and grew to about 1,500 lines one small step at a time:

1. **The seed** — a bare OpenAI Responses loop with `read` and `bash`. The hardest part.
2. **Tools and TUI** — `write` and `edit` completed the tool set; colored output showed what the agent was doing.
3. **Status line** — an ANSI scroll region pins row 1, so model, tokens, git branch, and cwd stay visible while rows 2+ scroll.
4. **Sessions** — history serialized to `~/.k/sessions/<id>.json`; resume with `--resume` or `/resume`.
5. **Terminal polish** — Markdown rendering with Pi-inspired light/dark palettes; the demo GIF recorded with VHS.
6. **Skills** — plain Markdown files from `~/.agents/skills/` injected into the system prompt. No plugin system, no DSL.
7. **Packaging** — Chat Completions support for other providers, the `k` entry point on PyPI, Ctrl+J multiline input.
8. **Safer sessions** — session metadata and discovery, per-session `/model`, one-turn `/undo`, activity status.
9. **Hardened boundaries** — project-bound file tools (symlink escapes included), shell approval, recovered corrupt sessions, explicit limits everywhere.
10. **Hardening became features** — headless `k -p` mode, bounded `grep`, layered `AGENTS.md` instructions.
11. **Presets and control** — `config.toml` `[defaults]`/`[presets.*]`, interruptible shell commands, `/fork` and `/compact`.
12. **Streaming** — incremental text and tool arguments over both APIs, behind the `formatted`/`stream` display modes.

Every feature followed the same pattern: add the minimum that works, ship it, iterate on edges, document the design.

---

## Build Your Own

`k` is designed as both a tool and a reference implementation. The entire agent is **one Python file** (about 1,500 lines) — here's how to build a minimal coding agent from scratch.

### The Core Loop

```python
import json
from openai import OpenAI

client = OpenAI()
tools = [...]   # tool definitions
history = [{"role": "user", "content": task}]

response = client.responses.create(
    model="openrouter/free",
    instructions="You are a coding assistant.",
    input=history,
    tools=tools,
)

while True:
    calls = [item for item in response.output
             if item.type == "function_call"]
    if not calls:
        print(response.output_text)
        break

    for call in calls:
        result = dispatch_tool(call.name, call.arguments)
        history.append(call)
        history.append(result)

    response = client.responses.create(
        model="openrouter/free",
        instructions="You are a coding assistant.",
        input=history,
        tools=tools,
    )
```

That's it. The loop is: **send input → execute tool calls → feed results back → repeat until the model answers in plain text.**

After K reads a text file, `edit` checks that the file has not changed before writing it. If another process changed it, K must reread the file first. This is a safety check, not a file lock.

### Tools: the 4 You Need

A coding agent needs exactly 4 tools to do almost everything:

| Tool    | What it does                                                       |
| ------- | ------------------------------------------------------------------ |
| `read`  | Read file contents (text as numbered lines, images as base64)      |
| `write` | Create or overwrite files                                          |
| `edit`  | Replace exactly one occurrence of text, then return a unified diff |
| `bash`  | Run shell commands (git, grep, tests, ls)                          |

`k` itself ships a fifth tool, `grep`: a bounded regex search over the project that skips `.git`, `.venv`, `node_modules`, `dist`, and `__pycache__`.

**Keep tool definitions small and strict.** Set `strict: true` and `additionalProperties: false` in the JSON schema so the model doesn't hallucinate parameters.

```python
{
    "type": "function",
    "name": "read",
    "description": "Read file contents.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to read"},
            "offset": {"type": "integer", "description": "Start line (1-indexed)", "default": 1},
            "limit": {"type": "integer", "description": "Max lines to read", "default": 2000},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "strict": True,
}
```

### Session Persistence

Save history so the user can resume:

```python
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SESSIONS_DIR = Path.home() / ".k" / "sessions"

def save_session(session: dict) -> None:
    path = SESSIONS_DIR / f"{session['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=2))

def load_session(session_id: str) -> dict:
    return json.loads((SESSIONS_DIR / f"{session_id}.json").read_text())
```

### Handling Large Outputs

Model context windows are finite. When tool output exceeds a threshold, compact it:

```python
MAX_OUTPUT = 12_000

def compact_output(text: str, label: str, session_id: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    # Save full output to disk
    artifact_dir = SESSIONS_DIR / f"{session_id}-artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"{label}-{uuid4().hex[:6]}.txt"
    path.write_text(text, encoding="utf-8")
    # Return useful preview
    return f"[compacted from {len(text)} chars]\n{text[:6000]}\n...\n{text[-2000:]}"
```

### Adding a Terminal UI (Optional)

A pinned status line (row 1) that stays visible while the scrollable area (rows 2+) contains conversation:

```python
import shutil

def pin_status(text: str) -> None:
    if not sys.stdout.isatty():
        return
    rows = shutil.get_terminal_size().lines
    # Reserve row 1 for status, rows 2+ for scrollable content
    sys.stdout.write(f"\033[2;{rows}r\033[1;1H\033[2K{text}\n")
    sys.stdout.write("\033[2;1H")
    sys.stdout.flush()
```

ANSI escape sequence breakdown:

- `\033[2;Nr` — set scroll region (row 2 to N)
- `\033[1;1H` — move cursor to row 1
- `\033[2K` — clear entire row

### Skills Injection

Allow users to load custom system instructions from Markdown files:

```python
def load_skills(names: list[str], base_prompt: str) -> str:
    skills_dir = Path.home() / ".agents" / "skills"
    blocks = []
    for name in names:
        path = skills_dir / name / "SKILL.md"
        if path.exists():
            blocks.append(f"### {name}\n{path.read_text()}")
    if blocks:
        return base_prompt + "\n\n## Active skills\n\n" + "\n\n".join(blocks)
    return base_prompt
```

### Key Decisions

- **Self-contained history vs `previous_response_id`:** `k` sends the full history on every turn. It supports both Responses and Chat Completions formats, so providers only need to implement one of those APIs.
- **Streaming behind two display modes:** Both APIs stream text and tool arguments incrementally. `formatted` waits for completion and renders Markdown; `stream` prints live output as it arrives.
- **One file, no framework:** The agent remains one file (about 1,500 lines). There is no plugin system, database, or async framework. Add structure only when the file becomes hard to change.

## Commands

| Command                        | Action                                                       |
| ------------------------------ | ------------------------------------------------------------ |
| `/help`                        | Show the command list                                        |
| `/quit`                        | Exit without calling the model                               |
| `/resume`                      | List up to 20 recent sessions                                |
| `/resume <id>`                 | Switch to a saved session                                    |
| `/model <model-name>`          | Set and persist this session's model                         |
| `/mode <review\|yolo>`         | Set the bash permission mode                                 |
| `/display <formatted\|stream>` | Set the response display mode                                |
| `/preset <name>`               | Apply a configured provider preset                           |
| `/new`                         | Start a fresh session (carries over skills and modes)        |
| `/fork`                        | Fork the current session, copying its history                |
| `/compact [focus]`             | Summarize the session into a compact replacement             |
| `/undo`                        | Revert the last completed agent turn in a clean Git worktree |
| `/skills`                      | List available skills in `~/.agents/skills/`                 |
| `/skill <name>`                | Load a skill for this session                                |
| `/skill clear`                 | Clear all loaded skills                                      |
| Ctrl+J                         | Insert a line break in the current prompt                    |
| blank prompt / EOF (Ctrl+D)    | Exit cleanly                                                 |
| Ctrl+C                         | Interrupt / exit                                             |

## Reference

### Project Structure

```
k/
├── main.py              # the entire agent
├── test_main.py         # unittest test suite
├── pyproject.toml       # uv/pip project config
├── assets/
│   ├── agentk.jpg       # banner image
│   └── k-demo.gif       # demo animation
└── .env                 # OPENAI_API_KEY (not committed)
```

### Development

Run the full verification suite (this is what CI enforces):

```bash
uv sync --extra dev --locked
uv run ruff check main.py test_main.py
uv run python -m unittest -v
uv build
```

### Environment Variables

| Variable            | Default           | Description                              |
| ------------------- | ----------------- | ---------------------------------------- |
| `OPENAI_API_KEY`    | —                 | API key for the LLM provider             |
| `OPENAI_BASE_URL`   | provider default  | OpenAI-compatible endpoint base URL      |
| `OPENAI_MODEL_NAME` | `openrouter/free` | Model identifier                         |
| `K_API_MODE`        | `responses`       | `responses` or `chat-completions`        |
| `K_THEME`           | `light`           | Color theme (`light` or `dark`)          |

### Config and Session Files

```
~/.k/config.toml                        # defaults + provider presets
~/.k/AGENTS.md                          # global project instructions
~/.k/sessions/<id>.json                 # conversation history + skills
~/.k/sessions/<id>-artifacts/           # compacted tool outputs
```

Place durable repository guidance in `AGENTS.md` files — global `~/.k/AGENTS.md`, then repo root down to the current directory (16 KiB each, 48 KiB total):

```markdown
Run `uv run python -m unittest -v` before reporting completion.
```

### Dependencies

- `openai>=2.48.0` — OpenAI Responses and Chat Completions APIs
- `python-dotenv>=1.2.2` — `.env` loading

No web framework, no database, no async runtime, no plugins.

### License

[MIT](LICENSE)
