import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_tools import _resolved_cmd, _validate_cmd_command, run_command, run_python
from process_runner import run_process


class ProcessTests(unittest.TestCase):
    def run_script(self, source, **kwargs):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "script.py").write_text(source, encoding="utf-8")
            return run_python(workspace, "script.py", **kwargs)

    def test_output_is_bounded_and_stderr_is_not_failure(self):
        result = self.run_script(
            'import sys\nprint("a" * 2000000)\nprint("warning", file=sys.stderr)\n'
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["stdout_truncated"])
        self.assertLess(len(result["stdout"]), 20100)
        self.assertEqual(result["stderr"].strip(), "warning")

    def test_nonzero_exit(self):
        result = self.run_script("raise SystemExit(7)\n")
        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], 7)

    def test_python_utf8_overrides_inherited_stdio_encoding(self):
        with mock.patch.dict(os.environ, {"PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"}):
            result = self.run_script("print(input())\n", stdin="Yaşar\n")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["stdout"].strip(), "Yaşar")

    def test_target_startup_error_preserves_codes_without_raw_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = run_process([str(Path(temporary) / "missing.exe")], Path(temporary), None, 1)
        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], 126)
        self.assertIn("process.target.spawn", result["stderr"])
        self.assertIn("FileNotFoundError", result["stderr"])
        self.assertIn("errno=2", result["stderr"])
        self.assertNotIn(temporary, result["stderr"])

    def test_shell_names_in_python_data_are_accepted(self):
        for command in ('python -c "print(\'cmd\')"', "python cmd.py", "python -c powershell"):
            with self.subTest(command=command):
                self.assertEqual(_validate_cmd_command(command), command)

    def test_stdin_unicode_and_secret_environment(self):
        with mock.patch.dict(os.environ, {"JIRA_JSESSIONID": "test-only-session"}):
            result = self.run_script(
                'import os\nprint(input())\nprint("JIRA_JSESSIONID" in os.environ)\n',
                stdin="Yaşar\n",
            )
        self.assertTrue(result["success"], result)
        self.assertIn("Yaşar", result["stdout"])
        self.assertIn("False", result["stdout"])

    def test_timeout_cleans_tree_and_inherited_pipes(self):
        result = self.run_script(
            "import subprocess, sys, time\n"
            'subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
            'print("started", flush=True)\ntime.sleep(60)\n',
            timeout_seconds=1,
        )
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["success"])
        self.assertIsNone(result["exit_code"])
        self.assertIn("started", result["stdout"])
        self.assertEqual(result["error_stage"], "process.wait")

    def test_job_creation_failure_does_not_spawn(self):
        with (
            mock.patch("process_runner.IS_WINDOWS", True),
            mock.patch("process_runner.WindowsJob", side_effect=OSError),
            mock.patch("process_runner.subprocess.Popen") as spawn,
        ):
            result = run_process(["python", "script.py"], Path("."), None, 1)
        spawn.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["error_stage"], "process.job.create")
        self.assertEqual(result["exception_type"], "OSError")

    def test_assignment_failure_never_releases_target(self):
        job = mock.Mock()
        job.assign.side_effect = OSError
        process = mock.Mock()
        with (
            mock.patch("process_runner.IS_WINDOWS", True),
            mock.patch("process_runner.WindowsJob", return_value=job),
            mock.patch("process_runner.subprocess.Popen", return_value=process),
            mock.patch("process_runner._feed") as feed,
        ):
            result = run_process(["python", "script.py"], Path("."), None, 1)
        feed.assert_not_called()
        process.stdin.write.assert_not_called()
        job.close.assert_called_once()
        self.assertFalse(result["success"])
        self.assertEqual(result["error_stage"], "process.job.assign")

    def test_spawn_error_preserves_os_diagnostics_and_redacts_session(self):
        with (
            mock.patch("process_runner.IS_WINDOWS", False),
            mock.patch.dict(os.environ, {"JIRA_JSESSIONID": "synthetic-session"}),
            mock.patch(
                "process_runner.subprocess.Popen",
                side_effect=PermissionError(13, "denied synthetic-session"),
            ),
        ):
            result = run_process(["python", "script.py"], Path("."), None, 1)
        self.assertEqual(result["error_stage"], "process.spawn")
        self.assertEqual(result["exception_type"], "PermissionError")
        self.assertEqual(result["errno"], 13)
        self.assertNotIn("synthetic-session", str(result))

    def test_workspace_program_cannot_shadow_allowlisted_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "git.exe").write_bytes(b"not an executable")
            with mock.patch.dict(os.environ, {"PATH": str(workspace)}):
                with self.assertRaisesRegex(ValueError, "outside workspace"):
                    _resolved_cmd("git status", workspace)

    def test_cancellation_closes_job(self):
        job = mock.Mock()
        process = mock.Mock()
        process.wait.side_effect = [KeyboardInterrupt, 0]
        with (
            mock.patch("process_runner.IS_WINDOWS", True),
            mock.patch("process_runner.WindowsJob", return_value=job),
            mock.patch("process_runner.subprocess.Popen", return_value=process),
            mock.patch("process_runner.threading.Thread"),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_process(["python", "script.py"], Path("."), None, 1)
        job.close.assert_called_once()

    def test_shells_paths_and_scripts_are_rejected(self):
        for command in (
            "powershell Get-Item .",
            "pwsh -c test",
            "cmd /c dir",
            '"cmd.exe" /c dir',
            "git status | more",
            "python ../outside.py",
            r"python ..\outside.py",
            r"git -C C:\temp status",
            r"python \outside.py",
            "python /tmp/outside.py",
            "python script.py\nwhoami",
            "python script.py > result.txt",
            "python run.cmd",
            'python "unterminated',
            "python %PATH%",
        ):
            with self.subTest(command=command):
                with self.assertRaises(PermissionError):
                    _validate_cmd_command(command)

    def test_shell_executable_is_blocked_even_if_allowlisted(self):
        with mock.patch("agent_tools.COMMAND_ALLOWED_PROGRAMS", ("cmd.exe",)):
            with self.assertRaisesRegex(PermissionError, "shell"):
                _validate_cmd_command("cmd.exe /c dir")

    def test_py_resolution_preserves_version_selector(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            launcher = root / "py.exe"
            launcher.write_bytes(b"not executed")
            with mock.patch.dict(os.environ, {"PATH": str(root)}):
                command = _resolved_cmd('py -3 "hello world.py"', workspace)
        self.assertEqual(command, f'""{launcher.resolve()}" -3 "hello world.py""')

    def test_missing_launcher_reports_resolution_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            with (
                mock.patch("agent_tools.IS_WINDOWS", True),
                mock.patch("agent_tools.system_cmd", return_value=r"C:\Windows\System32\cmd.exe"),
                mock.patch.dict(os.environ, {"PATH": ""}),
            ):
                result = run_command(Path(temporary), "py -3 script.py")
        self.assertEqual(result["error_stage"], "process.resolve")
        self.assertIn("py.exe", result["exception_message"])
        self.assertIn("PATH", result["exception_message"])

    @unittest.skipUnless(os.name == "nt", "Requires real Windows CMD")
    def test_py_command_uses_utf8_for_stdin_and_stdout(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "script.py").write_text("print(input())\n", encoding="utf-8")
            # Substitute only executable discovery so this exercises real CMD
            # pipes and encoding without requiring an installed py launcher.
            with (
                mock.patch(
                    "agent_tools._resolved_cmd",
                    return_value=f'""{sys.executable}" script.py"',
                ),
                mock.patch.dict(os.environ, {"PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"}),
            ):
                result = run_command(workspace, "py script.py", stdin="Yaşar\n")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["stdout"].strip(), "Yaşar")

    @unittest.skipUnless(os.name == "nt", "Requires real Windows CMD and Job Objects")
    def test_real_cmd_ignores_comspec(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.dict(os.environ, {"COMSPEC": "not-a-shell.exe"}),
        ):
            result = run_command(Path(temporary), "dir")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["shell"], "cmd.exe")

    @unittest.skipUnless(os.name == "nt", "Requires real Windows CMD quoting")
    def test_real_cmd_quoted_python_filename(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "hello world.py").write_text("print(input())\n", encoding="utf-8")
            result = run_command(workspace, 'python "hello world.py"', stdin="Yaşar\n")
        self.assertTrue(result["success"], result)
        self.assertIn("Yaşar", result["stdout"])

    @unittest.skipUnless(os.name == "nt", "Requires Windows process queries")
    def test_windows_descendant_is_terminated(self):
        # A delayed marker detects an escaped child without invoking another shell.
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            marker = workspace / "escaped.txt"
            child = "import time,pathlib; time.sleep(2); pathlib.Path('escaped.txt').touch()"
            source = f"import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',{child!r}])\ntime.sleep(60)\n"
            (workspace / "script.py").write_text(source, encoding="utf-8")
            result = run_python(workspace, "script.py", timeout_seconds=1)
            self.assertTrue(result["timed_out"])
            import time

            time.sleep(2)
            self.assertFalse(marker.exists())
