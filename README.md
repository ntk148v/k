# k

A minimal coding agent named after Agent K from _Men in Black_, using OpenAI Responses native function calling.

## Run

Set `OPENAI_API_KEY` in `.env` or your environment, then run:

```bash
uv run python main.py
```

Enter one coding task at `›`. The agent can use `read`, `write`, `edit`, and `bash` in the current working directory.

After each response, a status line shows the model, cumulative token usage, and last request's context size:

```
model: openrouter/free | tokens: 1234 | context: 567
```

Output is color-coded in a terminal: dim title, cyan tool calls, yellow errors, green answer, and magenta status.
