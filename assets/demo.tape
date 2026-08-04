Output assets/k-demo.gif

Require uv
Require python3

Set Shell "bash"
Set FontSize 14
Set Width 1000
Set Height 700
Set Theme "Dracula"
Set Framerate 24
Set TypingSpeed 20ms
Set PlaybackSpeed 1.3
Set Padding 20
Set WindowBar Rings
Set WindowBarSize 36

# Recorded from a clean clone at /tmp/k-demo with an isolated demo HOME and
# a local OpenAI-compatible fixture on port 8765 (no API credits required).
Hide
Type "cd /tmp/k-demo"
Enter
Sleep 300ms

# ── Launch k: terminal UI and pinned status ───────────────
Show
Type "HOME=/tmp/k-demo-home OPENAI_BASE_URL=http://127.0.0.1:8765/v1 OPENAI_API_KEY=demo K_API_MODE=chat-completions OPENAI_MODEL_NAME=demo uv run python main.py --theme dark"
Enter
Sleep 1s

# ── Tool loop: read + bash with live activity status ──────
Type "inspect the project and summarize its features"
Enter
Sleep 3s

# ── Edit a file, then restore it with one-turn undo ───────
Type "add a demo marker comment at the top of main.py"
Enter
Sleep 3s
Type "/undo"
Enter
Sleep 1s

# ── Built-in session commands ─────────────────────────────
Type "/skills"
Enter
Sleep 1s
Type "/skill system-performance"
Enter
Sleep 1s
Type "/model demo-model"
Enter
Sleep 1s
Type "/resume"
Enter
Sleep 2s

# ── Exit ──────────────────────────────────────────────────
Type "/quit"
Enter
Sleep 1s
