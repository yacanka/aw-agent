"""Human-readable terminal presentation for the agent lifecycle."""

from __future__ import annotations

import json
import re
import sys
import textwrap
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from settings import redact

DEBUG_BLOCK_LIMIT = 20000
_SECRET_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|jsessionid)$"
)


def _debug_text(text: str) -> str:
    # Redact before truncation/JSON escaping so a split credential cannot leak.
    text = redact(text)
    text = re.sub(
        r'''(?im)(\b(?:password|passwd|secret|[\w-]*token|api[_-]?key|authorization|cookie|jsessionid)'''
        r'''\b["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;]+)''',
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r'''(?i)(--(?:password|passwd|secret|token|api-key)\s+)("[^"]*"|'[^']*'|\S+)''',
        r"\1[REDACTED]",
        text,
    )
    # Untrusted process/model output must not erase or rewrite the terminal log.
    return re.sub(
        r"[\x00-\x08\x0b-\x1f\x7f-\x9f]",
        lambda match: f"\\x{ord(match[0]):02x}",
        text,
    )


def _debug_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _debug_text(str(key)): (
                "[REDACTED]" if _SECRET_KEY.search(str(key)) else _debug_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_debug_value(item) for item in value]
    if isinstance(value, str):
        return _debug_text(value)
    return value


_TOOL_NAMES = {
    "list_files": "Dosya listesi",
    "read_file": "Dosya okuma",
    "write_file": "Dosya yazma",
    "run_python": "Python çalıştırma",
    "run_command": "Windows komutu",
    "jira_search": "Jira araması",
    "jira_get_issue": "Jira iş detayı",
    "jira_get_comments": "Jira yorumları",
}


