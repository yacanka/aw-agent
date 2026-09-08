from __future__ import annotations

import locale
import ntpath
import os
import re
import sys
from pathlib import Path
from typing import Any

from config import (
    COMMAND_ALLOWED_PROGRAMS,
    COMMAND_MAX_LENGTH,
    COMMAND_TIMEOUT_SECONDS,
    FILE_MAX_BYTES,
)
from jira_client import JIRA_TOOL_DEFINITIONS, execute_jira
from process_runner import run_process as _run_process
from settings import redact
from windows_job import system_cmd

IS_WINDOWS = os.name == "nt"


def _safe_workspace_path(
    workspace: Path,
    relative_path: str,
) -> Path:
    """Resolve a relative path while keeping it inside the workspace."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("Path must be a non-empty string")

    if ntpath.isabs(relative_path) or ntpath.splitdrive(relative_path)[0]:
        raise PermissionError("Path must be workspace-relative")
    if any(part.lower().startswith(".env") for part in Path(relative_path).parts):
        raise PermissionError("Environment files are not accessible to file tools")
    workspace = workspace.resolve()
    candidate = (workspace / relative_path).resolve()

    if candidate != workspace and workspace not in candidate.parents:
        raise PermissionError(f"Workspace dışına erişim yasak: {relative_path}")

    return candidate


def list_files(
    workspace: Path,
    directory: str = ".",
) -> dict[str, Any]:
    path = _safe_workspace_path(workspace, directory)

    if not path.exists():
        return {"error": f"Directory does not exist: {directory}"}

    if not path.is_dir():
        return {"error": f"Not a directory: {directory}"}

    entries = []

    for item in sorted(
        path.iterdir(),
        key=lambda p: (not p.is_dir(), p.name.lower()),
    ):
        entries.append(
            {
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
            }
        )

    return {
        "directory": directory,
        "files": entries,
    }


def read_file(
    workspace: Path,
    path: str,
) -> dict[str, Any]:
    file_path = _safe_workspace_path(workspace, path)

    if not file_path.exists():
        return {"error": f"File not found: {path}"}

    if not file_path.is_file():
        return {"error": f"Not a file: {path}"}

    try:
        with file_path.open("rb") as stream:
            data = stream.read(FILE_MAX_BYTES + 1)
        if len(data) > FILE_MAX_BYTES:
            return {"error": "File exceeds configured read limit"}
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "error": (
                f"File is not valid UTF-8 text: {path}. This agent only reads UTF-8 text files."
            )
        }
    except OSError as exc:
        return {"error": str(exc)}

    return {
        "path": path,
        "content": content,
    }


def write_file(
    workspace: Path,
    path: str,
    content: str,
) -> dict[str, Any]:
    file_path = _safe_workspace_path(workspace, path)

    if not isinstance(content, str):
        return {"error": "content must be a string"}

    if len(content.encode("utf-8")) > FILE_MAX_BYTES:
        return {"error": "Content exceeds configured file limit"}

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"error": str(exc)}

    return {
        "success": True,
        "path": path,
        "bytes_written": len(content.encode("utf-8")),
    }


def _clamp_timeout(timeout_seconds: Any) -> int:
    if timeout_seconds is None:
        return COMMAND_TIMEOUT_SECONDS

    if isinstance(timeout_seconds, bool) or isinstance(timeout_seconds, float):
        raise ValueError("timeout_seconds must be an integer")

    try:
        requested = int(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be an integer") from exc

    if requested < 1:
        raise ValueError("timeout_seconds must be at least 1")

    return min(requested, COMMAND_TIMEOUT_SECONDS)


def _first_command_token(command: str) -> str:
    stripped = command.lstrip()

    if not stripped:
        raise ValueError("command must not be empty")

    if stripped.startswith('"'):
        closing_quote = stripped.find('"', 1)
        if closing_quote == -1:
            raise ValueError("Unterminated quote in command executable")
        return stripped[1:closing_quote]

    return stripped.split(maxsplit=1)[0]


def _validate_cmd_command(command: str) -> str:
    if not isinstance(command, str):
        raise ValueError("command must be a string")

    command = command.strip()

    if not command:
        raise ValueError("command must not be empty")

    if len(command) > COMMAND_MAX_LENGTH:
        raise ValueError(f"command exceeds the {COMMAND_MAX_LENGTH}-character limit")

    forbidden = {
        "&": "command chaining",
        "|": "pipes",
        "<": "input redirection",
        ">": "output redirection",
        "^": "cmd escaping",
        "%": "environment expansion",
        "!": "delayed environment expansion",
        "\r": "multiple command lines",
        "\n": "multiple command lines",
    }

    for character, feature in forbidden.items():
        if character in command:
            raise PermissionError(
                f"Blocked cmd.exe feature ({feature}): {character!r}. "
                "Use one simple command per tool call."
            )

    # Keep obvious absolute/parent paths out of generic commands. This is not
    # an OS sandbox; README.txt documents that limitation.
    if re.search(r"(?i)(?:^|\s)[\"']?(?:[a-z]:[\\/]|\\\\)", command):
        raise PermissionError("Absolute Windows and UNC paths are blocked")

    if re.search(r"(?:^|[\"'\\/])\.\.(?:[\\/]|$)", command):
        raise PermissionError("Parent-directory paths are blocked")

    if "\x00" in command or command.count('"') % 2:
        raise PermissionError("NUL and unbalanced quotes are blocked")
    if re.search(r"(?i)(?:^|[^a-z0-9_])(?:powershell|pwsh|cmd)(?:\.exe)?(?:$|[^a-z0-9_])", command):
        raise PermissionError("Nested shells and PowerShell are blocked")
    if re.search(r"(?i)\.(?:bat|cmd)(?:$|[\s\"'])", command):
        raise PermissionError("Batch scripts are blocked")
    if re.search(r"(?:^|[\s=\"'\\/])\.\.(?:[\\/\s\"']|$)", command):
        raise PermissionError("Parent-directory paths are blocked")
    if re.search(r"(?i)(?:[a-z]:|\\\\)", command):
        raise PermissionError("Drive and UNC paths are blocked")
    if re.search(r"""(?:^|[\s=])["']?\\""", command):
        raise PermissionError("Root-relative Windows paths are blocked")
    if re.search(r"""(?:^|[\s=])["']?/[^/\s"']+/""", command):
        raise PermissionError("Absolute slash paths are blocked")

    executable = _first_command_token(command)

    if "\\" in executable or "/" in executable or ":" in executable:
        raise PermissionError(
            "The command executable must be an allowlisted program name, not a path"
        )

    executable = executable.lower()

    if executable not in COMMAND_ALLOWED_PROGRAMS:
        raise PermissionError(
            f"Program is not allowlisted: {executable}. "
            f"Allowed: {', '.join(COMMAND_ALLOWED_PROGRAMS)}"
        )

    return command


