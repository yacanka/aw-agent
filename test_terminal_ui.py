import io
import unittest
from unittest import mock

from terminal_ui import DEBUG_BLOCK_LIMIT, TerminalUI


class TerminalDebugTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.ui = TerminalUI(self.output)

    def test_command_description_does_not_hide_command_or_parameters(self):
        arguments = {
            "command": "python test.py --verbose",
            "description": "Testleri çalıştır",
            "working_directory": "src",
            "stdin": "input\n",
            "timeout_seconds": 5,
        }
        self.ui.tool_started(1, 2, "run_command", arguments, "call_example")
        self.ui.tool_finished("run_command", {
            "success": False, "exit_code": 2, "stdout": "first\nsecond\n",
            "stderr": "ValueError: invalid input\n", "timed_out": False,
        }, 0.25, "call_example")

        rendered = self.output.getvalue()
        for expected in (
            "TOOL CALL run_command call_example", "python test.py --verbose",
            '"working_directory": "src"', '"timeout_seconds": 5',
            "TOOL RESULT HATA run_command call_example", '"exit_code": 2',
            "STDOUT run_command call_example", "| first\n        | second",
            "STDERR run_command call_example", "ValueError: invalid input",
        ):
            self.assertIn(expected, rendered)

    def test_redaction_applies_before_truncation_and_to_nested_fields(self):
        with mock.patch.dict("os.environ", {"JIRA_JSESSIONID": "synthetic-session"}):
            self.ui.debug("PAYLOAD", {
                "headers": {"Authorization": "synthetic-bearer", "Cookie": "synthetic-cookie"},
                "access_token": "synthetic-token", "max_tokens": 2048,
                "command": "app --password synthetic-password",
                "stdout": "password=synthetic-password\nsynthetic-session",
            })
            self.ui.debug("LONG OUTPUT", "x" * 9996 + "synthetic-session" + "y" * 20000)

        rendered = self.output.getvalue()
        for value in ("synthetic-session", "synthetic-bearer", "synthetic-cookie",
                      "synthetic-token", "synthetic-password"):
            self.assertNotIn(value, rendered)
        self.assertIn("[REDACTED]", rendered)
        self.assertIn('"max_tokens": 2048', rendered)
        self.assertIn("LOG TRUNCATED", rendered)

    def test_large_output_keeps_error_tail_without_mutating_result(self):
        result = {"stdout": "a" * (DEBUG_BLOCK_LIMIT + 100) + "error-at-end", "exit_code": 1}
        original = dict(result)
        self.ui.tool_finished("run_python", result, 1)
        self.assertEqual(result, original)
        self.assertIn("error-at-end", self.output.getvalue())
        self.assertIn("LOG TRUNCATED", self.output.getvalue())
        self.assertIn("TOOL RESULT HATA", self.output.getvalue())

    def test_stderr_warning_alone_is_not_failure(self):
        self.ui.tool_finished("run_python", {
            "success": True, "exit_code": 0, "stderr": "warning: deprecated",
        }, 0.1)
        self.assertIn("TOOL RESULT OK", self.output.getvalue())
        self.assertIn("warning: deprecated", self.output.getvalue())

    def test_control_sequences_cannot_overwrite_terminal(self):
        self.ui.debug("STDOUT", "first\rreplaced\x1b[2J\x08")
        rendered = self.output.getvalue()
        for control in ("\r", "\x1b", "\x08"):
            self.assertNotIn(control, rendered)
        self.assertIn(r"\x1b[2J", rendered)

    def test_operation_logs_exception_location_and_reraises(self):
        with self.assertRaisesRegex(OSError, "test failure"):
            with self.ui.operation("tool.execute run_python"):
                raise OSError("test failure")
        rendered = self.output.getvalue()
        self.assertIn("ERROR tool.execute run_python", rendered)
        self.assertIn('"exception_type": "OSError"', rendered)
        self.assertIn("test_terminal_ui.py:", rendered)
        self.assertNotIn("DONE tool.execute", rendered)
