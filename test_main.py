import io
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
        first = SimpleNamespace(id="resp-1", output=[call], output_text="")
        second = SimpleNamespace(id="resp-2", output=[], output_text="Done")
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=[first, second])))

        with patch.object(main, "bash", return_value="hello"):
            self.assertEqual(main.run_agent("say hello", client), "Done")

        first_call, second_call = client.responses.create.call_args_list
        self.assertEqual(first_call.kwargs["instructions"], main.SYSTEM_PROMPT)
        self.assertEqual(first_call.kwargs["tools"], main.TOOLS)
        self.assertEqual(second_call.kwargs["previous_response_id"], "resp-1")
        self.assertEqual(second_call.kwargs["input"][0]["call_id"], "call-1")

    def test_render_has_no_ansi_when_stdout_is_not_a_tty(self):
        with patch.object(main.sys.stdout, "isatty", return_value=False), patch("sys.stdout", new_callable=io.StringIO) as output:
            main.render("tool", "read file.txt")
        self.assertNotIn("\x1b[", output.getvalue())
        self.assertIn("read file.txt", output.getvalue())
