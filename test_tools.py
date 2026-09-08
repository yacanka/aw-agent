import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_tools import (
    _safe_workspace_path,
    _validate_cmd_command,
    list_files,
    read_file,
    run_command,
    run_python,
    write_file,
)
from gemma_parser import (
    extract_gemma_thoughts,
    parse_gemma_arguments,
    parse_gemma_tool_calls,
)


class GemmaParserTests(unittest.TestCase):
    def test_gemma_string_arguments(self) -> None:
        parsed = parse_gemma_arguments('{path:<|"|>hello.py<|"|>,content:<|"|>abc<|"|>}')
        self.assertEqual(
            parsed,
            {"path": "hello.py", "content": "abc"},
        )

    def test_double_brace_json_arguments(self) -> None:
        parsed = parse_gemma_arguments('{{"command":"python hello.py","description":"test"}}')
        self.assertEqual(parsed["command"], "python hello.py")

    def test_native_tool_call(self) -> None:
        content = (
            "<|tool_call>call:run_python"
            '{script:<|"|>hello.py<|"|>,stdin:<|"|>Yaşar\\n<|"|>}'
            "<tool_call|>"
        )
        calls = parse_gemma_tool_calls(content)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "run_python")

    def test_thought_extraction(self) -> None:
        content = "<|channel>thought\nKontrol et.<channel|>"
        self.assertEqual(extract_gemma_thoughts(content), ["Kontrol et."])


class ToolTests(unittest.TestCase):
    def test_workspace_file_round_trip_and_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = 'name = input("Adınız nedir? ")\nprint(f"Merhaba {name}")\n'

            written = write_file(workspace, "hello.py", source)
            self.assertTrue(written["success"])
            self.assertEqual(read_file(workspace, "hello.py")["content"], source)
            self.assertEqual(list_files(workspace)["files"][0]["name"], "hello.py")

            result = run_python(
                workspace,
                script="hello.py",
                stdin="Yaşar\n",
                timeout_seconds=5,
            )
            self.assertTrue(result["success"], result)
            self.assertIn("Merhaba Yaşar", result["stdout"])

    def test_workspace_escape_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(PermissionError):
                _safe_workspace_path(Path(temporary), "../outside.txt")

    def test_cmd_operator_is_blocked(self) -> None:
        with self.assertRaises(PermissionError):
            _validate_cmd_command("python hello.py && del hello.py")

    def test_cmd_absolute_path_is_blocked(self) -> None:
        with self.assertRaises(PermissionError):
            _validate_cmd_command(r"python C:\temp\hello.py")

    def test_run_command_uses_cmd_exe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch("agent_tools.IS_WINDOWS", True):
                with mock.patch.dict(
                    os.environ,
                    {"COMSPEC": r"C:\Windows\System32\cmd.exe"},
                ):
                    with (
                        mock.patch(
                            "agent_tools.system_cmd", return_value=r"C:\Windows\System32\cmd.exe"
                        ),
                        mock.patch("agent_tools._resolved_cmd", return_value="python hello.py"),
                        mock.patch("agent_tools._run_process") as runner,
                    ):
                        runner.return_value = {"success": True, "exit_code": 0}
                        result = run_command(
                            Path(temporary),
                            command="python hello.py",
                        )

        argv = runner.call_args.args[0]
        self.assertEqual(
            argv,
            [
                r"C:\Windows\System32\cmd.exe",
                "/d",
                "/s",
                "/c",
                "python hello.py",
            ],
        )
        self.assertEqual(result["shell"], "cmd.exe")


if __name__ == "__main__":
    unittest.main()