def _short_text(value: Any, limit: int = 180) -> str:
    text = " ".join(_debug_text(str(value)).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _tool_action(name: str, arguments: dict[str, Any]) -> str:
    if name == "list_files":
        return f"Klasör listeleniyor: {_short_text(arguments.get('directory', '.'))}"
    if name == "read_file":
        return f"Dosya okunuyor: {_short_text(arguments.get('path', '?'))}"
    if name == "write_file":
        content = str(arguments.get("content", ""))
        size = len(content.encode("utf-8"))
        return f"Dosya yazılıyor: {_short_text(arguments.get('path', '?'))} ({size} bayt)"
    if name == "run_python":
        script = _short_text(arguments.get("script", "?"))
        directory = _short_text(arguments.get("working_directory", "."))
        return f"Python betiği çalıştırılıyor: {script} (klasör: {directory})"
    if name == "run_command":
        description = arguments.get("description")
        command = description or arguments.get("command", "?")
        return f"Komut çalıştırılıyor: {_short_text(command)}"
    if name == "jira_search":
        return f"Jira sorgulanıyor: {_short_text(arguments.get('jql', '?'))}"
    if name in {"jira_get_issue", "jira_get_comments"}:
        return f"Jira işi okunuyor: {_short_text(arguments.get('issue_key', '?'))}"
    return "Araç çalıştırılıyor."


def _result_summary(name: str, result: dict[str, Any]) -> str:
    error = result.get("error")
    if error or result.get("success") is False:
        return _short_text(error or "İşlem başarısız oldu.")
    if name == "list_files":
        return f"{len(result.get('files') or [])} öğe bulundu."
    if name == "read_file":
        size = len(str(result.get("content", "")).encode("utf-8"))
        return f"{size} bayt okundu."
    if name == "write_file":
        return f"{result.get('bytes_written', 0)} bayt yazıldı."
    if name in {"run_python", "run_command"}:
        exit_code = result.get("exit_code")
        stdout_size = len(str(result.get("stdout", "")))
        stderr_size = len(str(result.get("stderr", "")))
        details = f"Çıkış kodu: {exit_code}"
        if stdout_size or stderr_size:
            details += f", çıktı: {stdout_size} karakter, hata çıktısı: {stderr_size} karakter"
        return details + "."
    if name == "jira_search":
        count = len(result.get("issues") or [])
        return f"{count} iş getirildi (toplam: {result.get('total', count)})."
    if name == "jira_get_comments":
        count = len(result.get("comments") or [])
        return f"{count} yorum getirildi (toplam: {result.get('total', count)})."
    if name == "jira_get_issue":
        issue = result.get("issue") or {}
        key = _short_text(issue.get("key", "iş"))
        status = _short_text(issue.get("status", "bilinmiyor"))
        return f"{key} okundu (durum: {status})."
    return "İşlem tamamlandı."


class TerminalUI:
    """Render developer diagnostics with redaction and bounded payload blocks."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream

    def _write(self, text: str = "") -> None:
        print(_debug_text(text), file=self.stream or sys.stdout, flush=True)

    def debug(self, event: str, details: Any) -> None:
        self._write(f"[{datetime.now().astimezone().isoformat(timespec='milliseconds')}] {event}")
        safe = _debug_value(details)
        text = safe if isinstance(safe, str) else json.dumps(safe, ensure_ascii=False, indent=2)
        if len(text) > DEBUG_BLOCK_LIMIT:
            half = DEBUG_BLOCK_LIMIT // 2
            omitted = len(text) - DEBUG_BLOCK_LIMIT
            text = text[:half] + f"\n... [LOG TRUNCATED: {omitted} karakter] ...\n" + text[-half:]
        self._write("\n".join(f"        | {line}" for line in text.splitlines() or ["<empty>"]))

    def exception(self, stage: str, exc: Exception) -> None:
        # Stack locations are useful; source lines and locals may contain credentials.
        frames = [
            f"{frame.filename}:{frame.lineno} in {frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__)
        ]
        self.debug(
            f"ERROR {stage}",
            {"exception_type": type(exc).__name__, "message": str(exc), "stack": frames},
        )

    @contextmanager
    def operation(self, stage: str):
        started = time.monotonic()
        self.debug(f"START {stage}", {})
        try:
            yield
        except Exception as exc:
            self.exception(stage, exc)
            raise
        else:
            self.debug(f"DONE {stage}", {"duration_seconds": round(time.monotonic() - started, 3)})

    def show_banner(self, workspace: Path, model_path: Path, thoughts_enabled: bool) -> None:
        self._write("=" * 64)
        self._write(" Yerel Gemma Kodlama Ajanı")
        self._write("=" * 64)
        self._write(f"Çalışma alanı : {workspace}")
        self._write(f"Model          : {model_path.name}")
        state = "açık" if thoughts_enabled else "kapalı"
        self._write(f"Model düşüncesi: {state}")
        self._write("Log seviyesi   : DEBUG (parametreler, sonuçlar ve hata ayrıntıları)")
        self._write("Çıkmak için: exit, quit veya q")
        self._write()

    def request_started(self, max_steps: int) -> None:
        self._write()
        self._write("-" * 64)
        self._write("[GÖREV] İstek alındı; çalışma başlatılıyor.")
        self._write(f"[SÜREÇ] En fazla {max_steps} model adımı kullanılabilir.")

    def step_started(self, step: int, max_steps: int) -> None:
        self._write()
        self._write(f"[ADIM {step}/{max_steps}] Model durumu değerlendiriyor...")

    def invalid_response(self, attempt: int, limit: int) -> None:
        if attempt >= limit:
            self._write(f"[HATA] Model {limit} kez geçersiz yanıt verdi; süreç durduruluyor.")
            return
        self._write(f"[UYARI] Model yanıtı geçersiz; düzeltiliyor ({attempt}/{limit}).")

    def model_thoughts(self, thoughts: list[str]) -> None:
        if not thoughts:
            return
        self._write("[MODEL] Modelin düşüncesi:")
        for thought in thoughts:
            safe = _debug_text(thought).strip()
            if not safe:
                continue
            for line in safe.splitlines():
                wrapped = textwrap.wrap(line, width=96) or [""]
                for part in wrapped:
                    self._write(f"        {part}")

    def tool_plan(self, count: int) -> None:
        self._write(f"[PLAN] Model {count} araç işlemi seçti.")

    def tool_started(
        self, index: int, total: int, name: str, arguments: dict[str, Any], call_id: str = ""
    ) -> None:
        label = _TOOL_NAMES.get(name, name)
        self._write(f"[ARAÇ {index}/{total}] {label}")
        self._write(f"        {_tool_action(name, arguments)}")
        self.debug(f"TOOL CALL {name} {call_id}", {"arguments": arguments})

    def tool_finished(
        self, name: str, result: dict[str, Any], duration: float, call_id: str = ""
    ) -> None:
        failed = bool(
            result.get("error")
            or result.get("success") is False
            or result.get("timed_out")
            or result.get("exit_code") not in (None, 0)
        )
        status = "HATA" if failed else "OK"
        try:
            summary = _result_summary(name, result)
        except (TypeError, AttributeError):
            summary = "Araç sonucunun alanları beklenen biçimde değil; ayrıntılar aşağıda."
        self._write(f"        [{status}] {summary} ({duration:.1f} sn)")
        context = f"{name} {call_id}"
        metadata = {
            key: value
            for key, value in result.items()
            if key not in {"stdout", "stderr", "content"}
        }
        self.debug(f"TOOL RESULT {status} {context}", metadata)
        for key in ("stdout", "stderr", "content"):
            if key in result:
                self.debug(f"{key.upper()} {context}", result[key])

    def final_answer(self, answer: str, step: int, duration: float) -> None:
        self._write()
        self._write(f"[TAMAMLANDI] {step} adımda tamamlandı ({duration:.1f} sn).")
        self._write("[YANIT]")
        self._write(answer)

    def max_steps_reached(self, max_steps: int) -> None:
        self._write(f"[HATA] {max_steps} adımlık üst sınıra ulaşıldı; süreç durduruluyor.")

    def prompt(self) -> str:
        return "Göreviniz > "

    def error(self, message: str, startup: bool = False) -> None:
        label = "BAŞLATMA HATASI" if startup else "AJAN HATASI"
        self._write(f"[{label}] {redact(message)}")
