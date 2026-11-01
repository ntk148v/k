import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main


class IdentityTests(unittest.TestCase):
    """Tests for helper/identity functions that don't need complex mocking."""

    def test_load_config_reads_defaults_and_presets(self):
        with tempfile.TemporaryDirectory() as home, patch.object(main.Path, "home", return_value=Path(home)):
            (Path(home) / ".k").mkdir()
            (Path(home) / ".k/config.toml").write_text(
                "[defaults]\nmodel='base'\n[presets.fast]\nmodel='fast'\napi_mode='chat-completions'\n",
                encoding="utf-8",
            )
            config = main.load_config()
        self.assertEqual(config["defaults"]["model"], "base")
        self.assertEqual(config["presets"]["fast"]["model"], "fast")

    def test_load_config_ignores_malformed_files(self):
        with tempfile.TemporaryDirectory() as home, patch.object(main.Path, "home", return_value=Path(home)):
            (Path(home) / ".k").mkdir()
            (Path(home) / ".k/config.toml").write_text("[broken", encoding="utf-8")
            self.assertEqual(main.load_config(), {})

    def test_new_session_keeps_model_and_skills(self):
        session = {"id": "old", "model": "x", "api_mode": "responses", "skills": ["review"], "history": []}
        new = main.new_session_from(session)
        self.assertNotEqual(new["id"], "old")
        self.assertEqual(new["model"], "x")
        self.assertEqual(new["skills"], ["review"])

    def test_fork_session_copies_history_and_records_parent(self):
        session = {"id": "old", "history": [{"role": "user", "content": "keep"}], "skills": []}
        fork = main.fork_session(session)
        self.assertEqual(fork["forked_from"], "old")
        self.assertEqual(fork["history"], session["history"])
        self.assertIsNot(fork["history"], session["history"])

    def test_compact_session_replaces_history_with_summary(self):
        session = {"id": "old", "model": "x", "api_mode": "responses", "history": [{"role": "user", "content": "work"}]}
        response = SimpleNamespace(output_text="## Goal\n- work")
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response)))
        self.assertEqual(main.compact_session(session, client), "## Goal\n- work")
        self.assertEqual(session["history"][-1]["content"], "## Goal\n- work")

    def test_new_session_id_returns_formatted_stamp(self):
        sid = main.new_session_id()
        # Format: YYYYMMDD-HHMMSS-xxxxxx
        self.assertRegex(sid, r"^\d{8}-\d{6}-[0-9a-f]{6}$")

    def test_k_dir_returns_home_dot_k(self):
        self.assertEqual(str(main.k_dir()), str(Path.home() / ".k"))

    def test_sessions_dir_returns_k_sessions(self):
        self.assertEqual(main.sessions_dir(), Path.home() / ".k" / "sessions")

    def test_session_path_constructs_json_path(self):
        self.assertEqual(main.session_path("abc"), main.sessions_dir() / "abc.json")

    def test_session_path_rejects_invalid_ids(self):
        for bad in ("", ".", "..", "foo/bar", "foo\\bar"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError, msg=f"invalid session id not raised for {bad!r}"):
                    main.session_path(bad)

    def test_load_session_rejects_invalid_id(self):
        with self.assertRaisesRegex(ValueError, "cannot load session: invalid id"):
            main.load_session("../escape")

    def test_load_session_rejects_non_dict_json(self):
        with tempfile.TemporaryDirectory() as d, patch.object(main.Path, "home", return_value=Path(d)):
            path = main.session_path("arr")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected an object"):
                main.load_session("arr")

    def test_load_session_rejects_invalid_session_schema(self):
        cases = {
            "wrong-id": {"id": "other", "history": [], "transcript": []},
            "bad-history": {"id": "bad-history", "history": {}, "transcript": []},
            "bad-transcript": {"id": "bad-transcript", "history": [], "transcript": {}},
            "bad-skills": {"id": "bad-skills", "history": [], "transcript": [], "skills": {}},
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(main.Path, "home", return_value=Path(directory)):
            for session_id, session in cases.items():
                with self.subTest(session_id=session_id):
                    path = main.session_path(session_id)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(session), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "invalid session data"):
                        main.load_session(session_id)

    def test_load_session_rejects_present_optional_metadata_with_invalid_types(self):
        invalid_metadata = {
            "model": None,
            "title": 1,
            "cwd": [],
            "created_at": {},
            "updated_at": False,
            "undo": [],
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(main.Path, "home", return_value=Path(directory)):
            for field, value in invalid_metadata.items():
                with self.subTest(field=field):
                    session = {"id": field, "history": [], "transcript": [], field: value}
                    path = main.session_path(field)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(session), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "invalid session data"):
                        main.load_session(field)

    def test_load_session_reports_oserror(self):
        with patch.object(main.Path, "home", return_value=Path("/nonexistent-k-dir-for-testing")):
            with self.assertRaisesRegex(ValueError, "cannot load session"):
                main.load_session("ghost")

    def test_git_branch_returns_none_on_oserror(self):
        with patch.object(main.subprocess, "run", side_effect=OSError("no git")):
            self.assertIsNone(main.git_branch())

    def git(self, root: Path, *args: str) -> str:
        result = main.subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            stdout=main.subprocess.PIPE,
            stderr=main.subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def init_git_repo(self, directory: str) -> Path:
        root = Path(directory)
        self.git(root, "init", "-q")
        self.git(root, "config", "user.email", "k@example.test")
        self.git(root, "config", "user.name", "k")
        (root / "note.txt").write_text("before\n", encoding="utf-8")
        self.git(root, "add", "note.txt")
        self.git(root, "commit", "-qm", "init")
        return root

    def test_undo_checkpoint_is_unavailable_when_worktree_is_dirty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.init_git_repo(directory)
            (root / "note.txt").write_text("changed\n", encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(root)
                self.assertIsNone(main.begin_undo_checkpoint())
            finally:
                os.chdir(previous)

    def test_apply_undo_reverses_tracked_edit_and_removes_new_untracked_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.init_git_repo(directory)
            previous = Path.cwd()
            try:
                os.chdir(root)
                checkpoint = main.begin_undo_checkpoint()
                (root / "note.txt").write_text("after\n", encoding="utf-8")
                (root / "new.txt").write_text("new\n", encoding="utf-8")
                record = main.finish_undo_checkpoint(checkpoint)
                self.assertEqual(main.apply_undo(record), "Undo complete.")
                self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "before\n")
                self.assertFalse((root / "new.txt").exists())
                self.assertEqual(self.git(root, "status", "--porcelain"), "")
            finally:
                os.chdir(previous)

    def test_apply_undo_removes_untracked_file_with_no_tracked_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.init_git_repo(directory)
            previous = Path.cwd()
            try:
                os.chdir(root)
                checkpoint = main.begin_undo_checkpoint()
                (root / "file.txt").write_text("dmvt dmfpt\n", encoding="utf-8")
                record = main.finish_undo_checkpoint(checkpoint)
                self.assertEqual(main.apply_undo(record), "Undo complete.")
                self.assertFalse((root / "file.txt").exists())
                self.assertEqual(self.git(root, "status", "--porcelain"), "")
            finally:
                os.chdir(previous)

    def test_apply_undo_restores_staged_tracked_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.init_git_repo(directory)
            previous = Path.cwd()
            try:
                os.chdir(root)
                checkpoint = main.begin_undo_checkpoint()
                (root / "note.txt").write_text("after\n", encoding="utf-8")
                self.git(root, "add", "note.txt")
                record = main.finish_undo_checkpoint(checkpoint)
                self.assertEqual(main.apply_undo(record), "Undo complete.")
                self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "before\n")
                self.assertEqual(self.git(root, "status", "--porcelain"), "")
            finally:
                os.chdir(previous)

    def test_apply_undo_refuses_when_worktree_changed_after_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.init_git_repo(directory)
            previous = Path.cwd()
            try:
                os.chdir(root)
                checkpoint = main.begin_undo_checkpoint()
                (root / "note.txt").write_text("after\n", encoding="utf-8")
                record = main.finish_undo_checkpoint(checkpoint)
                (root / "later.txt").write_text("later\n", encoding="utf-8")
                self.assertIn("worktree changed", main.apply_undo(record))
                self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "after\n")
                self.assertEqual((root / "later.txt").read_text(encoding="utf-8"), "later\n")
            finally:
                os.chdir(previous)

    def test_format_markdown_handles_empty_string(self):
        self.assertEqual(main.format_markdown(""), "")

    def test_restore_terminal_does_nothing_when_not_a_tty(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(output, "isatty", return_value=False):
            main.restore_terminal()
        self.assertEqual(output.getvalue(), "")

    def test_render_pinned_status_falls_back_when_terminal_too_small(self):
        size = SimpleNamespace(lines=2)
        with (
            patch("sys.stdout", new_callable=io.StringIO) as output,
            patch.object(output, "isatty", return_value=True),
            patch.object(main.shutil, "get_terminal_size", return_value=size),
        ):
            result = main.render_pinned_status("k | /repo")
        self.assertFalse(result)
        self.assertIn("| /repo", output.getvalue())

    def test_set_theme_dark_switches_palette(self):
        main.set_theme("dark")
        self.assertIn("\033[38;2;138;190;183m", main.color("accent"))
        main.set_theme("light")
        self.assertIn("\033[38;2;90;128;128m", main.color("accent"))

    def test_load_session_adds_defaults_for_old_valid_session(self):
        with tempfile.TemporaryDirectory() as d, patch.object(main.Path, "home", return_value=Path(d)):
            session = {"id": "oldie", "history": [], "transcript": []}
            main.save_session(session)
            loaded = main.load_session("oldie")
        self.assertEqual(loaded["model"], main.MODEL)
        self.assertEqual(loaded["skills"], [])
        self.assertEqual(loaded["title"], "")
        self.assertEqual(loaded["cwd"], str(Path.cwd()))
        self.assertIsNone(loaded["undo"])

    def test_save_session_sets_created_and_updated_metadata(self):
        with tempfile.TemporaryDirectory() as d, patch.object(main.Path, "home", return_value=Path(d)):
            session = {"id": "abc123", "history": [], "transcript": []}
            main.save_session(session)
            saved = main.load_session("abc123")
        self.assertRegex(saved["created_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertRegex(saved["updated_at"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(saved["cwd"], str(Path.cwd()))
        self.assertEqual(saved["model"], main.MODEL)

    @unittest.skipIf(os.name == "nt", "POSIX permissions do not apply on Windows")
    def test_save_session_uses_owner_only_permissions(self):
        with tempfile.TemporaryDirectory() as home, patch.object(main.Path, "home", return_value=Path(home)):
            session = {"id": "session", "history": [], "transcript": []}
            main.save_session(session)
            path = main.session_path("session")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_save_session_twice_is_valid_json(self):
        with tempfile.TemporaryDirectory() as home, patch.object(main.Path, "home", return_value=Path(home)):
            session = {"id": "twice", "history": [], "transcript": []}
            main.save_session(session)
            main.save_session(session)
            self.assertEqual(main.load_session("twice")["id"], "twice")

    def test_list_sessions_is_newest_first_and_skips_invalid_files(self):
        with tempfile.TemporaryDirectory() as d, patch.object(main.Path, "home", return_value=Path(d)):
            early = {"id": "early", "history": [], "transcript": [], "updated_at": "2026-07-30T10:00:00+00:00"}
            later = {"id": "later", "history": [], "transcript": [], "updated_at": "2026-07-30T12:00:00+00:00"}
            for session in (later, early):
                path = main.session_path(session["id"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(session), encoding="utf-8")
            # Make the older persisted session newer on disk.
            (main.session_path("early")).touch()
            # Write an invalid JSON file.
            (main.session_path("bad")).write_text("not json", encoding="utf-8")
            sessions = main.list_sessions()
        self.assertEqual([s["id"] for s in sessions], ["later", "early"])

    def test_format_session_list_is_compact_and_handles_empty(self):
        self.assertEqual(main.format_session_list([]), "No saved sessions.")
        formatted = main.format_session_list(
            [
                {
                    "id": "abc123",
                    "title": "Fix parser",
                    "cwd": "/repo",
                    "updated_at": "2026-07-30T12:00:00+00:00",
                    "model": "small",
                }
            ]
        )
        self.assertIn("abc123", formatted)


class PromptTests(unittest.TestCase):
    def test_skill_instructions_includes_cwd_agents_md(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                Path("AGENTS.md").write_text("Run unit tests first.\n", encoding="utf-8")
                instructions, missing = main.skill_instructions([])
            finally:
                os.chdir(previous)
        self.assertEqual(missing, [])
        self.assertIn("## Project instructions", instructions)
        self.assertIn("Run unit tests first.", instructions)

    def test_skill_instructions_omits_missing_and_oversized_agents_md(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                missing_instructions, _ = main.skill_instructions([])
                Path("AGENTS.md").write_bytes(b"x" * (main.PROJECT_INSTRUCTIONS_MAX_BYTES + 1))
                oversized_instructions, _ = main.skill_instructions([])
            finally:
                os.chdir(previous)
        self.assertNotIn("## Project instructions", missing_instructions)
        self.assertNotIn("## Project instructions", oversized_instructions)

    def test_project_instructions_load_global_root_then_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "child"
            child.mkdir()
            home = root / "home"
            (home / ".k").mkdir(parents=True)
            (home / ".k" / "AGENTS.md").write_text("global", encoding="utf-8")
            (root / "AGENTS.md").write_text("root", encoding="utf-8")
            (child / "AGENTS.md").write_text("child", encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(child)
                with patch.object(main.Path, "home", return_value=home), patch.object(main, "_git_root", return_value=str(root)):
                    instructions = main.project_instructions()
            finally:
                os.chdir(previous)
        self.assertLess(instructions.index("global"), instructions.index("root"))
        self.assertLess(instructions.index("root"), instructions.index("child"))

    def test_project_instructions_outside_git_omits_parent_agents_md(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "child"
            child.mkdir()
            (root / "AGENTS.md").write_text("parent", encoding="utf-8")
            (child / "AGENTS.md").write_text("child", encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(child)
                with patch.object(main.Path, "home", return_value=root / "home"), patch.object(main, "_git_root", return_value=None):
                    instructions = main.project_instructions()
            finally:
                os.chdir(previous)
        self.assertIn("child", instructions)
        self.assertNotIn("parent", instructions)

    def test_skill_instructions_omits_invalid_utf8_agents_md(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                Path("AGENTS.md").write_bytes(b"\xff")
                instructions, _ = main.skill_instructions([])
            finally:
                os.chdir(previous)
        self.assertNotIn("## Project instructions", instructions)


class ReadlineTests(unittest.TestCase):
    def test_readline_is_loaded_for_interactive_history(self):
        self.assertIsNotNone(getattr(main, "readline", None))

    def test_ctrl_j_is_bound_to_insert_a_newline(self):
        with patch.object(main.readline, "parse_and_bind") as bind:
            main.configure_multiline_input()
        bind.assert_called_once_with(r'"\C-j": "\C-v\C-j"')


class ToolTests(unittest.TestCase):
    def setUp(self):
        self._previous_cwd = Path.cwd()
        self._workspace = tempfile.TemporaryDirectory()
        main.READ_STAMPS.clear()
        os.chdir(self._workspace.name)

    def tearDown(self):
        main.READ_STAMPS.clear()
        os.chdir(self._previous_cwd)
        self._workspace.cleanup()

    def test_grep_returns_numbered_matching_lines(self):
        Path("src").mkdir()
        Path("src/a.py").write_text("x = 1\n# TODO: test\n", encoding="utf-8")
        self.assertEqual(main.grep("TODO", "*.py"), "src/a.py:2: # TODO: test")

    def test_grep_skips_git_directory_and_rejects_invalid_regex(self):
        Path(".git").mkdir()
        Path(".git/ignored.py").write_text("TODO\n", encoding="utf-8")
        self.assertEqual(main.grep("TODO", "*.py"), "No matches.")
        self.assertTrue(main.grep("[", "*.py").startswith("Error: invalid regex:"))

    def test_read_respects_offset_and_limit(self):
        Path("notes.txt").write_text("first\nsecond\nthird\n", encoding="utf-8")
        self.assertEqual(main.read("notes.txt", offset=2, limit=1), "2: second")

    def test_read_returns_an_image_attachment(self):
        Path("pixel.png").write_bytes(b"png-bytes")
        result = main.read("pixel.png")
        self.assertEqual(result[0]["type"], "input_image")
        self.assertTrue(result[0]["image_url"].startswith("data:image/png;base64,"))

    def test_read_rejects_text_file_larger_than_limit(self):
        with patch.object(main, "MAX_TEXT_FILE_BYTES", 4):
            Path("large.txt").write_text("12345", encoding="utf-8")
            self.assertEqual(main.read("large.txt"), "Error: file exceeds 4-byte limit")

    def test_read_rejects_image_larger_than_limit(self):
        with patch.object(main, "MAX_IMAGE_FILE_BYTES", 4):
            Path("large.png").write_bytes(b"12345")
            self.assertEqual(main.read("large.png"), "Error: image exceeds 4-byte limit")

    def test_write_creates_parent_directories(self):
        result = main.write("new/file.txt", "hello")
        self.assertTrue(result.startswith("Wrote "))
        self.assertTrue(result.endswith("new/file.txt"))
        self.assertEqual(Path("new/file.txt").read_text(encoding="utf-8"), "hello")

    def test_edit_replaces_only_one_exact_match(self):
        Path("file.txt").write_text("before OLD after", encoding="utf-8")
        result = main.edit("file.txt", "OLD", "NEW")
        self.assertIn("--- a/file.txt", result)
        self.assertIn("+++ b/file.txt", result)
        self.assertIn("-before OLD after", result)
        self.assertIn("+before NEW after", result)
        self.assertEqual(Path("file.txt").read_text(encoding="utf-8"), "before NEW after")

    def test_edit_rejects_file_changed_since_read(self):
        Path("file.txt").write_text("before\n", encoding="utf-8")
        self.assertEqual(main.read("file.txt"), "1: before")
        Path("file.txt").write_text("external\n", encoding="utf-8")
        result = main.edit("file.txt", "external", "after")
        self.assertEqual(result, "Error: file changed since read; re-read before editing")
        self.assertEqual(Path("file.txt").read_text(encoding="utf-8"), "external\n")

    def test_edit_allows_re_read_after_file_changes(self):
        Path("file.txt").write_text("before\n", encoding="utf-8")
        main.read("file.txt")
        Path("file.txt").write_text("external\n", encoding="utf-8")
        self.assertEqual(main.read("file.txt"), "1: external")
        result = main.edit("file.txt", "external", "after")
        self.assertIn("--- a/file.txt", result)
        self.assertIn("+++ b/file.txt", result)
        self.assertIn("-external", result)
        self.assertIn("+after", result)
        self.assertEqual(Path("file.txt").read_text(encoding="utf-8"), "after\n")

    def test_write_is_not_blocked_by_a_stale_read(self):
        Path("file.txt").write_text("before\n", encoding="utf-8")
        main.read("file.txt")
        Path("file.txt").write_text("external\n", encoding="utf-8")
        self.assertTrue(main.write("file.txt", "replacement\n").startswith("Wrote "))
        self.assertEqual(Path("file.txt").read_text(encoding="utf-8"), "replacement\n")

    def test_edit_rejects_ambiguous_text_without_writing(self):
        Path("file.txt").write_text("OLD OLD", encoding="utf-8")
        result = main.edit("file.txt", "OLD", "NEW")
        self.assertIn("exactly once", result)
        self.assertEqual(Path("file.txt").read_text(encoding="utf-8"), "OLD OLD")

    def test_read_rejects_path_outside_project(self):
        with tempfile.TemporaryDirectory() as outside:
            Path(outside, "secret.txt").write_text("secret", encoding="utf-8")
            self.assertIn("outside the project", main.read(str(Path(outside, "secret.txt"))))
            self.assertIn("outside the project", main.read(f"../{Path(outside).name}/secret.txt"))

    @unittest.skipIf(os.name == "nt", "symlinks require privileges on Windows")
    def test_write_rejects_symlink_escaping_project(self):
        with tempfile.TemporaryDirectory() as outside:
            Path("escape").symlink_to(outside, target_is_directory=True)
            result = main.write("escape/leak.txt", "no")
            self.assertIn("outside the project", result)
            self.assertFalse(Path(outside, "leak.txt").exists())

    def test_bash_returns_combined_output(self):
        self.assertIn("hello", main.bash("python -c \"print('hello')\""))

    def test_read_rejects_non_positive_paging(self):
        self.assertEqual(main.read("notes.txt", offset=0), "Error: offset must be at least 1")
        self.assertEqual(main.read("notes.txt", limit=0), "Error: limit must be at least 1")

    def test_tools_return_errors_for_missing_files(self):
        self.assertTrue(main.read("definitely-missing-k-file.txt").startswith("Error: cannot read"))
        self.assertTrue(main.edit("definitely-missing-k-file.txt", "old", "new").startswith("Error: cannot edit"))

    def test_bash_terminates_process_on_keyboard_interrupt(self):
        process = Mock()
        process.communicate.side_effect = [KeyboardInterrupt, ("", None)]
        with patch.object(main.subprocess, "Popen", return_value=process), patch.object(main.os, "killpg") as killpg:
            self.assertEqual(main.bash("sleep 60"), "Error: command interrupted")
        killpg.assert_called_once_with(process.pid, main.signal.SIGTERM)

    def test_bash_timeout_returns_error_message(self):
        process = Mock()
        process.communicate.side_effect = [main.subprocess.TimeoutExpired("cmd", 1), ("", None)]
        with patch.object(main.subprocess, "Popen", return_value=process), patch.object(main.os, "killpg"):
            result = main.bash("sleep 60", timeout=1)
        self.assertTrue(result.startswith("Error: command timed out"))

    def test_bash_uses_default_timeout(self):
        process = Mock()
        process.communicate.return_value = ("", None)
        process.returncode = 0
        with patch.object(main.subprocess, "Popen", return_value=process):
            main.bash("echo hi")
        self.assertEqual(process.communicate.call_args.args, ())

    def test_bash_rejects_invalid_timeout_before_running(self):
        with patch.object(main.subprocess, "run") as run:
            result = main.bash("echo hi", timeout=0)
        run.assert_not_called()
        self.assertIn("timeout must be between 1 and", result)

    def test_bash_oserror_returns_error_message(self):
        with patch.object(main.subprocess, "Popen", side_effect=OSError("broken")):
            result = main.bash("anything")
        self.assertIn("Error:", result)
        self.assertIn("broken", result)

    def test_write_returns_error_for_unwritable_path(self):
        Path("blocker.txt").write_text("block", encoding="utf-8")
        result = main.write("blocker.txt/sub/file.txt", "content")
        self.assertTrue(result.startswith("Error: cannot write"))

    def test_dispatch_tool_catches_handler_exception(self):
        def broken(**kwargs):
            raise ValueError("oops")

        with patch.dict(main.TOOL_HANDLERS, {"broken": broken}):
            result = main.dispatch_tool("broken", "{}", "call-1")
        self.assertEqual(result["call_id"], "call-1")
        self.assertIn("oops", result["output"])

    def test_dispatch_tool_returns_list_output_untouched(self):
        with patch.dict(main.TOOL_HANDLERS, {"list_tool": lambda **kw: [{"type": "input_image", "image_url": "data:img"}]}):
            result = main.dispatch_tool("list_tool", "{}", "call-1")
        self.assertEqual(result["output"], [{"type": "input_image", "image_url": "data:img"}])

    def test_dispatch_requires_confirmation_for_every_bash_command(self):
        for command in ("echo ok", "rm -r tmp", "git push --force=origin main", "find . -delete"):
            with self.subTest(command=command):
                result = main.dispatch_tool("bash", json.dumps({"command": command}), "call")
                self.assertEqual(result["output"], "Error: bash command rejected by user")

    def test_dispatch_rejects_bash_when_callback_declines(self):
        result = main.dispatch_tool("bash", json.dumps({"command": "echo hi"}), "call", confirm_bash=lambda _: False)
        self.assertEqual(result["output"], "Error: bash command rejected by user")

    def test_dispatch_runs_bash_only_after_confirmation(self):
        result = main.dispatch_tool("bash", json.dumps({"command": "printf ok"}), "call", confirm_bash=lambda command: command == "printf ok")
        self.assertEqual(result["output"], "ok")

    def test_dispatch_reports_activity_immediately_before_the_handler(self):
        events = []

        def handler(**kwargs):
            events.append("handler")
            return "ok"

        with patch.dict(main.TOOL_HANDLERS, {"read": handler}):
            main.dispatch_tool("read", '{"path":"note.txt"}', "call-1", on_activity=events.append)

        self.assertEqual(events, ["read", "handler"])


class AgentTests(unittest.TestCase):
    def test_review_confirmation_shows_command(self):
        with patch("builtins.input", return_value="y"), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            stdout.isatty = Mock(return_value=True)
            self.assertTrue(main.confirm_bash("git status", "review"))
        self.assertIn("git status", stdout.getvalue())
        self.assertIn("Allow bash command", stdout.getvalue())

    def test_yolo_confirmation_skips_prompt(self):
        with patch("builtins.input") as ask:
            self.assertTrue(main.confirm_bash("rm -rf build", "yolo"))
        ask.assert_not_called()

    def test_command_help_lists_descriptions_and_usage(self):
        help_text = main.command_help()
        self.assertIn("/help", help_text)
        self.assertIn("/mode <review|yolo>", help_text)
        self.assertIn("/display <formatted|stream>", help_text)
        self.assertIn("Show this command list", help_text)

    def test_permission_mode_rejects_unknown_value(self):
        with self.assertRaises(ValueError):
            main.validate_permission_mode("maybe")

    def test_display_mode_rejects_unknown_value(self):
        with self.assertRaises(ValueError):
            main.validate_display_mode("maybe")

    def test_stream_emits_text_deltas_to_callback(self):
        events = [
            SimpleNamespace(type="response.output_text.delta", delta="hello"),
            SimpleNamespace(type="response.output_text.delta", delta=" world"),
        ]
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=events)))
        chunks = []
        main.run_agent("say hello", client, stream=True, quiet=True, on_text=chunks.append)
        self.assertEqual(chunks, ["hello", " world"])

    def test_stream_assembler_joins_tool_argument_deltas(self):
        assembler = main.StreamAssembler()
        assembler.consume({"type": "tool_start", "index": 0, "id": "c1", "name": "read"})
        assembler.consume({"type": "tool_arguments_delta", "index": 0, "arguments": '{"path":"a'})
        assembler.consume({"type": "tool_arguments_delta", "index": 0, "arguments": '.py"}'})
        self.assertEqual(assembler.tool_calls()[0]["arguments"], '{"path":"a.py"}')

    def test_stream_assembler_collects_text_deltas(self):
        assembler = main.StreamAssembler()
        assembler.consume({"type": "text_delta", "text": "hello "})
        assembler.consume({"type": "text_delta", "text": "world"})
        self.assertEqual(assembler.text(), "hello world")

    def test_run_agent_streams_response_text_deltas(self):
        events = [
            SimpleNamespace(type="response.output_text.delta", delta="hello"),
            SimpleNamespace(type="response.output_text.delta", delta=" world"),
        ]
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=events)))
        answer, _, _, _ = main.run_agent("say hello", client, stream=True, quiet=True)
        self.assertEqual(answer, "hello world")
        self.assertTrue(client.responses.create.call_args.kwargs["stream"])

    def test_streamed_markdown_uses_answer_renderer(self):
        events = [SimpleNamespace(type="response.output_text.delta", delta="**hello**")]
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=events)))
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            stdout.isatty = Mock(return_value=True)
            answer, _, _, _ = main.run_agent("say hello", client, stream=True)
            self.assertEqual(stdout.getvalue(), "")
            main.render("answer", answer)
        self.assertIn("\033[1mhello", stdout.getvalue())

    def test_main_print_mode_writes_only_final_answer_to_stdout(self):
        with (
            patch.object(sys, "argv", ["k", "-p", "describe main"]),
            patch.object(main, "OpenAI"),
            patch.object(main, "run_agent", return_value=("answer", 1, 1, [])),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
            patch("sys.stderr", new_callable=io.StringIO),
        ):
            main.main()
        self.assertEqual(stdout.getvalue(), "answer\n")

    def test_main_print_mode_rejects_bash_without_a_tty(self):
        result = main.dispatch_tool("bash", '{"command":"echo no"}', "call")
        self.assertEqual(result["output"], "Error: bash command rejected by user")

    def setUp(self):
        self.api_mode = main.API_MODE
        main.API_MODE = "responses"

    def tearDown(self):
        main.API_MODE = self.api_mode

    def test_dispatch_reports_bad_json_and_unknown_tools(self):
        self.assertIn("invalid JSON", main.dispatch_tool("read", "{", "call-1")["output"])
        self.assertIn("Unknown tool", main.dispatch_tool("missing", "{}", "call-2")["output"])

    def test_run_agent_uses_explicit_session_model_for_responses(self):
        call = SimpleNamespace(type="function_call", name="bash", arguments='{"command": "echo hello"}', call_id="call-1")
        first = SimpleNamespace(
            id="resp-1",
            output=[call],
            output_text="",
            usage=SimpleNamespace(input_tokens=5, output_tokens=3),
        )
        second = SimpleNamespace(
            id="resp-2",
            output=[],
            output_text="Done",
            usage=SimpleNamespace(input_tokens=9, output_tokens=4),
        )
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=[first, second])))

        with patch.object(main, "bash", return_value="hello"):
            main.run_agent("say hello", client, model="provider/model-x", confirm_bash=lambda _: True)

        first_call = client.responses.create.call_args_list[0]
        self.assertEqual(first_call.kwargs["model"], "provider/model-x")

    def test_run_agent_uses_explicit_session_model_for_chat(self):
        message = SimpleNamespace(content="Done", tool_calls=[])
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=response))))

        with patch.object(main, "API_MODE", "chat-completions"):
            main.run_agent("hello", client, model="provider/model-y")

        first_call = client.chat.completions.create.call_args_list[0]
        self.assertEqual(first_call.kwargs["model"], "provider/model-y")

    def test_responses_agent_announces_each_tool_only_when_it_dispatches(self):
        calls = [
            SimpleNamespace(type="function_call", name="read", arguments='{"path":"a.txt"}', call_id="call-1"),
            SimpleNamespace(type="function_call", name="bash", arguments='{"command":"echo ok"}', call_id="call-2"),
        ]
        first = SimpleNamespace(id="resp-1", output=calls, output_text="", usage=None)
        second = SimpleNamespace(id="resp-2", output=[], output_text="Done", usage=None)
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=[first, second])))
        events = []

        def read_handler(**kwargs):
            events.append("read handler")
            return "read"

        def bash_handler(**kwargs):
            events.append("bash handler")
            return "bash"

        with patch.dict(main.TOOL_HANDLERS, {"read": read_handler, "bash": bash_handler}):
            main.run_agent("go", client, on_activity=events.append, confirm_bash=lambda _: True)

        self.assertEqual(events, ["thinking", "read", "read handler", "bash", "bash handler", "thinking"])

    def test_responses_agent_stops_after_max_tool_turns(self):
        call = SimpleNamespace(type="function_call", name="bash", arguments='{"command": "echo ok"}', call_id="call-1")
        response = SimpleNamespace(output=[call], output_text="", usage=None)
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response)))
        with patch.dict(main.TOOL_HANDLERS, {"bash": Mock(return_value="ok")}):
            with self.assertRaisesRegex(RuntimeError, "agent exceeded 20 tool turns"):
                main.run_agent("go", client)
        self.assertEqual(client.responses.create.call_count, 21)

    def test_chat_agent_stops_after_max_tool_turns(self):
        call = SimpleNamespace(id="call-1", function=SimpleNamespace(name="bash", arguments='{"command": "echo ok"}'))
        message = SimpleNamespace(content=None, tool_calls=[call])
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=response))))
        with patch.dict(main.TOOL_HANDLERS, {"bash": Mock(return_value="ok")}), patch.object(main, "API_MODE", "chat-completions"):
            with self.assertRaisesRegex(RuntimeError, "agent exceeded 20 tool turns"):
                main.run_agent("go", client)
        self.assertEqual(client.chat.completions.create.call_count, 21)

    def test_responses_agent_trims_history_before_request(self):
        response = SimpleNamespace(output=[], output_text="Done", usage=None)
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response)))
        history = [{"role": "user", "content": f"msg {i}"} for i in range(201)]
        main.run_agent("next", client, history)
        sent = client.responses.create.call_args.kwargs["input"]
        self.assertEqual(len(sent), 200)
        self.assertEqual(sent[0], {"role": "user", "content": "msg 2"})
        self.assertEqual(sent[-1], {"role": "user", "content": "next"})

    def test_chat_agent_trims_history_to_limit(self):
        message = SimpleNamespace(content="Done", tool_calls=[])
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=response))))
        history = [{"role": "user", "content": f"msg {i}"} for i in range(201)]
        with patch.object(main, "API_MODE", "chat-completions"):
            main.run_agent("next", client, history)
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(len(messages), 201)
        self.assertEqual(messages[1], {"role": "user", "content": "msg 2"})
        self.assertEqual(messages[-1], {"role": "user", "content": "next"})

    def test_chat_agent_trims_history_and_drops_orphan_tool_message(self):
        message = SimpleNamespace(content="Done", tool_calls=[])
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=response))))
        history = []
        for i in range(67):
            history.append({"role": "user", "content": f"u{i}"})
            history.append({"role": "assistant", "content": f"a{i}", "tool_calls": [{"id": f"c{i}"}]})
            history.append({"role": "tool", "tool_call_id": f"c{i}", "content": "r"})
        with patch.object(main, "API_MODE", "chat-completions"):
            main.run_agent("go", client, history)
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(len(messages), 200)
        self.assertEqual(messages[1], {"role": "user", "content": "u1"})
        self.assertNotEqual(messages[1].get("role"), "tool")

    def test_compact_output_keeps_short_text(self):
        self.assertEqual(main.compact_output("short", "bash", "s1"), "short")

    def test_compact_output_saves_large_text_next_to_session(self):
        with tempfile.TemporaryDirectory() as d, patch.object(main.Path, "home", return_value=Path(d)):
            text = "x" * (main.MAX_TOOL_OUTPUT_CHARS + 50)
            compact = main.compact_output(text, "bash", "s1")
            self.assertIn("Full output saved:", compact)
            artifact_dir = Path(d) / ".k" / "sessions" / "s1-artifacts"
            saved = list(artifact_dir.glob("bash-*.txt"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].read_text(encoding="utf-8"), text)

    def test_run_agent_returns_final_text_after_a_tool_call(self):
        call = SimpleNamespace(type="function_call", name="bash", arguments='{"command": "echo hello"}', call_id="call-1")
        first = SimpleNamespace(
            id="resp-1",
            output=[call],
            output_text="",
            usage=SimpleNamespace(input_tokens=5, output_tokens=3),
        )
        second = SimpleNamespace(
            id="resp-2",
            output=[],
            output_text="Done",
            usage=SimpleNamespace(input_tokens=9, output_tokens=4),
        )
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=[first, second])))

        with patch.object(main, "bash", return_value="hello"):
            self.assertEqual(main.run_agent("say hello", client, confirm_bash=lambda _: True)[:3], ("Done", 21, 9))

        first_call, second_call = client.responses.create.call_args_list
        self.assertEqual(first_call.kwargs["model"], main.MODEL)
        self.assertEqual(first_call.kwargs["input"], [{"role": "user", "content": "say hello"}])
        self.assertEqual(second_call.kwargs["model"], main.MODEL)
        self.assertNotIn("previous_response_id", second_call.kwargs)
        self.assertEqual(second_call.kwargs["input"][0], {"role": "user", "content": "say hello"})
        self.assertIn(call, second_call.kwargs["input"])
        self.assertEqual(second_call.kwargs["input"][-1]["call_id"], "call-1")

    def test_run_agent_uses_chat_completions_for_compatible_providers(self):
        call = SimpleNamespace(id="call-1", function=SimpleNamespace(name="bash", arguments='{"command": "echo hello"}'))
        first_message = SimpleNamespace(content=None, tool_calls=[call])
        second_message = SimpleNamespace(content="Done", tool_calls=[])
        first = SimpleNamespace(choices=[SimpleNamespace(message=first_message)], usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3))
        second = SimpleNamespace(choices=[SimpleNamespace(message=second_message)], usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(side_effect=[first, second]))))

        with patch.object(main, "API_MODE", "chat-completions"), patch.dict(main.TOOL_HANDLERS, {"bash": Mock(return_value="hello")}):
            result = main.run_agent("say hello", client, confirm_bash=lambda _: True)
        self.assertEqual(result[:3], ("Done", 21, 9))
        self.assertNotIn("tool_calls", result[3][-1])

        first_call, second_call = client.chat.completions.create.call_args_list
        self.assertEqual(first_call.kwargs["model"], main.MODEL)
        self.assertEqual(first_call.kwargs["messages"], [{"role": "system", "content": main.SYSTEM_PROMPT}, {"role": "user", "content": "say hello"}])
        self.assertEqual(second_call.kwargs["messages"][-1], {"role": "tool", "tool_call_id": "call-1", "content": "hello"})
        self.assertEqual(first_call.kwargs["tools"], main.CHAT_TOOLS)

    def test_run_agent_repairs_empty_chat_tool_calls_in_saved_history(self):
        message = SimpleNamespace(content="Hi", tool_calls=[])
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=response))))
        history = [{"role": "assistant", "content": "old reply", "tool_calls": []}]

        with patch.object(main, "API_MODE", "chat-completions"):
            main.run_agent("hello", client, history)

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(messages[1], {"role": "assistant", "content": "old reply"})

    def test_chat_agent_omits_invalid_legacy_assistant_message(self):
        message = SimpleNamespace(content="Done", tool_calls=[])
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=response))))
        history = [
            {"role": "assistant", "content": None},
            {"role": "assistant", "content": "Earlier answer"},
        ]

        with patch.object(main, "API_MODE", "chat-completions"):
            _, _, _, saved_history = main.run_agent("next task", client, history)

        sent = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertNotIn({"role": "assistant", "content": None}, sent)
        self.assertNotIn({"role": "assistant", "content": None}, saved_history)
        self.assertIn({"role": "assistant", "content": "Earlier answer"}, sent)

    def test_chat_agent_rejects_empty_final_provider_message(self):
        message = SimpleNamespace(content=None, tool_calls=[])
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=response))))

        with patch.object(main, "API_MODE", "chat-completions"):
            with self.assertRaisesRegex(RuntimeError, "provider response is malformed"):
                main.run_agent("next task", client)

    def test_run_agent_rejects_unknown_api_mode(self):
        with patch.object(main, "API_MODE", "unknown"):
            with self.assertRaisesRegex(RuntimeError, "K_API_MODE"):
                main.run_agent("hello", SimpleNamespace())

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
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(output, "isatty", return_value=True):
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
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(output, "isatty", return_value=False):
            self.assertFalse(main.render_pinned_status("k | /repo"))
        self.assertEqual(output.getvalue(), "k | /repo\n")

    def test_restore_terminal_restores_the_default_scroll_region(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(output, "isatty", return_value=True):
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
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(output, "isatty", return_value=True):
            main.render("tool", "read file.txt")
        self.assertEqual(output.getvalue(), "  \033[38;2;84;125;167mread file.txt\033[0m\n")

    def test_render_error_uses_bright_red(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(output, "isatty", return_value=True):
            main.render("output", "Error: something went wrong")
        self.assertEqual(output.getvalue(), "\033[38;2;170;85;85mError: something went wrong\033[0m\n")

    def test_render_info_uses_green(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(output, "isatty", return_value=True):
            main.render("info", "Undo complete.")
        self.assertEqual(output.getvalue(), "\033[38;2;79;143;79mUndo complete.\033[0m\n")

    def test_render_info_plain_when_not_a_tty(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(output, "isatty", return_value=False):
            main.render("info", "Undo complete.")
        self.assertEqual(output.getvalue(), "Undo complete.\n")

    def test_render_answer_uses_default_terminal_foreground(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(output, "isatty", return_value=True):
            main.render("answer", "plain text")
        self.assertEqual(output.getvalue(), "\033[38;2;31;35;40mplain text\033[0m\n")

    def test_render_answer_plain_when_not_a_tty(self):
        with patch("sys.stdout", new_callable=io.StringIO) as output, patch.object(output, "isatty", return_value=False):
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

    def test_main_passes_multiline_task_to_agent(self):
        with (
            patch("builtins.input", side_effect=["first line\nsecond line", ""]),
            patch.object(main, "OpenAI"),
            patch.object(main, "run_agent", return_value=("ok", 1, 1, [])) as agent,
            patch.object(main, "render_pinned_status", return_value=True),
            patch.object(main, "restore_terminal"),
            patch.object(main, "render"),
        ):
            main.main()
        self.assertEqual(agent.call_args.args[0], "first line\nsecond line")

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

    def test_status_line_uses_passed_model_and_activity(self):
        line = main.status_line(21, 9, "provider/model-x", "thinking")
        self.assertIn("model: provider/model-x", line)
        self.assertIn("thinking", line)

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

    def test_main_shows_resume_command_on_eof(self):
        with (
            patch("sys.argv", ["./launch-k"]),
            patch("builtins.input", side_effect=EOFError),
            patch.object(main, "OpenAI"),
            patch.object(main, "new_session_id", return_value="resume-id"),
            patch.object(main, "render_pinned_status", return_value=True),
            patch.object(main, "restore_terminal"),
            patch.object(main, "render") as render,
        ):
            main.main()
        self.assertIn(
            ("info", "To resume this session: ./launch-k --resume resume-id"),
            [call.args for call in render.call_args_list],
        )

    def test_main_shows_resume_command_on_unexpected_error(self):
        with (
            patch("sys.argv", ["./launch-k"]),
            patch("builtins.input", side_effect=RuntimeError("boom")),
            patch.object(main, "OpenAI"),
            patch.object(main, "new_session_id", return_value="resume-id"),
            patch.object(main, "render_pinned_status", return_value=True),
            patch.object(main, "restore_terminal"),
            patch.object(main, "render") as render,
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                main.main()
        self.assertIn(
            ("info", "To resume this session: ./launch-k --resume resume-id"),
            [call.args for call in render.call_args_list],
        )

    def test_main_persists_history_for_resume(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main.Path, "home", return_value=Path(directory)):
            with (
                patch("builtins.input", side_effect=["hello", ""]),
                patch.object(main, "OpenAI"),
                patch.object(main, "new_session_id", return_value="resume-id"),
                patch.object(main, "run_agent", return_value=("hi", 1, 1, ["saved-history"])),
                patch.object(main, "render_pinned_status", return_value=True),
                patch.object(main, "restore_terminal"),
                patch.object(main, "render"),
            ):
                main.main()
            self.assertEqual(main.load_session("resume-id")["history"], ["saved-history"])

    def test_main_persists_session_before_first_prompt(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main.Path, "home", return_value=Path(directory)):
            with (
                patch("builtins.input", side_effect=EOFError),
                patch.object(main, "OpenAI"),
                patch.object(main, "new_session_id", return_value="resume-id"),
                patch.object(main, "render_pinned_status", return_value=True),
                patch.object(main, "restore_terminal"),
                patch.object(main, "render"),
            ):
                main.main()
            self.assertEqual(main.load_session("resume-id")["id"], "resume-id")

    def test_main_lists_sessions_for_resume_command(self):
        sessions = [{"id": "saved-id", "title": "Saved", "model": "provider/model-x", "cwd": "", "updated_at": ""}]
        with (
            patch("builtins.input", side_effect=["/resume", ""]),
            patch.object(main, "OpenAI"),
            patch.object(main, "list_sessions", return_value=sessions),
            patch.object(main, "run_agent") as agent,
            patch.object(main, "render_pinned_status", return_value=True),
            patch.object(main, "restore_terminal"),
            patch.object(main, "render") as render,
        ):
            main.main()
        self.assertIn(("info", main.format_session_list(sessions)), [call.args for call in render.call_args_list])
        agent.assert_not_called()

    def test_main_switches_session_for_resume_id(self):
        saved = {"id": "saved-id", "history": [], "transcript": [], "skills": [], "api_mode": "responses", "model": "provider/model-x"}
        with (
            patch("builtins.input", side_effect=["/resume saved-id", ""]),
            patch.object(main, "OpenAI"),
            patch.object(main, "load_session", return_value=saved),
            patch.object(main, "save_session") as save,
            patch.object(main, "render_pinned_status", return_value=True) as status,
            patch.object(main, "restore_terminal"),
            patch.object(main, "render") as render,
        ):
            main.main()
        self.assertEqual(save.call_args.args[0]["id"], "saved-id")
        self.assertEqual(save.call_args.args[0]["cwd"], str(Path.cwd()))
        self.assertIn(("info", "Resumed saved-id"), [call.args for call in render.call_args_list])
        self.assertTrue(any("model: provider/model-x" in call.args[0] for call in status.call_args_list))

    def test_main_sets_and_persists_display_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main.Path, "home", return_value=Path(directory)):
            with (
                patch("builtins.input", side_effect=["/display stream", "hello", ""]),
                patch.object(main, "OpenAI"),
                patch.object(main, "new_session_id", return_value="display-id"),
                patch.object(main, "begin_undo_checkpoint", return_value=None),
                patch.object(main, "finish_undo_checkpoint", return_value=None),
                patch.object(main, "run_agent", return_value=("hi", 1, 1, [])),
                patch.object(main, "render_pinned_status", return_value=True),
                patch.object(main, "restore_terminal"),
                patch.object(main, "render"),
            ):
                main.main()
            self.assertEqual(main.load_session("display-id")["display_mode"], "stream")

    def test_main_sets_and_persists_permission_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main.Path, "home", return_value=Path(directory)):
            with (
                patch("builtins.input", side_effect=["/mode yolo", "hello", ""]),
                patch.object(main, "OpenAI"),
                patch.object(main, "new_session_id", return_value="mode-id"),
                patch.object(main, "begin_undo_checkpoint", return_value=None),
                patch.object(main, "finish_undo_checkpoint", return_value=None),
                patch.object(main, "run_agent", return_value=("hi", 1, 1, [])),
                patch.object(main, "render_pinned_status", return_value=True),
                patch.object(main, "restore_terminal"),
                patch.object(main, "render"),
            ):
                main.main()
            self.assertEqual(main.load_session("mode-id")["permission_mode"], "yolo")

    def test_main_sets_and_persists_session_model(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main.Path, "home", return_value=Path(directory)):
            with (
                patch("builtins.input", side_effect=["/model provider/model-x", "hello", ""]),
                patch.object(main, "OpenAI"),
                patch.object(main, "new_session_id", return_value="model-id"),
                patch.object(main, "begin_undo_checkpoint", return_value=None),
                patch.object(main, "finish_undo_checkpoint", return_value=None),
                patch.object(main, "run_agent", return_value=("hi", 1, 1, [])) as agent,
                patch.object(main, "render_pinned_status", return_value=True),
                patch.object(main, "restore_terminal"),
                patch.object(main, "render"),
            ):
                main.main()
            self.assertEqual(agent.call_args.kwargs["model"], "provider/model-x")
            self.assertEqual(main.load_session("model-id")["model"], "provider/model-x")

    def test_main_undo_clears_record_after_success(self):
        saved = {"id": "saved-id", "history": [], "transcript": [], "skills": [], "api_mode": "responses", "undo": {"root": "/repo"}}
        with (
            patch("sys.argv", ["k", "--resume", "saved-id"]),
            patch("builtins.input", side_effect=["/undo", ""]),
            patch.object(main, "OpenAI"),
            patch.object(main, "load_session", return_value=saved),
            patch.object(main, "apply_undo", return_value="Undo complete."),
            patch.object(main, "save_session") as save,
            patch.object(main, "render_pinned_status", return_value=True),
            patch.object(main, "restore_terminal"),
            patch.object(main, "render"),
        ):
            main.main()
        self.assertIsNone(save.call_args.args[0]["undo"])

    def test_main_rejects_bash_without_tty_confirmation(self):
        call = SimpleNamespace(type="function_call", name="bash", arguments='{"command": "rm -rf build"}', call_id="call-1")
        call.to_dict = lambda: {"type": "function_call", "name": "bash", "arguments": '{"command": "rm -rf build"}', "call_id": "call-1"}
        first = SimpleNamespace(id="resp-1", output=[call], output_text="", usage=None)
        second = SimpleNamespace(id="resp-2", output=[], output_text="Done", usage=None)
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(side_effect=[first, second])))
        with (
            patch("builtins.input", side_effect=["remove", ""]),
            patch("sys.stdin", new_callable=io.StringIO) as stdin,
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
            patch.object(main, "OpenAI", return_value=client),
            patch.object(main, "bash") as bash,
            patch.object(main, "render_pinned_status", return_value=True),
            patch.object(main, "restore_terminal"),
        ):
            stdin.isatty = Mock(return_value=False)
            stdout.isatty = Mock(return_value=False)
            main.main()
        bash.assert_not_called()

    def test_main_activity_callback_repaints_status(self):
        def agent(*args, **kwargs):
            kwargs["on_activity"]("thinking")
            kwargs["on_activity"]("bash")
            return "ok", 1, 1, []

        with (
            patch("builtins.input", side_effect=["/model provider/model-x", "hello", ""]),
            patch.object(main, "OpenAI"),
            patch.object(main, "begin_undo_checkpoint", return_value=None),
            patch.object(main, "finish_undo_checkpoint", return_value=None),
            patch.object(main, "run_agent", side_effect=agent),
            patch.object(main, "render_pinned_status", return_value=True) as status,
            patch.object(main, "restore_terminal"),
            patch.object(main, "render"),
        ):
            main.main()
        calls = [call.args[0] for call in status.call_args_list]
        self.assertTrue(any("model: provider/model-x" in call and "thinking" in call for call in calls))
        self.assertTrue(any("model: provider/model-x" in call and "bash" in call for call in calls))

    def test_main_derives_title_from_first_normal_prompt_only(self):
        prompt = "one   two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen"
        expected = " ".join(prompt.split())[:72]
        with tempfile.TemporaryDirectory() as directory, patch.object(main.Path, "home", return_value=Path(directory)):
            with (
                patch("builtins.input", side_effect=["/model provider/model-x", prompt, ""]),
                patch.object(main, "OpenAI"),
                patch.object(main, "new_session_id", return_value="title-id"),
                patch.object(main, "begin_undo_checkpoint", return_value=None),
                patch.object(main, "finish_undo_checkpoint", return_value=None),
                patch.object(main, "run_agent", return_value=("ok", 1, 1, [])),
                patch.object(main, "render_pinned_status", return_value=True),
                patch.object(main, "restore_terminal"),
                patch.object(main, "render"),
            ):
                main.main()
            self.assertEqual(main.load_session("title-id")["title"], expected)

    def test_save_session_serializes_response_objects(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main.Path, "home", return_value=Path(directory)):
            response = SimpleNamespace(to_dict=lambda: {"type": "message", "content": []})
            main.save_session({"id": "abc123", "history": [response], "transcript": [], "skills": []})
            self.assertEqual(
                main.load_session("abc123")["history"],
                [{"type": "message", "content": []}],
            )

    def test_session_round_trip_uses_home_k_sessions(self):
        with tempfile.TemporaryDirectory() as d, patch.object(main.Path, "home", return_value=Path(d)):
            session = {"id": "abc123", "history": [{"role": "user", "content": "hi"}], "transcript": []}
            main.save_session(session)
            self.assertTrue((Path(d) / ".k" / "sessions" / "abc123.json").exists())
            self.assertEqual(main.load_session("abc123"), session)

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

    def test_run_agent_rejects_response_without_output_list(self):
        response = SimpleNamespace(output=None, output_text="", usage=None)
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response)))
        with self.assertRaisesRegex(RuntimeError, "provider response is malformed"):
            main.run_agent("hello", client)

    def test_run_agent_rejects_malformed_tool_call(self):
        call = SimpleNamespace(type="function_call", name="bash")
        response = SimpleNamespace(output=[call], output_text="", usage=None)
        client = SimpleNamespace(responses=SimpleNamespace(create=Mock(return_value=response)))
        with self.assertRaisesRegex(RuntimeError, "provider response is malformed"):
            main.run_agent("hello", client)

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
            path.parent.mkdir(parents=True, exist_ok=True)
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


class CodeReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.root)

    def git(self, *args: str) -> None:
        main.subprocess.run(["git", *args], cwd=self.root, check=True, stdout=main.subprocess.DEVNULL)

    def init_repo(self, files: dict[str, bytes]) -> None:
        self.git("init", "-q")
        self.git("config", "user.email", "k@example.test")
        self.git("config", "user.name", "k")
        self.git("config", "core.autocrlf", "false")
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.git("add", ".")
        self.git("commit", "-qm", "init")

    def test_undo_from_subdirectory_covers_whole_repo(self):
        self.init_repo({"new.txt": b"keep\n", "other.txt": b"before\n", "sub/tracked.txt": b"x\n"})
        os.chdir(self.root / "sub")
        checkpoint = main.begin_undo_checkpoint()
        (self.root / "sub/new.txt").write_text("created\n", encoding="utf-8")
        (self.root / "top.txt").write_text("created\n", encoding="utf-8")
        (self.root / "other.txt").write_text("after\n", encoding="utf-8")
        record = main.finish_undo_checkpoint(checkpoint)
        self.assertEqual(main.apply_undo(record), "Undo complete.")
        self.assertEqual((self.root / "new.txt").read_bytes(), b"keep\n")
        self.assertEqual((self.root / "other.txt").read_bytes(), b"before\n")
        self.assertFalse((self.root / "sub/new.txt").exists())
        self.assertFalse((self.root / "top.txt").exists())

    def test_undo_handles_crlf_and_non_utf8_through_session_json(self):
        self.init_repo({"crlf.txt": b"a\r\nb\r\n", "latin.txt": b"caf\xe9\n"})
        checkpoint = main.begin_undo_checkpoint()
        (self.root / "crlf.txt").write_bytes(b"a\r\nc\r\n")
        (self.root / "latin.txt").write_bytes(b"caf\xe9 au lait\n")
        record = json.loads(json.dumps(main.finish_undo_checkpoint(checkpoint)))
        self.assertEqual(main.apply_undo(record), "Undo complete.")
        self.assertEqual((self.root / "crlf.txt").read_bytes(), b"a\r\nb\r\n")
        self.assertEqual((self.root / "latin.txt").read_bytes(), b"caf\xe9\n")

    def test_edit_preserves_other_bytes(self):
        Path("latin.txt").write_bytes(b"caf\xe9\nold\n")
        diff = main.edit("latin.txt", "old", "new")
        self.assertEqual(Path("latin.txt").read_bytes(), b"caf\xe9\nnew\n")
        diff.encode("utf-8")  # The diff must be sendable to the provider.
        Path("crlf.txt").write_bytes(b"a\r\nold\r\nz\r\n")
        main.edit("crlf.txt", "a\nold", "a\nnew\nmore")
        self.assertEqual(Path("crlf.txt").read_bytes(), b"a\r\nnew\r\nmore\r\nz\r\n")

    def test_grep_skips_symlink_escape_and_large_files(self):
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret"
            secret.write_text("TOKEN=abc\n", encoding="utf-8")
            Path("link").symlink_to(secret)
            Path("big.txt").write_text("TOKEN\n" * (main.MAX_TEXT_FILE_BYTES // 5), encoding="utf-8")
            Path("ok.txt").write_text("TOKEN=ok\n", encoding="utf-8")
            self.assertEqual(main.grep("TOKEN"), "ok.txt:1: TOKEN=ok")

    def test_trim_history_drops_leading_call_with_its_output(self):
        call = {"type": "function_call", "call_id": "c", "name": "read", "arguments": "{}"}
        output = {"type": "function_call_output", "call_id": "c", "output": "x"}
        user = {"role": "user", "content": "hi"}
        self.assertEqual(main._trim_history([call, output, *[user] * (main.MAX_HISTORY_ITEMS - 2)]), [user] * (main.MAX_HISTORY_ITEMS - 2))
        self.assertEqual(main._trim_history([output, user]), [user])

    def test_stream_chat_keeps_arguments_and_ignores_empty_chunks(self):
        def tool_chunk(arguments, call_id="c1", name="read"):
            call = SimpleNamespace(index=0, id=call_id, function=SimpleNamespace(name=name, arguments=arguments))
            return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[call]))])

        chunks = [SimpleNamespace(choices=[]), tool_chunk('{"pa'), tool_chunk('th":"x"}')]
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(return_value=chunks))))
        _, calls = main._stream_chat_turn(client, "m", "i", [])
        self.assertEqual(calls, [{"id": "c1", "name": "read", "arguments": '{"path":"x"}'}])

    def test_stream_chat_empty_call_id_matches_tool_result(self):
        call = SimpleNamespace(index=0, id="", function=SimpleNamespace(name="grep", arguments='{"pattern":"zzz"}'))
        first = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[call]))])]
        second = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="done", tool_calls=None))])]
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(side_effect=[first, second]))))
        _, _, _, history = main.run_agent("x", client, api_mode="chat-completions", stream=True)
        call_id = history[1]["tool_calls"][0]["id"]
        self.assertTrue(call_id)
        self.assertEqual(history[2]["tool_call_id"], call_id)

    def test_bash_timeout_stops_children_and_keeps_partial_output(self):
        started = time.monotonic()
        result = main.bash("echo partial; sleep 20; echo done", timeout=1)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn("timed out", result)
        self.assertIn("partial", result)

    def test_tool_schemas_follow_strict_rules(self):
        for tool in main.TOOLS:
            parameters = tool["parameters"]
            self.assertEqual(sorted(parameters["required"]), sorted(parameters["properties"]), tool["name"])
            for schema in parameters["properties"].values():
                self.assertNotIn("default", schema)

    def test_dispatch_treats_null_arguments_as_defaults(self):
        Path("a.txt").write_text("one\n", encoding="utf-8")
        result = main.dispatch_tool("read", '{"path":"a.txt","offset":null,"limit":null}', "c")
        self.assertEqual(result["output"], "1: one")

    def test_read_accepts_jpeg(self):
        Path("a.jpeg").write_bytes(b"x")
        self.assertEqual(main.read("a.jpeg")[0]["type"], "input_image")

    def test_chat_images_follow_all_tool_messages(self):
        history = []
        main._append_chat_results(
            history,
            [
                {"call_id": "a", "output": [{"type": "input_image", "image_url": "data:img"}]},
                {"call_id": "b", "output": "text"},
            ],
        )
        self.assertEqual([item["role"] for item in history], ["tool", "tool", "user"])
        self.assertEqual(history[0]["content"], "Image attached in the next message.")
        self.assertEqual(history[2]["content"], [{"type": "image_url", "image_url": {"url": "data:img"}}])

    def test_compact_chat_sends_no_tools_field(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="summary"))])
        create = Mock(return_value=response)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        main.compact_session({"history": [], "api_mode": "chat-completions", "model": "m"}, client)
        self.assertNotIn("tools", create.call_args.kwargs)

    def test_system_prompt_lists_grep(self):
        self.assertIn("- grep:", main.SYSTEM_PROMPT)

    def run_main(self, argv, inputs, *, config=None, saved=None, env=None, agent_result=("ok", 1, 1, [])):
        with (
            patch("sys.argv", ["k", *argv]),
            patch("builtins.input", side_effect=inputs),
            patch.dict(os.environ, env or {}),
            patch.object(main.Path, "home", return_value=self.root),
            patch.object(main, "load_config", return_value=config or {}),
            patch.object(main, "load_session", side_effect=lambda session_id: saved[session_id]),
            patch.object(main, "OpenAI") as client,
            patch.object(main, "run_agent", return_value=agent_result) as agent,
            patch.object(main, "begin_undo_checkpoint", return_value=None),
            patch.object(main, "render_pinned_status", return_value=True),
            patch.object(main, "restore_terminal"),
            patch.object(main, "save_session"),
            patch.object(main, "render") as render,
            patch("builtins.print"),
        ):
            main.main()
        return SimpleNamespace(client=client, agent=agent, messages=[call.args[1] for call in render.call_args_list])

    def session(self, **fields):
        return main.ensure_session_defaults({"id": "saved", "history": [], "transcript": [], **fields})

    def test_print_mode_needs_explicit_yolo_flag(self):
        config = {"defaults": {"permission_mode": "yolo"}}
        self.assertIsNone(self.run_main(["-p", "task"], [], config=config).agent.call_args.kwargs["confirm_bash"])
        self.assertIsNotNone(self.run_main(["-p", "task", "--mode", "yolo"], []).agent.call_args.kwargs["confirm_bash"])

    def test_interactive_config_yolo_without_tty_rejects_bash(self):
        result = self.run_main([], ["hello", ""], config={"defaults": {"permission_mode": "yolo"}})
        with patch("sys.stdin", new_callable=io.StringIO):
            self.assertFalse(result.agent.call_args.kwargs["confirm_bash"]("rm -rf build"))

    def test_resume_command_keeps_current_permission_mode(self):
        saved = {"saved": self.session(permission_mode="yolo", api_mode="responses")}
        with patch.dict(os.environ, {"K_API_MODE": "responses"}):
            self.run_main([], ["/resume saved", ""], saved=saved)
        self.assertEqual(saved["saved"]["permission_mode"], "review")

    def test_resume_keeps_saved_model(self):
        saved = {"saved": self.session(model="saved-model", api_mode="responses")}
        result = self.run_main(["--resume", "saved", "-p", "task"], [], saved=saved, env={"OPENAI_MODEL_NAME": "env-model", "K_API_MODE": "responses"})
        self.assertEqual(result.agent.call_args.kwargs["model"], "saved-model")

    def test_resume_uses_api_mode_from_config(self):
        saved = {"saved": self.session(api_mode="chat-completions")}
        env = {key: value for key, value in os.environ.items() if key != "K_API_MODE"}
        with patch.dict(os.environ, env, clear=True):
            result = self.run_main(["--resume", "saved", "-p", "task"], [], saved=saved, config={"defaults": {"api_mode": "chat-completions"}})
        self.assertEqual(result.agent.call_args.kwargs["api_mode"], "chat-completions")
        with self.assertRaisesRegex(ValueError, "cannot resume"), patch.object(main, "load_session", return_value=saved["saved"]):
            main.load_resume_session("saved", "responses")

    def test_preset_refuses_api_mode_change_with_history(self):
        saved = {"saved": self.session(api_mode="responses", history=[{"role": "user", "content": "hi"}], model="old")}
        config = {"presets": {"fast": {"model": "new", "api_mode": "chat-completions"}}}
        result = self.run_main(["--resume", "saved"], ["/preset fast", ""], saved=saved, config=config, env={"K_API_MODE": "responses"})
        self.assertEqual(saved["saved"]["api_mode"], "responses")
        self.assertEqual(saved["saved"]["model"], "old")
        self.assertTrue(any("different API mode" in message for message in result.messages))

    def test_environment_base_url_overrides_config(self):
        config = {"defaults": {"base_url": "https://config.example/v1"}}
        result = self.run_main([], [""], config=config, env={"OPENAI_BASE_URL": "https://env.example/v1"})
        self.assertEqual(result.client.call_args.kwargs["base_url"], "https://env.example/v1")
