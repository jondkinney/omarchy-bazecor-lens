#!/usr/bin/env python3
"""Install the pinned Bazecor AppImage without pathname check/use gaps.

Destination directories are opened once, component by component, relative to
the pinned home directory. Every created file stays open while it is renamed
into place, and the published name must resolve to that held inode before the
installer reports success.
"""

import hashlib
import http.client
import os
import ssl
import stat
import subprocess
import sys
import time
import urllib.parse


BAZECOR_VERSION = "v1.10.0-wayland.1"
BAZECOR_ASSET = "Bazecor-1.10.0-x64.AppImage"
BAZECOR_SHA256 = "4929784eb1874f9b758b6a02a2ede0160ad48a4058fc8addd013ded074b2933a"
BAZECOR_URL = (
    "https://github.com/jondkinney/Bazecor/releases/download/"
    f"{BAZECOR_VERSION}/{BAZECOR_ASSET}"
)

ALLOWED_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
MAX_APPIMAGE_BYTES = 256 * 1024 * 1024
MAX_REDIRECTS = 5
CONNECT_TIMEOUT_SECONDS = 15
READ_TIMEOUT_SECONDS = 60
TOTAL_TIMEOUT_SECONDS = 60 * 60
LOW_SPEED_WINDOW_SECONDS = 60
LOW_SPEED_BYTES_PER_SECOND = 1024
CHUNK_BYTES = 1024 * 1024

O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
O_PATH = getattr(os, "O_PATH", 0)


class InstallError(Exception):
    pass


def fail(message):
    raise InstallError(message)