def _cmd_output_encoding() -> str:
    """Return the Windows OEM code page normally used by cmd.exe output."""
    if not IS_WINDOWS:
        return "utf-8"

    try:
        import ctypes

        code_page = ctypes.windll.kernel32.GetOEMCP()
        if code_page:
            return f"cp{code_page}"
    except (AttributeError, OSError):
        pass

    return locale.getpreferredencoding(False)


def _resolved_cmd(command: str, workspace: Path) -> str:
    """Avoid CMD's current-directory/PATHEXT executable and batch shadowing."""
    token = _first_command_token(command)
    if command.startswith('"'):
        remainder = command[len(token) + 2 :].strip()
    else:
        remainder = command[len(token) :].strip()
    name = token.lower()
    if name in {"dir", "type"}:
        return name + (" " + remainder if remainder else "")
    if name in {"python", "python.exe"}:
        executable = Path(sys.executable).resolve()
        remainder = "-X utf8" + (" " + remainder if remainder else "")
    else:
        filename = name if name.endswith(".exe") else name + ".exe"
        executable = None
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            root = Path(directory.strip('"'))
            if not directory or not root.is_absolute():
                continue
            candidate = (root / filename).resolve()
            if workspace.resolve() in candidate.parents:
                continue
            if candidate.is_file() and candidate.suffix.lower() == ".exe":
                executable = candidate
                break
        if executable is None:
            raise ValueError("Allowlisted executable not found outside workspace")
    if any(character in str(executable) for character in '&|<>^%!\r\n"'):
        raise ValueError("Executable path contains unsupported CMD characters")
    # /s strips the outer pair while preserving the executable/argument quotes.
    return '""' + str(executable) + '"' + (" " + remainder if remainder else "") + '"'


