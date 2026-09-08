"""Private launch gate: the parent assigns the Job before sending argv.

Run with Python -I so workspace modules cannot shadow standard-library imports.
Do not use buffered input here: bytes after the first newline belong to the
target process, including interactive stdin supplied by the user.
"""

import json
import os
import subprocess
import sys


def main() -> int:
    if os.name == "nt":
        import msvcrt

        msvcrt.setmode(0, os.O_BINARY)
    line = bytearray()
    while len(line) <= 1048576:
        character = os.read(0, 1)
        if not character:
            return 125
        if character == b"\n":
            argv = json.loads(line.decode("utf-8"))
            try:
                if len(argv) == 5 and argv[1:4] == ["/d", "/s", "/c"]:
                    # cmd.exe does not understand list2cmdline's backslash quote
                    # escaping. Supply the already validated /c text verbatim.
                    command_line = '"' + argv[0] + '" /d /s /c ' + argv[4]
                    return subprocess.call(command_line, executable=argv[0], shell=False)
                return subprocess.call(argv, shell=False)
            except OSError:
                print("Target process could not be started", file=sys.stderr)
                return 126
        line.extend(character)
    return 125


if __name__ == "__main__":
    sys.exit(main())
