"""CMD-friendly offline wheel inventory and target-host acceptance checks."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import platform
import struct
import sys
from pathlib import Path
from zipfile import ZipFile


def wheel_inventory(directory: Path) -> bool:
    """Print immutable hashes and reject an absent/incompatible runtime wheel."""
    expected = f"cp{sys.version_info.major}{sys.version_info.minor}"
    compatible = False
    for wheel in sorted(directory.glob("*.whl")):
        with wheel.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        print(f"{wheel.name}  sha256={digest}")
        if not wheel.name.startswith("llama_cpp_python-0.3.35-"):
            continue
        with ZipFile(wheel) as archive:
            metadata = [name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")]
            if len(metadata) != 1:
                continue
            tags = archive.read(metadata[0]).decode("utf-8").splitlines()
        supported = {
            f"Tag: {expected}-{expected}-win_amd64",
            f"Tag: {expected}-abi3-win_amd64",
        }
        compatible |= bool(supported.intersection(tags))
    if not compatible:
        print(f"FAIL: no llama-cpp-python 0.3.35 Windows amd64 wheel for {expected}")
    return compatible


def check_runtime(load_model: bool = False) -> bool:
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        print("FAIL: target requires Windows x64 Python")
        return False
    try:
        llama = importlib.import_module("llama_cpp")
        if llama.__version__ != "0.3.35":
            raise RuntimeError("Expected llama-cpp-python 0.3.35")
        if not llama.llama_supports_gpu_offload():
            raise RuntimeError("GPU offload is unavailable; check Vulkan build and driver")
        print("Runtime version and GPU offload support: OK")
        info = llama.llama_print_system_info()
        print(info.decode("utf-8", errors="replace") if isinstance(info, bytes) else info)
        print("Confirm that the build information identifies Vulkan, not another GPU backend.")
        if load_model:
            from agent import _build_llm

            model = _build_llm()
            try:
                model.create_chat_completion(
                    messages=[{"role": "user", "content": "Reply with one short greeting."}],
                    max_tokens=32,
                )
                print("Model load and inference: OK")
            finally:
                model.close()
    except (ImportError, OSError, ValueError, RuntimeError, AttributeError) as exc:
        from settings import redact

        print("FAIL: " + redact(str(exc)))
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheels-only", action="store_true", help="Validate ABI and print wheel SHA-256 inventory"
    )
    parser.add_argument("--load-model", action="store_true")
    parser.add_argument(
        "--jira", action="store_true", help="Verify the configured Jira session without printing it"
    )
    options = parser.parse_args()
    print(
        f"Python {platform.python_version()} / {platform.machine()} / {struct.calcsize('P') * 8}-bit"
    )
    if options.wheels_only:
        return 0 if wheel_inventory(Path(__file__).parent / "wheels") else 1
    success = check_runtime(options.load_model)
    if options.jira:
        from jira_client import JiraClient, JiraError

        try:
            identity = JiraClient()._get("myself")
            if not identity.get("name") and not identity.get("key"):
                raise JiraError("session_expired", "No authenticated Jira user")
            print("Jira session: OK")
        except (JiraError, OSError, ValueError):
            print(
                "FAIL: Jira session/configuration check failed; update .env or check corporate access"
            )
            success = False
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
