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
                os.O_RDWR | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_CLOEXEC,
                0o600,
                dir_fd=dir_fd,
            )
        except FileExistsError:
            continue
        return fd, name
    fail("could not create a temporary file")


def same_file(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def validate_config(info, path):
    """Validate security properties from the already-open config descriptor."""
    if not stat.S_ISREG(info.st_mode):
        fail(f"{path} is not a regular file")
    if info.st_uid != os.geteuid():
        fail(f"{path} is not owned by the current user")
    mode = stat.S_IMODE(info.st_mode)
    if mode not in (0o600, 0o640, 0o644):
        fail(f"{path} has unsafe mode {mode:04o}; expected 0600, 0640, or 0644")
    if info.st_size > MAX_BYTES:
        fail(f"{path} is larger than {MAX_BYTES} bytes, refusing")
    return mode


def read_bounded(fd):
    chunks = []
    remaining = MAX_BYTES + 1
    while remaining:
        chunk = os.read(fd, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if len(raw) > MAX_BYTES:
        fail("config grew while being read, refusing")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as err:
        fail(f"config is not valid UTF-8: {err}")


def write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written == 0:
            fail("short write while saving config")
        view = view[written:]


def stat_name(dir_fd, name, description):
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as err:
        fail(f"cannot inspect {description}: {err.strerror}")


def unlink_if_same(dir_fd, name, held_info):
    """Remove only our own temporary, never a replacement planted at its name."""
    try:
        named_info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if same_file(named_info, held_info):
            os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass


def main(argv):
    if len(argv) != 3:
        fail("usage: set-flag.py <key> <true|false>")
    if not all((O_CLOEXEC, O_NOFOLLOW, O_NONBLOCK, O_DIRECTORY)):
        fail("this helper requires Linux descriptor-safety flags")
    key, raw_value = argv[1], argv[2]
    if key not in ALLOWED_KEYS:
        fail(f"refusing unknown key: {key}")
    if raw_value not in ("true", "false"):
        fail("value must be true or false")
    value = raw_value == "true"

    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
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

    source_fd = None
    try:
        directory_info = os.fstat(dir_fd)
        if not stat.S_ISDIR(directory_info.st_mode):
            fail(f"{directory} is not a directory")
        if directory_info.st_uid != os.geteuid() or stat.S_IMODE(directory_info.st_mode) & 0o022:
            fail(f"{directory} must be owned by the current user and not group/world-writable")

        try:
            source_fd = os.open(
                name,
                os.O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC,
                dir_fd=dir_fd,
            )
        except OSError as err:
            fail(f"cannot open {path}: {err.strerror}")

        # Keep this descriptor open until after publication. Its identity,
        # ownership, type and mode are the authority for both the read and the
        # decision to replace shell.json.
        source_info = os.fstat(source_fd)
        source_mode = validate_config(source_info, path)
        raw = read_bounded(source_fd)
        after_read_info = os.fstat(source_fd)
        validate_config(after_read_info, path)
        if (
            not same_file(source_info, after_read_info)
            or source_info.st_size != after_read_info.st_size
            or source_info.st_mtime_ns != after_read_info.st_mtime_ns
            or source_info.st_ctime_ns != after_read_info.st_ctime_ns
        ):
            fail(f"{path} changed while it was being read")
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

        rendered = (json.dumps(config, indent=2) + "\n").encode("utf-8")
        if len(rendered) > MAX_BYTES:
            fail(f"updated config would exceed {MAX_BYTES} bytes, refusing")

        tmp_fd, tmp_name = open_temp(dir_fd)
        tmp_info = os.fstat(tmp_fd)
        published = False
        try:
            # Written and permissioned through the descriptor we created, never
            # by reopening the temporary name. The original descriptor remains
            # open across replace so the published inode can be compared to it.
            os.fchmod(tmp_fd, source_mode)
            write_all(tmp_fd, rendered)
            os.fsync(tmp_fd)

            if not same_file(stat_name(dir_fd, tmp_name, tmp_name), tmp_info):
                fail(f"{tmp_name} was replaced while it was being written")
            if not same_file(stat_name(dir_fd, name, path), source_info):
                fail(f"{path} was replaced while it was being updated")
            current_source_info = os.fstat(source_fd)
            validate_config(current_source_info, path)
            if (
                current_source_info.st_size != after_read_info.st_size
                or current_source_info.st_mtime_ns != after_read_info.st_mtime_ns
                or current_source_info.st_ctime_ns != after_read_info.st_ctime_ns
            ):
                fail(f"{path} changed while it was being updated")

            os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            published = True
            if not same_file(stat_name(dir_fd, name, path), tmp_info):
                fail(f"published {path} does not match the file that was written")
            os.fsync(dir_fd)
        except BaseException:
            if not published:
                unlink_if_same(dir_fd, tmp_name, tmp_info)
            raise
        finally:
            os.close(tmp_fd)
    finally:
        if source_fd is not None:
            os.close(source_fd)
        os.close(dir_fd)


if __name__ == "__main__":
    main(sys.argv)
