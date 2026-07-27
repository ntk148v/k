Output docs/k-demo.gif

Require uv
Require python3

Set Shell "bash"
Set FontSize 14
Set Width 1200
Set Height 800
Set Theme "Dracula"
Set Framerate 30
Set TypingSpeed 20ms
Set PlaybackSpeed 1.3
Set Padding 20
Set WindowBar Rings
Set WindowBarSize 36

# ── Navigate to project ───────────────────────────────────
Hide
Type "cd /home/kien/Workspace/github.com/ntk148v/k"
Enter
Sleep 300ms
Type "source .venv/bin/activate"
Enter
Sleep 300ms
Hide

# ── Launch k ──────────────────────────────────────────────
Show
Sleep 500ms
Type "uv run python main.py"
Sleep 300ms
Enter
Sleep 3s

# ── Task 1: quick question ────────────────────────────────
Type "how many lines of code in this project?"
Sleep 100ms
Enter
Sleep 8s

# ── Task 2: run the tests ─────────────────────────────────
Type "run the unit tests"
Sleep 100ms
Enter
Sleep 14s

# ── Task 3: simple edit ───────────────────────────────────
Type "change the banner to say 'k - Agent K' instead of just 'k'"
Sleep 100ms
Enter
Sleep 10s

# ── Exit ──────────────────────────────────────────────────
Type "/quit"
Sleep 100ms
Enter
Sleep 1s