def same_file(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def validate_directory(info, description):
    if not stat.S_ISDIR(info.st_mode):
        fail(f"{description} is not a directory")
    if info.st_uid != os.geteuid():
        fail(f"{description} is not owned by the current user")
    if stat.S_IMODE(info.st_mode) & 0o022:
        fail(f"{description} is group/world-writable")


def open_home():
    home = os.environ.get("HOME", "")
    if not home or not os.path.isabs(home) or os.path.normpath(home) != home:
        fail("HOME must be an absolute, normalized path")
    if any(ord(character) < 32 or ord(character) == 127 for character in home):
        fail("HOME contains control characters")
    try:
        fd = os.open(home, os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
    except OSError as err:
        fail(f"cannot securely open HOME {home}: {err.strerror}")
    try:
        validate_directory(os.fstat(fd), f"HOME {home}")
    except BaseException:
        os.close(fd)
        raise
    return home, fd


def open_or_create_directory(parent_fd, name, description):
    if not name or "/" in name or name in (".", ".."):
        fail(f"unsafe directory component: {name!r}")
    flags = os.O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
        try:
            fd = os.open(name, flags, dir_fd=parent_fd)
        except OSError as err:
            fail(f"cannot securely open {description}: {err.strerror}")
    except OSError as err:
        fail(f"cannot securely open {description}: {err.strerror}")
    try:
        validate_directory(os.fstat(fd), description)
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_destinations(home_fd):
    descriptors = []
    try:
        local_fd = open_or_create_directory(home_fd, ".local", "~/.local")
        descriptors.append(local_fd)
        share_fd = open_or_create_directory(local_fd, "share", "~/.local/share")
        descriptors.append(share_fd)
        app_fd = open_or_create_directory(share_fd, "bazecor", "~/.local/share/bazecor")
        descriptors.append(app_fd)
        desktop_fd = open_or_create_directory(
            share_fd, "applications", "~/.local/share/applications"
        )
        descriptors.append(desktop_fd)
        bin_fd = open_or_create_directory(local_fd, "bin", "~/.local/bin")
        descriptors.append(bin_fd)
        return descriptors, app_fd, bin_fd, desktop_fd
    except BaseException:
        for fd in reversed(descriptors):
            os.close(fd)
        raise


def verify_directory_binding(parent_fd, name, child_fd, description):
    named_info = stat_name(parent_fd, name, description)
    held_info = os.fstat(child_fd)
    if not same_file(named_info, held_info) or not stat.S_ISDIR(named_info.st_mode):
        fail(f"{description} was replaced during installation")


def open_temp(dir_fd, prefix):
    for _ in range(64):
        name = prefix + os.urandom(12).hex()
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
    fail("could not create a secure temporary file")


def stat_name(dir_fd, name, description):
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as err:
        fail(f"cannot inspect {description}: {err.strerror}")


def unlink_if_same(dir_fd, name, held_info):
    try:
        named_info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if same_file(named_info, held_info):
            os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass


def write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written == 0:
            fail("short write while staging a file")
        view = view[written:]


def validate_source(info, source):
    if not stat.S_ISREG(info.st_mode):
        fail(f"source is not a regular file: {source}")
    if info.st_uid != os.geteuid():
        fail(f"source is not owned by the current user: {source}")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o7022:
        fail(f"source has unsafe mode {mode:04o}: {source}")
    if info.st_size > MAX_APPIMAGE_BYTES:
        fail(f"source exceeds the {MAX_APPIMAGE_BYTES}-byte ceiling: {source}")


def copy_source_once(source, destination_fd):
    flags = os.O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC
    try:
        source_fd = os.open(source, flags)
    except OSError as err:
        fail(f"cannot securely open source {source}: {err.strerror}")

    try:
        before = os.fstat(source_fd)
        validate_source(before, source)
        copied = 0
        while True:
            chunk = os.read(source_fd, min(CHUNK_BYTES, MAX_APPIMAGE_BYTES - copied + 1))
            if not chunk:
                break
            copied += len(chunk)
            if copied > MAX_APPIMAGE_BYTES:
                fail(f"source exceeds the {MAX_APPIMAGE_BYTES}-byte ceiling")
            write_all(destination_fd, chunk)
        after = os.fstat(source_fd)
        validate_source(after, source)
        if (
            not same_file(before, after)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            fail(f"source changed while it was being copied: {source}")
    finally:
        os.close(source_fd)


def validate_download_url(url):
    if len(url) > 16 * 1024 or any(ord(character) < 32 for character in url):
        fail("download redirect contains invalid characters")
    try:
        url.encode("ascii")
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (UnicodeError, ValueError) as err:
        fail(f"invalid download URL: {err}")
    if parsed.scheme != "https":
        fail(f"refusing non-HTTPS download URL: {url}")
    if parsed.username is not None or parsed.password is not None:
        fail(f"refusing credentials in download URL: {url}")
    if parsed.hostname not in ALLOWED_HOSTS or port not in (None, 443):
        fail(f"download redirect uses an unexpected host: {url}")
    return parsed


def download_into(destination_fd):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    current = BAZECOR_URL
    deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS

    for redirect_count in range(MAX_REDIRECTS + 1):
        if time.monotonic() > deadline:
            fail(f"download exceeded {TOTAL_TIMEOUT_SECONDS} seconds")
        parsed = validate_download_url(current)
        request_target = parsed.path or "/"
        if parsed.query:
            request_target += "?" + parsed.query

        connection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port or 443,
            timeout=CONNECT_TIMEOUT_SECONDS,
            context=context,
        )
        response = None
        try:
            connection.request(
                "GET",
                request_target,
                headers={
                    "Accept": "application/octet-stream",
                    "Accept-Encoding": "identity",
                    "User-Agent": "omarchy-bazecor-lens-installer/1",
                },
            )
            response = connection.getresponse()

            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    fail(f"download redirect {response.status} had no Location header")
                if redirect_count == MAX_REDIRECTS:
                    fail(f"download exceeded {MAX_REDIRECTS} redirects")
                # Validate now, before a request is ever sent to the next hop.
                current = urllib.parse.urljoin(current, location)
                validate_download_url(current)
                continue

            if response.status != 200:
                fail(f"download failed with HTTP {response.status} {response.reason}")

            length = response.getheader("Content-Length")
            if length is not None:
                try:
                    declared_length = int(length, 10)
                except ValueError:
                    fail("download returned an invalid Content-Length")
                if declared_length < 0 or declared_length > MAX_APPIMAGE_BYTES:
                    fail(f"download exceeds the {MAX_APPIMAGE_BYTES}-byte ceiling")

            if connection.sock is not None:
                connection.sock.settimeout(READ_TIMEOUT_SECONDS)
            downloaded = 0
            speed_window_started = time.monotonic()
            speed_window_bytes = 0
            reader = getattr(response, "read1", response.read)
            while True:
                if time.monotonic() > deadline:
                    fail(f"download exceeded {TOTAL_TIMEOUT_SECONDS} seconds")
                chunk = reader(min(CHUNK_BYTES, MAX_APPIMAGE_BYTES - downloaded + 1))
                now = time.monotonic()
                if now > deadline:
                    fail(f"download exceeded {TOTAL_TIMEOUT_SECONDS} seconds")
                if not chunk:
                    break
                downloaded += len(chunk)
                speed_window_bytes += len(chunk)
                if downloaded > MAX_APPIMAGE_BYTES:
                    fail(f"download exceeds the {MAX_APPIMAGE_BYTES}-byte ceiling")
                write_all(destination_fd, chunk)
                elapsed = now - speed_window_started
                if elapsed >= LOW_SPEED_WINDOW_SECONDS:
                    if speed_window_bytes < LOW_SPEED_BYTES_PER_SECOND * elapsed:
                        fail(
                            "download stayed below "
                            f"{LOW_SPEED_BYTES_PER_SECOND} bytes/second for "
                            f"{LOW_SPEED_WINDOW_SECONDS} seconds"
                        )
                    speed_window_started = now
                    speed_window_bytes = 0
            return
        except (OSError, http.client.HTTPException) as err:
            fail(f"download failed: {err}")
        finally:
            if response is not None:
                response.close()
            connection.close()

    fail(f"download exceeded {MAX_REDIRECTS} redirects")


def hash_held_file(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    hashed = 0
    while True:
        chunk = os.read(fd, min(CHUNK_BYTES, MAX_APPIMAGE_BYTES - hashed + 1))
        if not chunk:
            break
        hashed += len(chunk)
        if hashed > MAX_APPIMAGE_BYTES:
            fail(f"staged AppImage exceeds the {MAX_APPIMAGE_BYTES}-byte ceiling")
        digest.update(chunk)
    return digest.hexdigest()


def publish_staged_appimage(app_fd, source):
    staged_fd, staged_name = open_temp(app_fd, ".Bazecor.AppImage.")
    staged_info = os.fstat(staged_fd)
    published = False
    try:
        if source is not None:
            copy_source_once(source, staged_fd)
        else:
            print(f"Downloading {BAZECOR_ASSET} {BAZECOR_VERSION}")
            download_into(staged_fd)

        os.fsync(staged_fd)
        actual = hash_held_file(staged_fd)
        if actual != BAZECOR_SHA256:
            fail(
                "digest mismatch\n"
                f"  expected {BAZECOR_SHA256}\n"
                f"  got      {actual}\n"
                "Refusing to install. Delete the file and try again, and if it keeps "
                "failing, do not run it."
            )
        print("  digest verified")

        os.fchmod(staged_fd, 0o755)
        os.fsync(staged_fd)
        if not same_file(stat_name(app_fd, staged_name, staged_name), staged_info):
            fail(f"{staged_name} was replaced while it was being prepared")

        os.replace(
            staged_name,
            "Bazecor.AppImage",
            src_dir_fd=app_fd,
            dst_dir_fd=app_fd,
        )
        published = True
        if not same_file(
            stat_name(app_fd, "Bazecor.AppImage", "published Bazecor.AppImage"),
            staged_info,
        ):
            fail("published Bazecor.AppImage does not match the verified inode")
        os.fsync(app_fd)
        return staged_fd, staged_info
    except BaseException:
        if not published:
            unlink_if_same(app_fd, staged_name, staged_info)
        os.close(staged_fd)
        raise


def publish_symlink(bin_fd, target):
    if not O_PATH:
        fail("this installer requires Linux O_PATH support")
    name = ".bazecor." + os.urandom(12).hex()
    held_fd = None
    published = False
    try:
        os.symlink(target, name, dir_fd=bin_fd)
        held_fd = os.open(name, O_PATH | O_NOFOLLOW | O_CLOEXEC, dir_fd=bin_fd)
        held_info = os.fstat(held_fd)
        if not stat.S_ISLNK(held_info.st_mode):
            fail("temporary bazecor launcher is not a symbolic link")
        if os.readlink(name, dir_fd=bin_fd) != target:
            fail("temporary bazecor launcher has the wrong target")
        if not same_file(stat_name(bin_fd, name, name), held_info):
            fail("temporary bazecor launcher was replaced")

        os.replace(name, "bazecor", src_dir_fd=bin_fd, dst_dir_fd=bin_fd)
        published = True
        published_info = stat_name(bin_fd, "bazecor", "published bazecor launcher")
        if (
            not same_file(published_info, held_info)
            or os.readlink("bazecor", dir_fd=bin_fd) != target
        ):
            fail("published bazecor launcher does not match the link that was created")
        os.fsync(bin_fd)
        return held_fd, held_info
    except BaseException:
        if held_fd is not None:
            if not published:
                unlink_if_same(bin_fd, name, os.fstat(held_fd))
            os.close(held_fd)
        raise


def desktop_quote(argument):
    if any(ord(character) < 32 or ord(character) == 127 for character in argument):
        fail("launcher path contains control characters")
    escaped = argument.replace("\\", "\\\\")
    for character in ('"', "`", "$"):
        escaped = escaped.replace(character, "\\" + character)
    return f'"{escaped}"'


def launcher_contents(app_path):
    executable = desktop_quote(app_path)
    return f"""[Desktop Entry]
Type=Application
Name=Bazecor
GenericName=Keyboard Configurator
Comment=Configure your Dygma keyboard
Exec={executable}
Icon=input-keyboard
Terminal=false
Categories=Utility;
Keywords=dygma;keyboard;keymap;layout;macro;lens;
StartupNotify=true
StartupWMClass=Bazecor
Actions=Hidden;ToggleLens;

[Desktop Action Hidden]
Name=Start in tray (Layer Lens only)
Exec={executable} --hidden

[Desktop Action ToggleLens]
Name=Toggle Layer Lens
Exec={executable} --toggle-lens
""".encode("utf-8")


def publish_launcher(desktop_fd, contents):
    held_fd, name = open_temp(desktop_fd, ".bazecor.desktop.")
    held_info = os.fstat(held_fd)
    published = False
    try:
        write_all(held_fd, contents)
        os.fchmod(held_fd, 0o644)
        os.fsync(held_fd)
        if not same_file(stat_name(desktop_fd, name, name), held_info):
            fail("temporary desktop launcher was replaced while it was being written")

        os.replace(name, "bazecor.desktop", src_dir_fd=desktop_fd, dst_dir_fd=desktop_fd)
        published = True
        if not same_file(
            stat_name(desktop_fd, "bazecor.desktop", "published desktop launcher"),
            held_info,
        ):
            fail("published desktop launcher does not match the file that was written")
        os.fsync(desktop_fd)
        return held_fd, held_info
    except BaseException:
        if not published:
            unlink_if_same(desktop_fd, name, held_info)
        os.close(held_fd)
        raise


def update_desktop_database(desktop_fd):
    command = "/usr/bin/update-desktop-database"
    if not os.access(command, os.X_OK):
        return
    subprocess.run(
        [command, f"/proc/self/fd/{desktop_fd}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(desktop_fd,),
        check=False,
    )


def main(argv):
    if len(argv) > 2:
        fail("usage: install-bazecor.sh [path-to-AppImage]")
    if not all((O_CLOEXEC, O_DIRECTORY, O_NOFOLLOW, O_NONBLOCK, O_PATH)):
        fail("this installer requires Linux descriptor-safety flags")
    source = argv[1] if len(argv) == 2 else None

    home, home_fd = open_home()
    descriptors = []
    published_descriptors = []
    try:
        descriptors, app_fd, bin_fd, desktop_fd = open_destinations(home_fd)
        app_path = os.path.join(home, ".local", "share", "bazecor", "Bazecor.AppImage")
        image_fd, image_info = publish_staged_appimage(app_fd, source)
        published_descriptors.append(image_fd)
        link_fd, link_info = publish_symlink(bin_fd, app_path)
        published_descriptors.append(link_fd)
        launcher_fd, launcher_info = publish_launcher(desktop_fd, launcher_contents(app_path))
        published_descriptors.append(launcher_fd)
        update_desktop_database(desktop_fd)

        # Keep every published object and every parent directory pinned through
        # the final check. This catches a path component or target exchanged
        # after its immediate publication check but before reported success.
        local_fd, share_fd = descriptors[0], descriptors[1]
        verify_directory_binding(home_fd, ".local", local_fd, "~/.local")
        verify_directory_binding(local_fd, "share", share_fd, "~/.local/share")
        verify_directory_binding(share_fd, "bazecor", app_fd, "~/.local/share/bazecor")
        verify_directory_binding(
            share_fd, "applications", desktop_fd, "~/.local/share/applications"
        )
        verify_directory_binding(local_fd, "bin", bin_fd, "~/.local/bin")
        if not same_file(
            stat_name(app_fd, "Bazecor.AppImage", "published Bazecor.AppImage"), image_info
        ):
            fail("Bazecor.AppImage was replaced before installation completed")
        if not same_file(stat_name(bin_fd, "bazecor", "published bazecor launcher"), link_info):
            fail("bazecor launcher was replaced before installation completed")
        if not same_file(
            stat_name(desktop_fd, "bazecor.desktop", "published desktop launcher"),
            launcher_info,
        ):
            fail("desktop launcher was replaced before installation completed")
    finally:
        for fd in reversed(published_descriptors):
            os.close(fd)
        for fd in reversed(descriptors):
            os.close(fd)
        os.close(home_fd)

    bin_path = os.path.join(home, ".local", "bin", "bazecor")
    desktop_path = os.path.join(home, ".local", "share", "applications", "bazecor.desktop")
    print(
        f"""
Done.

  binary    {app_path}
  on PATH   {bin_path}
  launcher  {desktop_path}

Layer Lens needs HID access. Start Bazecor once with the keyboard plugged in
and accept the udev prompt, then replug the keyboard.

To remove:
  rm -rf {os.path.dirname(app_path)} {bin_path} {desktop_path}"""
    )


if __name__ == "__main__":
    try:
        main(sys.argv)
    except InstallError as err:
        print(f"install-bazecor: {err}", file=sys.stderr)
        raise SystemExit(1)
