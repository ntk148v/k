# k

A minimal coding agent named after Agent K from *Men in Black*, using OpenAI Responses native function calling.

## Run

Set `OPENAI_API_KEY` in `.env` or your environment, then run:

```bash
uv run python main.py
```

Enter one coding task at `›`. The agent can use `read`, `write`, `edit`, and `bash` in the current working directory.
