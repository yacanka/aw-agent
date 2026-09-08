"""Validated startup configuration. Jira credentials are loaded on demand."""

import math
from pathlib import Path

from settings import load_settings

BASE_DIR = Path(__file__).resolve().parent
_values = {key: value for key, value in load_settings().items() if key.startswith("GEMMA_")}


def _integer(name: str, default: int, minimum: int) -> int:
    try:
        value = int(_values.get(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _boolean(name: str, default: bool) -> bool:
    value = _values.get(name, "1" if default else "0")
    if value not in {"0", "1"}:
        raise ValueError(f"{name} must be 0 or 1")
    return value == "1"


MODEL_PATH = Path(_values.get("GEMMA_MODEL_PATH", str(BASE_DIR / "model/model.gguf"))).resolve()
WORKSPACE = Path(_values.get("GEMMA_AGENT_WORKSPACE", str(BASE_DIR / "workspace"))).resolve()
if WORKSPACE == BASE_DIR or WORKSPACE in BASE_DIR.parents:
    raise ValueError("Workspace must not contain the application directory and .env")
N_CTX = _integer("GEMMA_N_CTX", 16384, 512)
N_GPU_LAYERS = _integer("GEMMA_N_GPU_LAYERS", -1, -1)
N_THREADS = _integer("GEMMA_N_THREADS", 0, 0)
MAX_TOKENS = _integer("GEMMA_MAX_TOKENS", 2048, 1)
if MAX_TOKENS >= N_CTX:
    raise ValueError("GEMMA_MAX_TOKENS must be smaller than GEMMA_N_CTX")
try:
    TEMPERATURE = float(_values.get("GEMMA_TEMPERATURE", "0.2"))
except ValueError:
    raise ValueError("GEMMA_TEMPERATURE must be a number") from None
if not math.isfinite(TEMPERATURE) or not 0 <= TEMPERATURE <= 2:
    raise ValueError("GEMMA_TEMPERATURE must be between 0 and 2")
MAX_AGENT_STEPS = _integer("GEMMA_MAX_AGENT_STEPS", 15, 1)
MAX_UNKNOWN_TOOL_ATTEMPTS = _integer("GEMMA_MAX_UNKNOWN_TOOL_ATTEMPTS", 3, 1)
VERBOSE_LLAMA = _boolean("GEMMA_VERBOSE_LLAMA", False)
SHOW_MODEL_THOUGHTS = _boolean("GEMMA_SHOW_THOUGHTS", True)
COMMAND_TIMEOUT_SECONDS = _integer("GEMMA_COMMAND_TIMEOUT_SECONDS", 30, 1)
COMMAND_MAX_OUTPUT_CHARS = _integer("GEMMA_COMMAND_MAX_OUTPUT_CHARS", 20000, 1000)
COMMAND_MAX_LENGTH = _integer("GEMMA_COMMAND_MAX_LENGTH", 4000, 1)
FILE_MAX_BYTES = _integer("GEMMA_FILE_MAX_BYTES", 1048576, 1024)
COMMAND_ALLOWED_PROGRAMS = tuple(
    item.strip().lower()
    for item in _values.get(
        "GEMMA_COMMAND_ALLOWLIST",
        "python,python.exe,py,py.exe,pytest,pytest.exe,ruff,ruff.exe,mypy,mypy.exe,git,git.exe,dir,type,where,tree",
    ).split(",")
    if item.strip()
)
ALLOW_CMD_OPERATORS = False
if _boolean("GEMMA_ALLOW_CMD_OPERATORS", False):
    raise ValueError("GEMMA_ALLOW_CMD_OPERATORS=1 is no longer supported")
