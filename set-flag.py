#!/usr/bin/env python3
"""Set one boolean on this plugin's own entry in shell.json.

    set-flag.py <key> <true|false>

Called from LensPanel.qml with the key and value as argv, never as shell text.
Both are checked again here, since a script on disk can be run by anything.

The config is opened exactly once, with O_NOFOLLOW, and everything afterwards —
the size check, the read, the permission bits — comes from that one descriptor.
Checking the path and then reopening it would leave a window in which the name
could be pointed at a different file between the check and the use, so the name
is never resolved twice.
"""
import json
import os
import stat
import sys
import tempfile

PLUGIN_ID = "io.github.jondkinney.bazecor-lens"
ALLOWED_KEYS = {"floatOverlay", "pinOverlay", "themedBorder", "rememberPosition"}
# A shell config is a few kilobytes. This only exists so a pathological file
# cannot be read into memory unbounded.
MAX_BYTES = 8 * 1024 * 1024


def fail(message):
    print(f"set-flag: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(argv):
    if len(argv) != 3:
        fail("usage: set-flag.py <key> <true|false>")
    key, raw_value = argv[1], argv[2]
    if key not in ALLOWED_KEYS:
        fail(f"refusing unknown key: {key}")
    if raw_value not in ("true", "false"):
        fail("value must be true or false")
    value = raw_value == "true"

    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(config_home, "omarchy", "shell.json")
    directory = os.path.dirname(path)

    # O_NOFOLLOW fails outright if the final component is a symlink, so a link
    # planted at this path is refused rather than followed.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except OSError as err:
        fail(f"cannot open {path}: {err.strerror}")

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            fail(f"{path} is not a regular file")
        if info.st_size > MAX_BYTES:
            fail(f"{path} is larger than {MAX_BYTES} bytes, refusing")
        # Read from the descriptor already validated above, not by reopening
        # the name. Mode comes from the same fstat for the same reason.
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = None
            raw = handle.read(MAX_BYTES + 1)
    finally:
        if fd is not None:
            os.close(fd)

    if len(raw) > MAX_BYTES:
        fail("config grew while being read, refusing")
    try:
        config = json.loads(raw)
    except ValueError as err:
        fail(f"{path} is not valid JSON: {err}")
    if not isinstance(config, dict):
        fail(f"{path} is not a JSON object")

    def patch(entries):
        if not isinstance(entries, list):
            return entries
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id") == PLUGIN_ID:
                entry[key] = value
        return entries

    patch(config.get("plugins"))
    layout = (config.get("bar") or {}).get("layout") or {}
    for section in ("left", "center", "right"):
        patch(layout.get(section))

    rendered = json.dumps(config, indent=2) + "\n"

    # mktemp equivalent in the destination directory: created O_EXCL with an
    # unpredictable name, so the replace below is a rename within one
    # filesystem rather than a copy across one.
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".shell.json.", dir=directory)
    try:
        os.fchmod(tmp_fd, stat.S_IMODE(info.st_mode))
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


if __name__ == "__main__":
    main(sys.argv)
