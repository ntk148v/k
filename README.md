# k

A minimal coding agent named after Agent K from _Men in Black_, using OpenAI Responses native function calling.

## Run

Set `OPENAI_API_KEY` in `.env` or your environment, then run:

```bash
uv run python main.py
```

Resume a saved session by ID:

```bash
uv run python main.py --resume 20260727-120000-ab12cd
```

Enter coding tasks at `❯`. Use `/quit`, submit a blank prompt, send EOF (Ctrl+D), or press Ctrl+C to exit cleanly.

Sessions are saved automatically to `~/.k/sessions/<id>.json` after each completed answer.

In an interactive terminal, `k` pins its statusline to the top row and keeps prompts, tool calls, answers, and errors scrollable below it. On a redirected or non-interactive stream, it falls back to ordinary line-by-line output.

The status line contains its name, current directory, Git branch (when available), model, token usage, and last request context size:

```
k | /path/to/project | main | model: openrouter/free | tokens: 1234 | context: 567
```

Tool calls use self-contained request history, which works with OpenAI-compatible providers that do not support `previous_response_id` lookups.

Output uses Pi-inspired truecolor light and dark palettes: colored Markdown, blue tool calls, red errors, and an accent-plus-muted status line with no background. Select one with `--theme light` or `--theme dark` (light is the default).

## Commands

- `/quit` exits without calling the model.
- `/skills` lists `~/.agents/skills/*.md`.
- `/skill <name>` activates an exact named skill for the current saved session.
- `/skill clear` removes the session's active skills.

Skills are direct Markdown files in `~/.agents/skills/`. They are never selected automatically.

## Context-mode

`k` keeps large tool outputs out of the model context. Oversized outputs are compacted to a useful head/tail preview, and the full text is saved beside the session under `~/.k/sessions/<id>-artifacts/`.

## Tools

The agent can use `read`, `write`, `edit`, and `bash` in the current working directory.

## Build the first minimal agent

The smallest useful coding agent needs:

1. A system prompt that says what the agent can do.
2. A short tool list with JSON schemas.
3. A loop that sends user input to the model.
4. A dispatcher that runs requested tools and feeds results back.
5. A session file so the loop can resume later.

Start boring: one file, stdlib first, no plugin system, no database, no framework. Add structure only when the file becomes hard to change.
