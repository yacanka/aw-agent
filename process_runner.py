"""Bounded, concurrent pipe draining and process-tree cancellation."""

from __future__ import annotations

import codecs
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Sequence

from config import COMMAND_MAX_OUTPUT_CHARS
from settings import child_environment, redact
from windows_job import WindowsJob

IS_WINDOWS = os.name == "nt"


class OutputBuffer:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.head = ""
        self.tail = ""
        self.total = 0

    def append(self, value: str) -> None:
        self.total += len(value)
        remaining = self.limit // 2 - len(self.head)
        if remaining > 0:
            self.head += value[:remaining]
            value = value[remaining:]
        self.tail = (self.tail + value)[-(self.limit - self.limit // 2) :]

    def result(self) -> tuple[str, bool]:
        truncated = self.total > self.limit
        marker = "\n... [output truncated] ...\n" if truncated else ""
        return redact(self.head + marker + self.tail), truncated


def _drain(stream: Any, buffer: OutputBuffer, encoding: str) -> None:
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    try:
        while chunk := stream.read(8192):
            buffer.append(decoder.decode(chunk))
        buffer.append(decoder.decode(b"", final=True))
    finally:
        stream.close()


def _feed(stream: Any, data: bytes) -> None:
    try:
        stream.write(data)
        stream.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        stream.close()


def run_process(
    argv: Sequence[str],
    cwd: Path,
    stdin_text: str | None,
    timeout_seconds: int,
    output_encoding: str = "utf-8",
    python_utf8: bool = False,
) -> dict[str, Any]:
    if stdin_text is not None and not isinstance(stdin_text, str):
        return {"success": False, "error": "stdin must be a string"}
    if stdin_text is not None and len(stdin_text) > 1048576:
        return {"success": False, "error": "stdin exceeds 1 MiB character limit"}
    job = None
    process = None
    threads = []
    timed_out = False
    stdout = OutputBuffer(COMMAND_MAX_OUTPUT_CHARS)
    stderr = OutputBuffer(COMMAND_MAX_OUTPUT_CHARS)
    stage = "process.job.create"
    try:
        if IS_WINDOWS:
            job = WindowsJob()
        worker = Path(__file__).with_name("process_worker.py")
        environment = child_environment()
        if python_utf8:
            # Apply to launcher-selected interpreters too, without rewriting
            # py's version selectors. Override inherited pipe encodings.
            environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        stage = "process.spawn"
        process = subprocess.Popen(
            [sys.executable, "-I", str(worker)],
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0,
            start_new_session=not IS_WINDOWS,
        )
        if job:
            # No target argv is released until assignment succeeds.
            stage = "process.job.assign"
            job.assign(process.pid)
        stage = "process.io"
        data = (json.dumps(list(argv)) + "\n").encode("utf-8")
        data += (stdin_text or "").encode(output_encoding, errors="replace")
        for function, arguments in (
            (_drain, (process.stdout, stdout, output_encoding)),
            (_drain, (process.stderr, stderr, output_encoding)),
            (_feed, (process.stdin, data)),
        ):
            thread = threading.Thread(target=function, args=arguments, daemon=True)
            threads.append(thread)
            thread.start()
        try:
            stage = "process.wait"
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
    except OSError as exc:
        return {
            "success": False,
            "error": "Process containment or startup failed; target was not released",
            "error_stage": stage,
            "exception_type": type(exc).__name__,
            "exception_message": redact(str(exc)),
            "errno": exc.errno,
            "winerror": getattr(exc, "winerror", None),
        }
    finally:
        if job:
            job.close()
        if process:
            if not IS_WINDOWS:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            process.wait()
            for thread in threads:
                thread.join(timeout=5)
            if not threads:
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
    out, out_cut = stdout.result()
    err, err_cut = stderr.result()
    result = {
        "success": not timed_out and process.returncode == 0,
        "exit_code": None if timed_out else process.returncode,
        "stdout": out,
        "stderr": err,
        "stdout_truncated": out_cut,
        "stderr_truncated": err_cut,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
    }
    if timed_out:
        result["error"] = f"Command timed out after {timeout_seconds} seconds"
        result["error_stage"] = "process.wait"
    return result
