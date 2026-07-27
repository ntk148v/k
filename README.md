# k

A minimal coding agent named after Agent K from _Men in Black_, using OpenAI Responses native function calling.

## Run

Set `OPENAI_API_KEY` in `.env` or your environment, then run:

```bash
uv run python main.py
```

Enter coding tasks at `❯`. `k` stays open for additional tasks; submit a blank prompt, send EOF (Ctrl+D), or press Ctrl+C to exit cleanly. The agent can use `read`, `write`, `edit`, and `bash` in the current working directory.

In an interactive terminal, `k` pins its statusline to the top row and keeps prompts, tool calls, answers, and errors scrollable below it. On a redirected or non-interactive stream, it falls back to ordinary line-by-line output.

The status line contains its name, current directory, Git branch (when available), model, token usage, and last request context size:

```
k | /path/to/project | main | model: openrouter/free | tokens: 1234 | context: 567
```

Tool calls use self-contained request history, which works with OpenAI-compatible providers that do not support `previous_response_id` lookups.

Output is color-coded in a terminal: red `k` and grey status text, cyan tool calls, yellow errors, and green answers.
