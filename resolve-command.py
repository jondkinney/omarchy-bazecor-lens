#!/usr/bin/env python3
"""Resolve one bounded executable name and emit at most one bounded path."""

import os
import re
import shutil
import stat
import sys


MAX_COMMAND_CHARS = 4096
MAX_OUTPUT_BYTES = 4096
MAX_PATH_ENV_BYTES = 64 * 1024
COMMAND = re.compile(r"(?:[A-Za-z0-9._][A-Za-z0-9._+-]*|/[A-Za-z0-9._+/\-]+)\Z")


def main(argv):
    if len(argv) != 2:
        return 1
    command = argv[1]
    if not command or len(command) > MAX_COMMAND_CHARS or not COMMAND.fullmatch(command):
        return 1

    path_env = os.environ.get("PATH", "")
    if len(path_env.encode("utf-8", errors="surrogateescape")) > MAX_PATH_ENV_BYTES:
        return 1
    resolved = shutil.which(command, path=path_env)
    if not resolved:
        return 1
    resolved = os.path.abspath(resolved)
    if "\n" in resolved or "\r" in resolved:
        return 1
    try:
        info = os.stat(resolved)
    except OSError:
        return 1
    if not stat.S_ISREG(info.st_mode) or not os.access(resolved, os.X_OK):
        return 1
    encoded = resolved.encode("utf-8", errors="surrogateescape")
    if len(encoded) > MAX_OUTPUT_BYTES:
        return 1
    sys.stdout.buffer.write(encoded + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
