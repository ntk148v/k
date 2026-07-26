import tempfile
import unittest
from pathlib import Path

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
