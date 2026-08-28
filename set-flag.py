#!/usr/bin/env python3
"""Set one boolean on this plugin's own entry in shell.json.

    set-flag.py <key> <true|false>

Called from LensPanel.qml with the key and value as argv, never as shell text.
Both are checked again here, since a script on disk can be run by anything.

Every filesystem operation is descriptor-relative. The parent directory is
opened once and pinned, and the config, the temporary file and the rename all
happen relative to that descriptor, so the directory cannot be swapped underneath
us partway through. The config itself is opened exactly once, with O_NOFOLLOW so
a symlink at that name is refused rather than followed, and O_NONBLOCK so a FIFO
parked there returns immediately instead of blocking the open until a writer
appears. Everything afterwards -- the regular-file check, the read, the
permission bits -- comes from that one descriptor. Resolving the name a second
time would leave a window in which it could be pointed at a different file
between the check and the use.
"""
import json
import os
import stat
import sys

PLUGIN_ID = "io.github.jondkinney.bazecor-lens"
ALLOWED_KEYS = {"floatOverlay", "pinOverlay", "themedBorder", "rememberPosition"}
# A shell config is a few kilobytes. This only exists so a pathological file
# cannot be read into memory unbounded.
MAX_BYTES = 8 * 1024 * 1024

O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def fail(message):
    print(f"set-flag: {message}", file=sys.stderr)
    raise SystemExit(1)


def open_temp(dir_fd):
    """O_EXCL create with an unpredictable name, relative to the pinned parent.

    Returns an open write descriptor and the name, so the caller never has to
    resolve the path again. Created 0600 and widened only after it is written.
    """
    for _ in range(64):
        name = ".shell.json." + os.urandom(8).hex()
        try:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_CLOEXEC,
                0o600,
                dir_fd=dir_fd,
            )
        except FileExistsError:
            continue
        return fd, name
    fail("could not create a temporary file")


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
    directory = os.path.join(config_home, "omarchy")
    name = "shell.json"
    path = os.path.join(directory, name)

    # The parent is resolved once and held open for the rest of the run. A
    # symlinked config directory is a normal dotfiles layout, so this follows
    # links here; it is the final component that must not be one.
    try:
        dir_fd = os.open(directory, os.O_RDONLY | O_DIRECTORY | O_CLOEXEC)
    except OSError as err:
        fail(f"cannot open {directory}: {err.strerror}")

    try:
        if not stat.S_ISDIR(os.fstat(dir_fd).st_mode):
            fail(f"{directory} is not a directory")

        try:
            fd = os.open(name, os.O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, dir_fd=dir_fd)
        except OSError as err:
            fail(f"cannot open {path}: {err.strerror}")

        try:
            info = os.fstat(fd)
            # Fail closed on anything that is not a plain file. With O_NONBLOCK
            # above, a FIFO reaches this check instead of hanging in open().
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

        tmp_fd, tmp_name = open_temp(dir_fd)
        try:
            # Written and permissioned through the descriptor we created, never
            # by reopening the temporary name.
            os.fchmod(tmp_fd, stat.S_IMODE(info.st_mode))
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except OSError:
                pass
            raise
    finally:
        os.close(dir_fd)


if __name__ == "__main__":
    main(sys.argv)
