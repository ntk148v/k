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
- **Tool loop.** `read`, `write`, `edit`, `bash` — the four operations that cover most coding tasks.
- **Context-mode compaction.** Large tool outputs are truncated to a useful head/tail preview; the full text is saved to disk alongside the session.
- **Session persistence.** Every conversation is saved to `~/.k/sessions/<id>.json`. Resume any session by ID.
- **Skills.** Load Markdown skill files from `~/.agents/skills/` to inject system instructions dynamically.
- **Terminal UI.** Pi-inspired truecolor light/dark themes, pinned status line (showing model, tokens, git branch, cwd), colored Markdown rendering, multiline prompts.
- **Portable.** Works with OpenAI-compatible providers that support either Responses or Chat Completions — OpenAI, OpenRouter, xAI, local LLMs, and OpenCode Zen.

## Quick Start

```bash
# Prerequisites: Python ≥3.12, uv (or pip)
uv sync
cp .env.example .env   # add OPENAI_API_KEY
uv run python main.py
```

Resume a session:

```bash
uv run python main.py --resume 20260727-120000-ab12cd
```

## Tutorial

### Using `k` as a Coding Agent

When you run `k`, you get a prompt (`❯`). Type a coding task and the agent will reason over it, call tools (`read`, `write`, `edit`, `bash`), and return an answer. Press **Ctrl+J** to add a line break; press Enter to submit the complete prompt.

**Example session:**

```
❯ read the main.py file and summarize the architecture
k | /home/user/project | main | model: openrouter/free | tokens: 1234 | context: 567
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
The project is a single-file coding agent with:
• OpenAI Responses API loop
• 4 tools: read, write, edit, bash
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

**In-session commands:**

| Command         | Action                        |
| --------------- | ----------------------------- |
| `/skills`       | List available skills         |
| `/skill <name>` | Load a skill for this session |
| `/skill clear`  | Remove all loaded skills      |

### Resuming Sessions

Sessions are saved automatically after every completed answer. Find the session ID with:

```bash
ls ~/.k/sessions/
```

Resume with:

```bash
uv run python main.py --resume <session-id>
```

### Themes

```bash
uv run python main.py --theme dark        # dark mode
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

Set defaults via environment variables:

- `K_THEME` — `light` (default) or `dark`
- `OPENAI_MODEL_NAME` — model name (default: `openrouter/free`)
- `K_API_MODE` — `responses` (default) or `chat-completions`; use the latter for OpenAI-compatible providers without a Responses endpoint, including OpenCode Zen.

### Context-mode for Large Outputs

When a tool call returns more than 12,000 characters, `k` truncates it to a 6,000-char head and 2,000-char tail with a notice:

```
[context-mode: output compacted from 45000 chars]
<first 6000 chars>
...
<last 2000 chars>
Full output saved: ~/.k/sessions/<id>-artifacts/bash-142530-abc123.txt
```

The full output is always saved to an artifact file next to the session. You can re-read it with `read` if the model needs the full content.

---

## How `k` Was Built (Commit by Commit)

`k` started as a 50-line experiment and grew into ~400 lines of production-ish agent. Here's the story of every meaningful commit — what was added and why.

### 1. The Seed — Minimal Agent Loop

```
198628e init commit
```

The first commit was a bare OpenAI Responses API loop: send a prompt, parse tool calls, dispatch them, feed results back. Two tools (`read`, `bash`), no state, no TUI. Just the loop — the hardest part.

```python
# The essence hasn't changed
response = client.responses.create(model=MODEL, instructions=SYSTEM_PROMPT, input=history, tools=TOOLS)
while True:
    calls = [item for item in response.output if item.type == "function_call"]
    if not calls:
        print(response.output_text)
        break
    # dispatch, feed back, loop
```

### 2. More Tools, Structured Output

```
28e512d feat: add native local tools
```

Added `write` and `edit` — the 4-tool set that covers almost every coding task. `read` gained numbered line output so the model could reference line numbers in edits. `edit` uses exact string replacement (rejecting ambiguous matches) — the simplest correct implementation.

```
78d2f71 feat: add native tool calling tui
```

First TUI: colored output for tool calls (blue), errors (red), answers. The model's tool invocations were printed so the user could see what the agent was doing.

### 3. The Status Line

```
c3d7e2a chore: rename agent to k
977ee63 feat: show model and token usage
51ac10e feat: show cli context in status
1da8232 fix: keep cli running after each task
47296e1 feat: pin cli statusline
b4680b3 docs: describe pinned cli statusline
9dd8752 feat: clear terminal at startup before rendering pinned status
bf6395e fix: preserve prompt position when repainting status
dad5c42 render 'k' in status lines with reversed foreground (bg=accent)
```

The status line evolved over several commits. The key trick: ANSI escape sequences set a scroll region (`\033[2;Nr`) so row 1 stays pinned while rows 2+ scroll. Each task repaints the status without moving the input cursor (`\033[s` / `\033[u`).

```
k | /repo | main | model: openrouter/free | tokens: 1234 | context: 567
```

### 4. Clean Exits and Commands

```
2eef8bc feat: handle cli interruption cleanly
4aca4e9 feat: save and resume sessions
1d79899 feat: add slash command handling
```

Ctrl+C / Ctrl+D / blank input all exit cleanly. Sessions serialize the full history array to `~/.k/sessions/<id>.json` — resume with `--resume`. Slash commands (`/quit`, later `/skills`, `/skill`) were wired in.

### 5. Session Persistence

```
4aca4e9 feat: save and resume sessions
bbc8ed7 feat: show k sunglasses banner
73c5415 feat: compact large tool outputs
```