def run_command(
    workspace: Path,
    command: str,
    working_directory: str = ".",
    stdin: str | None = None,
    timeout_seconds: Any = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Run one allowlisted command through Windows cmd.exe."""
    del description  # Accepted for model compatibility; it is not executed.

    if not IS_WINDOWS:
        return {
            "error": "run_command requires Windows and cmd.exe",
            "platform": os.name,
        }

    try:
        command = _validate_cmd_command(command)
        timeout = _clamp_timeout(timeout_seconds)
        cwd = _safe_workspace_path(workspace, working_directory)
    except (ValueError, PermissionError) as exc:
        return {"error": redact(f"{type(exc).__name__}: {exc}")}

    if not cwd.exists() or not cwd.is_dir():
        return {"error": f"Working directory not found: {working_directory}"}

    try:
        comspec = system_cmd()
        resolved_command = _resolved_cmd(command, workspace)
    except (OSError, ValueError):
        return {"error": "Verified system cmd.exe or allowlisted executable is unavailable"}

    result = _run_process(
        [comspec, "/d", "/s", "/c", resolved_command],
        cwd=cwd,
        stdin_text=stdin,
        timeout_seconds=timeout,
        output_encoding=(
            "utf-8"
            if _first_command_token(command).lower() in {"python", "python.exe"}
            else _cmd_output_encoding()
        ),
    )
    result.update(
        {
            "shell": "cmd.exe",
            "command": command,
            "working_directory": working_directory,
        }
    )
    return result


def run_python(
    workspace: Path,
    script: str,
    arguments: list[Any] | None = None,
    working_directory: str = ".",
    stdin: str | None = None,
    timeout_seconds: Any = None,
) -> dict[str, Any]:
    """Run a workspace-local Python file with the current interpreter."""
    try:
        script_path = _safe_workspace_path(workspace, script)
        cwd = _safe_workspace_path(workspace, working_directory)
        timeout = _clamp_timeout(timeout_seconds)
    except (ValueError, PermissionError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    if not script_path.exists() or not script_path.is_file():
        return {"error": f"Python script not found: {script}"}

    if script_path.suffix.lower() != ".py":
        return {"error": "run_python only accepts .py files"}

    if not cwd.exists() or not cwd.is_dir():
        return {"error": f"Working directory not found: {working_directory}"}

    if arguments is None:
        arguments = []

    if not isinstance(arguments, list):
        return {"error": "arguments must be an array"}

    if len(arguments) > 100:
        return {"error": "arguments may contain at most 100 items"}

    argv = [sys.executable, "-X", "utf8", str(script_path)]
    argv.extend(str(item) for item in arguments)

    result = _run_process(
        argv,
        cwd=cwd,
        stdin_text=stdin,
        timeout_seconds=timeout,
    )
    result.update(
        {
            "executable": sys.executable,
            "script": script,
            "arguments": [str(item) for item in arguments],
            "working_directory": working_directory,
        }
    )
    return result


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Lists files and directories inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Workspace-relative directory; use '.'.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Reads a UTF-8 text file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file path.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Creates or overwrites a UTF-8 workspace file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative file path.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete text to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Runs a workspace-local .py file with the current Python "
                "interpreter. Prefer this for testing Python code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "Workspace-relative .py file path.",
                    },
                    "arguments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional command-line arguments.",
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Workspace-relative cwd; default '.'.",
                    },
                    "stdin": {
                        "type": "string",
                        "description": "Optional standard input, including newlines.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Timeout capped by config.py.",
                    },
                },
                "required": ["script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Runs one allowlisted Windows command through cmd.exe. "
                "Use Windows CMD syntax, not Bash syntax."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "One Windows cmd.exe command.",
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Workspace-relative cwd; default '.'.",
                    },
                    "stdin": {
                        "type": "string",
                        "description": "Optional standard input.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Timeout capped by config.py.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional human-readable purpose.",
                    },
                },
                "required": ["command"],
            },
        },
    },
]


TOOL_DEFINITIONS.extend(JIRA_TOOL_DEFINITIONS)

AVAILABLE_TOOL_NAMES = tuple(item["function"]["name"] for item in TOOL_DEFINITIONS)


def _missing(arguments: dict[str, Any], *names: str) -> list[str]:
    return [name for name in names if name not in arguments]


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    """Central dispatcher shared by structured and native Gemma calls."""
    try:
        if name in {"jira_search", "jira_get_issue", "jira_get_comments"}:
            return execute_jira(name, arguments)

        if name == "list_files":
            return list_files(
                workspace=workspace,
                directory=arguments.get("directory", "."),
            )

        if name == "read_file":
            missing = _missing(arguments, "path")
            if missing:
                return {"error": "Missing required argument: path"}
            return read_file(workspace=workspace, path=arguments["path"])

        if name == "write_file":
            missing = _missing(arguments, "path", "content")
            if missing:
                return {"error": ("Missing required argument(s): " + ", ".join(missing))}
            return write_file(
                workspace=workspace,
                path=arguments["path"],
                content=arguments["content"],
            )

        if name == "run_python":
            missing = _missing(arguments, "script")
            if missing:
                return {"error": "Missing required argument: script"}
            return run_python(
                workspace=workspace,
                script=arguments["script"],
                arguments=arguments.get("arguments"),
                working_directory=arguments.get("working_directory", "."),
                stdin=arguments.get("stdin"),
                timeout_seconds=arguments.get("timeout_seconds"),
            )

        if name == "run_command":
            missing = _missing(arguments, "command")
            if missing:
                return {"error": "Missing required argument: command"}
            return run_command(
                workspace=workspace,
                command=arguments["command"],
                working_directory=arguments.get("working_directory", "."),
                stdin=arguments.get("stdin"),
                timeout_seconds=arguments.get("timeout_seconds"),
                description=arguments.get("description"),
            )

        return {
            "error": f"Unknown tool: {name}",
            "available_tools": list(AVAILABLE_TOOL_NAMES),
            "instruction": (
                f"Do not call {name!r} again. Use only the available tools. "
                "Use run_python for Python files or run_command for one "
                "Windows cmd.exe command."
            ),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
