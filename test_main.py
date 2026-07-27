import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main


class ToolTests(unittest.TestCase):
    def test_read_respects_offset_and_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "notes.txt")
            path.write_text("first\nsecond\nthird\n", encoding="utf-8")
            self.assertEqual(main.read(str(path), offset=2, limit=1), "2: second")

    def test_read_returns_an_image_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "pixel.png")
            path.write_bytes(b"png-bytes")
            result = main.read(str(path))
        self.assertEqual(result[0]["type"], "input_image")
        self.assertTrue(result[0]["image_url"].startswith("data:image/png;base64,"))

    def test_write_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "new", "file.txt")
            self.assertEqual(main.write(str(path), "hello"), f"Wrote {path}")
            self.assertEqual(path.read_text(encoding="utf-8"), "hello")

    def test_edit_replaces_only_one_exact_match(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "file.txt")
            path.write_text("before OLD after", encoding="utf-8")
            self.assertEqual(main.edit(str(path), "OLD", "NEW"), f"Edited {path}")
            self.assertEqual(path.read_text(encoding="utf-8"), "before NEW after")

    def test_edit_rejects_ambiguous_text_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "file.txt")
            path.write_text("OLD OLD", encoding="utf-8")
            result = main.edit(str(path), "OLD", "NEW")
            self.assertIn("exactly once", result)
            self.assertEqual(path.read_text(encoding="utf-8"), "OLD OLD")

    def test_bash_returns_combined_output(self):
        self.assertIn("hello", main.bash("python -c \"print('hello')\""))

    def test_read_rejects_non_positive_paging(self):
        self.assertEqual(main.read("notes.txt", offset=0), "Error: offset must be at least 1")
        self.assertEqual(main.read("notes.txt", limit=0), "Error: limit must be at least 1")

    def test_tools_return_errors_for_missing_files(self):
        missing = "/definitely-missing-k-file"
        self.assertTrue(main.read(missing).startswith("Error: cannot read"))
        self.assertTrue(main.edit(missing, "old", "new").startswith("Error: cannot edit"))


