import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from zipfile import ZipFile

from runtime_check import wheel_inventory


class RuntimeInventoryTests(unittest.TestCase):
    def test_missing_or_incompatible_wheel_fails(self):
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
            directory = Path(temporary)
            self.assertFalse(wheel_inventory(directory))
            wheel = directory / "llama_cpp_python-0.3.35-cp39-cp39-linux_x86_64.whl"
            with ZipFile(wheel, "w") as archive:
                archive.writestr("llama_cpp_python.dist-info/WHEEL", "Tag: cp39-cp39-linux_x86_64")
            self.assertFalse(wheel_inventory(directory))

    def test_compatible_wheel_produces_hash_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
            wheel = directory / f"llama_cpp_python-0.3.35-{abi}-{abi}-win_amd64.whl"
            with ZipFile(wheel, "w") as archive:
                archive.writestr("llama_cpp_python.dist-info/WHEEL", f"Tag: {abi}-{abi}-win_amd64")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertTrue(wheel_inventory(directory))
            self.assertIn("sha256=", output.getvalue())
            self.assertIn(wheel.name, output.getvalue())
