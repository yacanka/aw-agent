"""Streaming response assembly and cooperative inference deadline monitoring."""

from __future__ import annotations

import threading
import time


def _merge_delta(message: dict, calls: dict, delta: dict) -> None:
    for field in ("content", "reasoning_content"):
        value = delta.get(field)
        if value is not None:
            message[field] = message.get(field, "") + value
    for part in delta.get("tool_calls") or []:
        index = part["index"]
        if type(index) is not int or not 0 <= index < 16:
            raise ValueError("Expected at most 16 indexed tool calls")
        target = calls.setdefault(index, {"type": "function", "function": {}})
        if part.get("id"):
            target["id"] = part["id"]
        for field in ("name", "arguments"):
            value = (part.get("function") or {}).get(field)
            if value is not None:
                function = target["function"]
                function[field] = function.get(field, "") + value


def complete_chat(llm, terminal, step: int, timeout_seconds: int, **kwargs) -> dict:
    """Discard partial output on timeout. Native calls cannot be forcibly interrupted.

    The monitor reports elapsed time even before the first stream chunk. A chunk
    is not a token: chat handlers may buffer output or emit metadata separately.
    """
    started = time.monotonic()
    stopped = threading.Event()
    lock = threading.Lock()
    progress = {"chunks_received": 0, "last_chunk_at": None}

    def report():
        while not stopped.wait(5):
            elapsed = time.monotonic() - started
            with lock:
                details = dict(progress)
            last = details.pop("last_chunk_at")
            details.update(
                elapsed_seconds=round(elapsed, 1),
                seconds_since_last_chunk=None
                if last is None
                else round(time.monotonic() - last, 1),
                timeout_seconds=timeout_seconds,
                deadline_exceeded=elapsed >= timeout_seconds,
            )
            if elapsed >= timeout_seconds:
                details["status"] = (
                    "Süre sınırı aşıldı; model henüz kontrolü döndürmedi. "
                    "Sonuç geldiğinde kullanılmayacak. Ctrl+C ile durdurmayı deneyin; "
                    "yanıt vermiyorsa süreci kapatıp yeniden başlatın."
                )
            terminal.debug(f"WAIT step={step} model.inference", details)

    def check_deadline():
        if time.monotonic() - started >= timeout_seconds:
            raise TimeoutError(
                f"Model inference exceeded {timeout_seconds} seconds; partial output discarded. "
                "Confirmed tool results are retained in chat history. "
                "Adjust GEMMA_INFERENCE_TIMEOUT_SECONDS for slower hardware."
            )

    monitor = threading.Thread(target=report, name="inference-progress", daemon=True)
    monitor.start()
    response = None
    try:
        response = llm.create_chat_completion(stream=True, **kwargs)
        check_deadline()
        # Preserve compatibility with injected non-streaming model adapters.
        if isinstance(response, dict):
            return response
        message, calls = {"role": "assistant", "content": ""}, {}
        finish, usage = None, None
        for chunk in response:
            check_deadline()
            with lock:
                progress["chunks_received"] += 1
                progress["last_chunk_at"] = time.monotonic()
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                if choice.get("index", 0) != 0:
                    continue
                _merge_delta(message, calls, choice.get("delta") or {})
                if choice.get("finish_reason") is not None:
                    finish = choice["finish_reason"]
        check_deadline()
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
        # An exhausted stream without a terminal marker is incomplete, even if
        # its partial tool arguments happen to be valid JSON.
        return {
            "choices": [{"message": message, "finish_reason": finish or "incomplete"}],
            "usage": usage,
        }
    finally:
        stopped.set()
        monitor.join()
        if response is not None and hasattr(response, "close"):
            response.close()