Sessions save automatically after every completed answer. The session ID is a timestamp + random suffix: `20260727-120000-ab12cd`. Large tool outputs (>12KB) are truncated with a head/tail preview and the full output is saved to an artifact file next to the session JSON.

### 6. Terminal Polish

```
36a5eb5 docs: document minimal agent usage
1b65489 docs: add VHS demo recording
b161b48 feat: render markdown with accessible terminal colors
4b92598 fix: preserve terminal theme contrast
54fd651 feat: add pi-inspired terminal palettes
```

Markdown rendering came next: headings, bold, italic, code, lists, blockquotes, fences. Two color palettes (light/dark), Pi-inspired. The demo GIF was recorded with VHS (`assets/demo.tape`).

### 7. Skills System

```
966e20e docs: add skills support design
f8f386d feat: add skill discovery
e0ec168 feat: add session skill commands
53a24a7 docs: document skill commands
7e48984 ignore files
9ce9ce7 refactor: remove built-in skills, only ~/.agents/skills/
8afabc0 fix: discover skills from ~/.agent/skills/<name>/SKILL.md
750fb36 fix: correct skills dir to .agents
```

Skills are Markdown files in `~/.agents/skills/<name>/SKILL.md`. They get appended to the system prompt as additional instructions. No plugin system, no DSL — just text injected into the prompt. The directory path was iterated a few times (`~/.agent/` → `~/.agents/`).

### The Pattern

Every feature followed the same path:

1. **Add the minimum** that works (one file, no deps)
2. **Ship it** — the simplest version first
3. **Iterate on edges** — error handling, edge cases, UX polish
4. **Document** — explain the design, not just the API

No premature abstractions, no plugin system, no database, no async framework. ~400 lines total.

---

## Build Your Own

`k` is designed as both a tool and a reference implementation. The entire agent is **one Python file** (~400 lines) — here's how to build a minimal coding agent from scratch.

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

### Tools: the 4 You Need

A coding agent needs exactly 4 tools to do almost everything:

| Tool    | What it does                                                  |
| ------- | ------------------------------------------------------------- |
| `read`  | Read file contents (text as numbered lines, images as base64) |
| `write` | Create or overwrite files                                     |
| `edit`  | Replace exactly one occurrence of text in a file              |
| `bash`  | Run shell commands (git, grep, tests, ls)                     |

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
- **No streaming:** Blocking `responses.create()` calls keep the agent loop simple. Streaming adds complexity with no functional benefit for a tool-calling agent.
- **One file, no framework:** The entire agent is ~400 lines. There's no plugin system, no database, no async framework. Add structure only when the file becomes hard to change.

### Your First Custom Agent (Minimal Example)

```python
"""minimal_agent.py — your first coding agent in <100 lines."""
import json, subprocess, sys
from openai import OpenAI

client = OpenAI()

def bash(command):
    return subprocess.run(command, shell=True, capture_output=True, text=True).stdout

def read(path):
    with open(path) as f:
        return f.read()

TOOLS = [{
    "type": "function", "name": "bash",
    "description": "Run a shell command",
    "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                   "required": ["command"], "additionalProperties": False},
    "strict": True,
}, {
    "type": "function", "name": "read",
    "description": "Read a file",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                   "required": ["path"], "additionalProperties": False},
    "strict": True,
}]

HANDLERS = {"bash": bash, "read": read}
history = [{"role": "user", "content": sys.argv[1]}]

response = client.responses.create(
    model="openrouter/free",
    instructions="You are a coding assistant. Use tools to answer.",
    input=history, tools=TOOLS,
)

while True:
    calls = [i for i in response.output if i.type == "function_call"]
    if not calls:
        print(response.output_text)
        break
    for call in calls:
        result = HANDLERS[call.name](**json.loads(call.arguments))
        history.append(call)
        history.append({"type": "function_call_output", "call_id": call.call_id, "output": str(result)})
    response = client.responses.create(
        model="openrouter/free",
        instructions="You are a coding assistant.",
        input=history, tools=TOOLS,
    )
```

Usage: `python minimal_agent.py "find all python files larger than 10KB"`

## Commands

| Command                     | Action                                       |
| --------------------------- | -------------------------------------------- |
| `/quit`                     | Exit without calling the model               |
| `/skills`                   | List available skills in `~/.agents/skills/` |
| `/skill <name>`             | Load a skill for this session                |
| `/skill clear`              | Clear all loaded skills                      |
| Ctrl+J                      | Insert a line break in the current prompt    |
| blank prompt / EOF (Ctrl+D) | Exit cleanly                                 |
| Ctrl+C                      | Interrupt / exit                             |

## Reference

### Project Structure

```
k/
├── main.py              # ~400 lines, the entire agent
├── test_main.py         # pytest test suite
├── pyproject.toml       # uv/pip project config
├── assets/
│   ├── agentk.jpg       # banner image
│   └── k-demo.gif       # demo animation
└── .env                 # OPENAI_API_KEY (not committed)
```

### Environment Variables

| Variable            | Default           | Description                       |
| ------------------- | ----------------- | --------------------------------- |
| `OPENAI_API_KEY`    | —                 | API key for the LLM provider      |
| `OPENAI_MODEL_NAME` | `openrouter/free` | Model identifier                  |
| `K_API_MODE`        | `responses`       | `responses` or `chat-completions` |
| `K_THEME`           | `light`           | Color theme (`light` or `dark`)   |

### Session Files

```
~/.k/sessions/<id>.json                 # conversation history + skills
~/.k/sessions/<id>-artifacts/           # compacted tool outputs
```

### Dependencies

- `openai>=2.48.0` — OpenAI Responses and Chat Completions APIs
- `python-dotenv>=1.2.2` — `.env` loading

No web framework, no database, no async runtime, no plugins.

### License

[MIT](LICENSE)
