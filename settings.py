"""Literal .env loading; credentials never enter os.environ."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping

ENV_PATH = Path(__file__).resolve().parent / ".env"


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid .env assignment at line {number}")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Unclosed .env quote at line {number}")
            value = value[1:-1]
        values[key] = value
    return values


def load_settings(
    path: Path = ENV_PATH, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    return {**read_env(path), **(os.environ if environ is None else environ)}


def child_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"JIRA_JSESSIONID", "JSESSIONID"}
    }


def redact(text: str) -> str:
    try:
        sources = [read_env(), os.environ]
    except (OSError, ValueError):
        sources = [os.environ]
    for source in sources:
        for key in ("JIRA_JSESSIONID", "JSESSIONID"):
            value = source.get(key)
            if value:
                text = text.replace(value, "[REDACTED]")
    return re.sub(
        r"(?im)(\b(?:cookie|set-cookie|authorization|jsessionid)\s*[:=]\s*)[^\r\n]+",
        r"\1[REDACTED]",
        text,
    )
