import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from settings import child_environment, load_settings, read_env, redact


class SettingsTests(unittest.TestCase):
    def test_literal_loading_precedence_and_preservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            source = '# comment\nJIRA_JSESSIONID="test-session"\nLITERAL=$(ignored) %PATH%\n'
            path.write_text(source, encoding="utf-8")
            loaded = load_settings(path, {"JIRA_JSESSIONID": "process-session"})
            self.assertEqual(loaded["JIRA_JSESSIONID"], "process-session")
            self.assertEqual(loaded["LITERAL"], "$(ignored) %PATH%")
            self.assertEqual(path.read_text(), source)
            self.assertEqual(load_settings(path, {})["JIRA_JSESSIONID"], "test-session")
            path.write_text("JIRA_JSESSIONID=renewed-session\n", encoding="utf-8")
            self.assertEqual(load_settings(path, {})["JIRA_JSESSIONID"], "renewed-session")

    def test_invalid_line_never_echoes_value(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text("not-an-assignment-with-private-data", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 1") as raised:
                read_env(path)
            self.assertNotIn("private-data", str(raised.exception))

    def test_child_environment_removes_credentials_case_insensitively(self):
        with mock.patch.dict(
            os.environ,
            {"JIRA_JSESSIONID": "test-session", "jsessionid": "another-test", "KEEP": "ok"},
        ):
            child = child_environment()
        self.assertNotIn("JIRA_JSESSIONID", child)
        self.assertNotIn("jsessionid", child)
        self.assertEqual(child["KEEP"], "ok")

    def test_redaction(self):
        with mock.patch.dict(os.environ, {"JIRA_JSESSIONID": "test-session-value"}):
            cleaned = redact(
                "error test-session-value\nCookie: unrelated-cookie\nAuthorization: bearer example"
            )
        self.assertNotIn("test-session-value", cleaned)
        self.assertNotIn("unrelated-cookie", cleaned)
        self.assertNotIn("bearer example", cleaned)
