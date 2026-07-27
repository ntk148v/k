import io
import shutil
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


class AgentTests(unittest.TestCase):
    def test_dispatch_reports_bad_json_and_unknown_tools(self):
        self.assertIn("invalid JSON", main.dispatch_tool("read", "{", "call-1")["output"])
        self.assertIn("Unknown tool", main.dispatch_tool("missing", "{}", "call-2")["output"])

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
            self.assertEqual(main.run_agent("say hello", client), ("Done", 21, 9))

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
        self.assertEqual(main.run_agent("say hello", client), ("Done", 0, None))

    def test_render_status_has_no_ansi_when_stdout_is_not_a_tty(self):
        with patch.object(main.sys.stdout, "isatty", return_value=False), patch("sys.stdout", new_callable=io.StringIO) as output:
            main.render("status", "model: openrouter/free | tokens: 21 | context: 9")
        self.assertEqual(output.getvalue(), "model: openrouter/free | tokens: 21 | context: 9\n")

    def test_render_status_colors_k_red_and_remainder_grey_in_a_tty(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(
            output, "isatty", return_value=True
        ):
            main.render("status", "k | /repo | main")
        self.assertEqual(
            output.getvalue(),
            "\033[31mk\033[90m | /repo | main\033[0m\n",
        )

    def test_render_pinned_status_reserves_first_row_and_repositions_cursor(self):
        size = SimpleNamespace(lines=24)
        with (
            patch("sys.stdout", new_callable=io.StringIO) as output,
            patch.object(output, "isatty", return_value=True),
            patch.object(main.shutil, "get_terminal_size", return_value=size),
        ):
            self.assertTrue(main.render_pinned_status("k | /repo"))
        self.assertEqual(
            output.getvalue(),
            "\033[2;24r\033[1;1H\033[2K\033[31mk\033[90m | /repo\033[0m\n\033[2;1H",
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

    def test_main_processes_multiple_tasks_until_blank_input(self):
        with (
            patch("builtins.input", side_effect=["first", "second", ""]),
            patch.object(main, "OpenAI"),
            patch.object(main, "run_agent", side_effect=[("one", 1, 1), ("two", 2, 2)]) as mock_agent,
            patch.object(main, "render") as render,
        ):
            main.main()
        self.assertEqual(mock_agent.call_args_list[0].args[0], "first")
        self.assertEqual(mock_agent.call_args_list[1].args[0], "second")
        self.assertEqual(mock_agent.call_count, 2)
        self.assertFalse(any(call.args[0] == "title" for call in render.call_args_list))

    def test_main_renders_error_and_continues_after_agent_failure(self):
        with (
            patch("builtins.input", side_effect=["broken", "working", ""]),
            patch.object(main, "OpenAI"),
            patch.object(main, "run_agent", side_effect=[RuntimeError("provider down"), ("ok", 3, 2)]),
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
