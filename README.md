# k

A minimal coding agent named after Agent K from _Men in Black_, using OpenAI Responses native function calling.

## Run

Set `OPENAI_API_KEY` in `.env` or your environment, then run:

```bash
uv run python main.py
```

Enter coding tasks at `›`. `k` stays open for additional tasks; submit a blank prompt or send EOF to exit. The agent can use `read`, `write`, `edit`, and `bash` in the current working directory.

After every successful or failed task, `k` prints a status line containing its name, current directory, Git branch (when available), model, token usage, and last request context size:

```
k | /path/to/project | main | model: openrouter/free | tokens: 1234 | context: 567
```

Tool calls use self-contained request history, which works with OpenAI-compatible providers that do not support `previous_response_id` lookups.

Output is color-coded in a terminal: cyan tool calls, yellow errors, green answer, and magenta status.