class AgentTests(unittest.TestCase):
    def test_dispatch_reports_bad_json_and_unknown_tools(self):
        self.assertIn("invalid JSON", main.dispatch_tool("read", "{", "call-1")["output"])
        self.assertIn("Unknown tool", main.dispatch_tool("missing", "{}", "call-2")["output"])

    def test_compact_output_keeps_short_text(self):
        self.assertEqual(main.compact_output("short", "bash", "s1"), "short")

    def test_compact_output_saves_large_text_next_to_session(self):
        with tempfile.TemporaryDirectory() as d:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = d
            try:
                text = "x" * (main.MAX_TOOL_OUTPUT_CHARS + 50)
                compact = main.compact_output(text, "bash", "s1")
                self.assertIn("Full output saved:", compact)
                artifact_dir = Path(d) / ".k" / "sessions" / "s1-artifacts"
                saved = list(artifact_dir.glob("bash-*.txt"))
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0].read_text(encoding="utf-8"), text)
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

    def test_run_agent_returns_final_text_after_a_tool_call(self):
        call = SimpleNamespace(type="function_call", name="bash", arguments='{"command": "echo hello"}', call_id="call-1")
        first = SimpleNamespace(
            id="resp-1", output=[call], output_text="",
            usage=SimpleNamespace(input_tokens=5, output_tokens=3),
        )
        second = SimpleNamespace(
            id="resp-2", output=[], output_text="Done",
            usage=SimpleNamespace(input_tokens=9, output_tokens=4),
        )
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=[first, second])))

        with patch.object(main, "bash", return_value="hello"):
            self.assertEqual(main.run_agent("say hello", client)[:3], ("Done", 21, 9))

        first_call, second_call = client.responses.create.call_args_list
        self.assertEqual(first_call.kwargs["model"], main.MODEL)
        self.assertEqual(first_call.kwargs["input"], [{"role": "user", "content": "say hello"}])
        self.assertEqual(second_call.kwargs["model"], main.MODEL)
        self.assertNotIn("previous_response_id", second_call.kwargs)
        self.assertEqual(second_call.kwargs["input"][0], {"role": "user", "content": "say hello"})
        self.assertIn(call, second_call.kwargs["input"])
        self.assertEqual(second_call.kwargs["input"][-1]["call_id"], "call-1")

    def test_run_agent_tolerates_missing_usage(self):
        response = SimpleNamespace(id="resp-1", output=[], output_text="Done")
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response)))
        answer, total_tokens, context_tokens, history = main.run_agent("say hello", client)
        self.assertEqual((answer, total_tokens, context_tokens), ("Done", 0, None))
        self.assertTrue(history)

    def test_render_status_has_no_ansi_when_stdout_is_not_a_tty(self):
        with patch.object(main.sys.stdout, "isatty", return_value=False), patch("sys.stdout", new_callable=io.StringIO) as output:
            main.render("status", "model: openrouter/free | tokens: 21 | context: 9")
        self.assertEqual(output.getvalue(), "model: openrouter/free | tokens: 21 | context: 9\n")

    def test_render_status_uses_light_pi_palette_in_a_tty(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(
            output, "isatty", return_value=True
        ):
            main.render("status", "k | /repo | main")
        self.assertEqual(
            output.getvalue(),
            "\033[7m\033[38;2;90;128;128m k \033[0m\033[38;2;108;108;108m | /repo | main\033[0m\n",
        )

    def test_render_pinned_status_reserves_first_row_and_repositions_cursor(self):
        size = SimpleNamespace(lines=24)
        with (
            patch("sys.stdout", new_callable=io.StringIO) as output,
            patch.object(output, "isatty", return_value=True),
            patch.object(main.shutil, "get_terminal_size", return_value=size),
        ):
            self.assertTrue(main.render_pinned_status("k | /repo", move_cursor=True))
        self.assertEqual(
            output.getvalue(),
            "\033[2;24r\033[1;1H\033[2K\033[7m\033[38;2;90;128;128m k \033[0m\033[38;2;108;108;108m | /repo\033[0m\n\033[2;1H",
        )

    def test_render_pinned_status_can_repaint_without_moving_input_cursor(self):
        size = SimpleNamespace(lines=24)
        with (
            patch("sys.stdout", new_callable=io.StringIO) as output,
            patch.object(output, "isatty", return_value=True),
            patch.object(main.shutil, "get_terminal_size", return_value=size),
        ):
            self.assertTrue(main.render_pinned_status("k | /repo"))
        self.assertEqual(
            output.getvalue(),
            "\033[s\033[2;24r\033[1;1H\033[2K\033[7m\033[38;2;90;128;128m k \033[0m\033[38;2;108;108;108m | /repo\033[0m\n\033[u",
        )

    def test_render_pinned_status_falls_back_when_stdout_is_not_a_tty(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(
            output, "isatty", return_value=False
        ):
            self.assertFalse(main.render_pinned_status("k | /repo"))
        self.assertEqual(output.getvalue(), "k | /repo\n")

    def test_restore_terminal_restores_the_default_scroll_region(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(
            output, "isatty", return_value=True
        ):
            main.restore_terminal()
        self.assertEqual(output.getvalue(), "\033[r\033[0m\n")

    def test_render_has_no_ansi_when_stdout_is_not_a_tty(self):
        with patch.object(main.sys.stdout, "isatty", return_value=False), patch("sys.stdout", new_callable=io.StringIO) as output:
            main.render("tool", "read file.txt")
        self.assertNotIn("\x1b[", output.getvalue())
        self.assertIn("read file.txt", output.getvalue())

    def test_format_markdown_heading(self):
        self.assertEqual(main.format_markdown("# Title"), "\033[38;2;154;115;38m\033[1mTitle\033[0m")

    def test_format_markdown_list_with_inline(self):
        self.assertEqual(
            main.format_markdown("- **bold** and `code`"),
            "\033[38;2;88;132;88m• \033[38;2;31;35;40m\033[1mbold\033[0m\033[38;2;31;35;40m and \033[38;2;90;128;128mcode\033[38;2;31;35;40m\033[0m",
        )

    def test_format_markdown_fenced_code(self):
        self.assertEqual(
            main.format_markdown("```\n*x*\n```"),
            "\033[38;2;108;108;108m```\033[0m\n\033[38;2;88;132;88m*x*\033[0m\n\033[38;2;108;108;108m```\033[0m",
        )

    def test_format_markdown_blockquote(self):
        self.assertEqual(main.format_markdown("> note"), "\033[38;2;108;108;108m│ note\033[0m")

    def test_format_markdown_hr(self):
        self.assertEqual(main.format_markdown("---"), "\033[38;2;108;108;108m───\033[0m")

    def test_format_markdown_bold_italic(self):
        self.assertEqual(
            main.format_markdown("**bold** and *italic*"),
            "\033[38;2;31;35;40m\033[1mbold\033[0m\033[38;2;31;35;40m and \033[3mitalic\033[0m\033[38;2;31;35;40m\033[0m",
        )

    def test_render_tool_uses_bright_blue(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(
            output, "isatty", return_value=True
        ):
            main.render("tool", "read file.txt")
        self.assertEqual(output.getvalue(), "  \033[38;2;84;125;167mread file.txt\033[0m\n")

    def test_render_error_uses_bright_red(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(
            output, "isatty", return_value=True
        ):
            main.render("output", "Error: something went wrong")
        self.assertEqual(output.getvalue(), "\033[38;2;170;85;85mError: something went wrong\033[0m\n")

    def test_render_answer_uses_default_terminal_foreground(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(
            output, "isatty", return_value=True
        ):
            main.render("answer", "plain text")
        self.assertEqual(output.getvalue(), "\033[38;2;31;35;40mplain text\033[0m\n")

    def test_render_answer_plain_when_not_a_tty(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(
            output, "isatty", return_value=False
        ):
            main.render("answer", "# Title")
        self.assertEqual(output.getvalue(), "# Title\n")

    def test_main_processes_multiple_tasks_until_blank_input(self):
        with (
            patch("builtins.input", side_effect=["first", "second", ""]) as mock_input,
            patch.object(main, "OpenAI"),
            patch.object(main, "run_agent", side_effect=[("one", 1, 1, []), ("two", 2, 2, [])]) as mock_agent,
            patch.object(main, "render_pinned_status", return_value=True),
            patch.object(main, "restore_terminal"),
            patch.object(main, "render") as render,
        ):
            main.main()
        self.assertEqual(mock_agent.call_args_list[0].args[0], "first")
        self.assertEqual(mock_agent.call_args_list[1].args[0], "second")
        self.assertEqual(mock_agent.call_count, 2)
        self.assertEqual(mock_input.call_args_list[0].args, ("\u276f ",))
        self.assertFalse(any(call.args[0] == "title" for call in render.call_args_list))

    def test_main_renders_error_and_continues_after_agent_failure(self):
        with (
            patch("builtins.input", side_effect=["broken", "working", ""]),
            patch.object(main, "OpenAI"),
            patch.object(main, "run_agent", side_effect=[RuntimeError("provider down"), ("ok", 3, 2, [])]),
            patch.object(main, "render_pinned_status", return_value=True),
            patch.object(main, "restore_terminal"),
            patch.object(main, "render") as render,
        ):
            main.main()
        self.assertIn(
            ("output", "Error: provider down"),
            [call.args for call in render.call_args_list],
        )
        self.assertIn(("answer", "ok"), [call.args for call in render.call_args_list])

    def test_status_line_includes_k_directory_branch_and_metrics(self):
        with patch.object(main, "git_branch", return_value="main"):
            line = main.status_line(21, 9)
        self.assertIn("k", line)
        self.assertIn(str(Path.cwd()), line)
        self.assertIn("main", line)
        self.assertIn(f"model: {main.MODEL}", line)
        self.assertIn("tokens: 21", line)
        self.assertIn("context: 9", line)

    def test_status_line_omits_branch_when_unavailable(self):
        with patch.object(main, "git_branch", return_value=None):
            line = main.status_line(0, None)
        self.assertNotIn("branch:", line)
        self.assertIn("context: ?", line)

    def test_main_uses_pinned_status_and_the_new_prompt(self):
        with (
            patch("builtins.input", side_effect=[""]),
            patch.object(main, "OpenAI"),
            patch.object(main, "render_pinned_status", return_value=True) as status,
            patch.object(main, "restore_terminal") as restore,
        ):
            main.main()
        self.assertEqual(status.call_args.args[0], main.status_line("?", None))
        self.assertTrue(status.call_args.kwargs["move_cursor"])
        self.assertEqual(restore.call_count, 1)

    def test_main_exits_cleanly_on_eof(self):
        with (
            patch("builtins.input", side_effect=EOFError),
            patch.object(main, "OpenAI"),
            patch.object(main, "render_pinned_status", return_value=True),
            patch.object(main, "restore_terminal") as restore,
        ):
            main.main()
        self.assertEqual(restore.call_count, 1)

    def test_main_exits_cleanly_on_keyboard_interrupt(self):
        with (
            patch("builtins.input", side_effect=KeyboardInterrupt),
            patch.object(main, "OpenAI"),
            patch.object(main, "render_pinned_status", return_value=True),
            patch.object(main, "restore_terminal") as restore,
        ):
            main.main()
        self.assertEqual(restore.call_count, 1)

    def test_session_round_trip_uses_home_k_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = d
            try:
                session = {"id": "abc123", "history": [{"role": "user", "content": "hi"}], "transcript": []}
                main.save_session(session)
                self.assertTrue((Path(d) / ".k" / "sessions" / "abc123.json").exists())
                self.assertEqual(main.load_session("abc123"), session)
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

    def test_handle_command_recognizes_quit(self):
        self.assertEqual(main.handle_command("/quit"), "quit")

    def test_handle_command_rejects_unknown_commands(self):
        self.assertEqual(main.handle_command("/wat"), "Unknown command: /wat")
        self.assertIsNone(main.handle_command("write a file"))

    def test_available_skills_lists_user_skills(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main, "USER_SKILLS_DIR", Path(directory)):
            (Path(directory) / "coding" / "SKILL.md").parent.mkdir(exist_ok=True)
            (Path(directory) / "coding" / "SKILL.md").write_text("Use pytest.", encoding="utf-8")
            (Path(directory) / "review" / "SKILL.md").parent.mkdir(exist_ok=True)
            (Path(directory) / "review" / "SKILL.md").write_text("Review code.", encoding="utf-8")
            skills = main.available_skills()
            self.assertEqual(sorted(skills), ["coding", "review"])
            self.assertEqual(skills["coding"].read_text(encoding="utf-8"), "Use pytest.")

    def test_skill_instructions_appends_active_skill_and_reports_missing(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main, "USER_SKILLS_DIR", Path(directory)):
            (Path(directory) / "coding" / "SKILL.md").parent.mkdir(exist_ok=True)
            (Path(directory) / "coding" / "SKILL.md").write_text("Use pytest.", encoding="utf-8")
            instructions, missing = main.skill_instructions(["coding", "gone"])
            self.assertIn("## Active skills", instructions)
            self.assertIn("### coding\nUse pytest.", instructions)
            self.assertEqual(missing, ["gone"])

    def test_run_agent_sends_active_skill_in_instructions(self):
        response = SimpleNamespace(output=[], output_text="Done", usage=None)
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response)))
        with patch.object(main, "skill_instructions", return_value=("base\n## Active skills\n### coding", [])):
            main.run_agent("hello", client, skills=["coding"])
        self.assertEqual(client.responses.create.call_args.kwargs["instructions"], "base\n## Active skills\n### coding")

    def test_handle_skill_command_lists_loads_and_clears(self):
        session = {"skills": []}
        with patch.object(main, "available_skills", return_value={"coding": Path("skills/coding/SKILL.md")}):
            self.assertEqual(main.handle_skill_command("/skills", session), "Available skills: coding")
            self.assertEqual(main.handle_skill_command("/skill coding", session), "Loaded skill: coding")
            self.assertEqual(main.handle_skill_command("/skill coding", session), "Skill already loaded: coding")
            self.assertEqual(main.handle_skill_command("/skill clear", session), "Cleared skills")
        self.assertEqual(session["skills"], [])

    def test_handle_skill_command_rejects_bad_names(self):
        session = {"skills": []}
        self.assertEqual(main.handle_skill_command("/skill ../secret", session), "Invalid skill name")
        self.assertEqual(main.handle_skill_command("/skill missing", session), "Unknown skill: missing")

    def test_load_session_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main.Path, "home", return_value=Path(directory)):
            path = Path(directory) / ".k" / "sessions" / "bad.json"
            path.parent.mkdir(parents=True)
            path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot load session"):
                main.load_session("bad")

    def test_main_starts_new_session_when_resume_is_invalid(self):
        with (
            patch("sys.argv", ["k", "--resume", "bad"]),
            patch.object(main, "load_session", side_effect=ValueError("cannot load session: invalid JSON")),
            patch("builtins.input", side_effect=[""]),
            patch.object(main, "OpenAI"),
            patch.object(main, "render_pinned_status", return_value=True),
            patch.object(main, "restore_terminal"),
            patch.object(main, "render") as render,
        ):
            main.main()
        self.assertIn(("output", "Error: cannot load session: invalid JSON"), [call.args for call in render.call_args_list])

    def test_session_round_trip_keeps_skills(self):
        with tempfile.TemporaryDirectory() as d, patch.object(main.Path, "home", return_value=Path(d)):
            session = {"id": "abc123", "history": [], "transcript": [], "skills": ["coding"]}
            main.save_session(session)
            self.assertEqual(main.load_session("abc123")["skills"], ["coding"])
