#!/usr/bin/env python3
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load_module("bazecor_installer", "install-bazecor.py")
set_flag = load_module("bazecor_set_flag", "set-flag.py")
resolver = load_module("bazecor_resolver", "resolve-command.py")


class InstallerTests(unittest.TestCase):
    def private_home(self, root):
        home = Path(root, "home")
        home.mkdir(mode=0o700)
        return home

    def test_local_source_installs_exact_verified_inode(self):
        with tempfile.TemporaryDirectory() as root:
            home = self.private_home(root)
            source = Path(root, "Bazecor.AppImage")
            payload = b"test AppImage bytes"
            source.write_bytes(payload)
            source.chmod(0o600)

            with (
                mock.patch.dict(os.environ, {"HOME": str(home)}),
                mock.patch.object(installer, "BAZECOR_SHA256", hashlib.sha256(payload).hexdigest()),
                mock.patch.object(installer, "MAX_APPIMAGE_BYTES", len(payload)),
                mock.patch.object(installer, "update_desktop_database"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                installer.main(["install-bazecor.sh", str(source)])

            installed = home / ".local/share/bazecor/Bazecor.AppImage"
            launcher = home / ".local/share/applications/bazecor.desktop"
            command = home / ".local/bin/bazecor"
            self.assertEqual(installed.read_bytes(), payload)
            self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o755)
            self.assertTrue(command.is_symlink())
            self.assertEqual(os.readlink(command), str(installed))
            self.assertIn(f'Exec="{installed}"', launcher.read_text())
            self.assertFalse(
                any(path.name.startswith(".Bazecor.AppImage.") for path in installed.parent.iterdir())
            )
            self.assertFalse(
                any(path.name.startswith(".bazecor.desktop.") for path in launcher.parent.iterdir())
            )

    def test_copy_accepts_exact_limit_and_rejects_limit_plus_one(self):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root, "destination")
            destination.mkdir()
            directory_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
            try:
                for size, accepted in ((4, True), (5, False)):
                    source = Path(root, f"source-{size}")
                    source.write_bytes(b"x" * size)
                    source.chmod(0o600)
                    output_fd = os.open(
                        destination / f"output-{size}",
                        os.O_RDWR | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    try:
                        with mock.patch.object(installer, "MAX_APPIMAGE_BYTES", 4):
                            if accepted:
                                installer.copy_source_once(str(source), output_fd)
                            else:
                                with self.assertRaises(installer.InstallError):
                                    installer.copy_source_once(str(source), output_fd)
                    finally:
                        os.close(output_fd)
            finally:
                os.close(directory_fd)

    def test_local_source_rejects_symlink_and_fifo_without_blocking(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root, "target")
            target.write_bytes(b"bytes")
            target.chmod(0o600)
            symlink = Path(root, "symlink")
            symlink.symlink_to(target)
            fifo = Path(root, "fifo")
            os.mkfifo(fifo)
            output = Path(root, "output")
            output_fd = os.open(output, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                for source in (symlink, fifo):
                    with self.assertRaises(installer.InstallError):
                        installer.copy_source_once(str(source), output_fd)
            finally:
                os.close(output_fd)

    def test_destination_parent_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            home = self.private_home(root)
            elsewhere = Path(root, "elsewhere")
            elsewhere.mkdir()
            (home / ".local").symlink_to(elsewhere)
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                _, home_fd = installer.open_home()
                try:
                    with self.assertRaises(installer.InstallError):
                        installer.open_destinations(home_fd)
                finally:
                    os.close(home_fd)

    def test_redirect_escape_is_rejected_before_second_request(self):
        class Response:
            reason = "Found"

            def __init__(self, location):
                self.status = 302
                self.location = location

            def getheader(self, name):
                return self.location if name == "Location" else None

            def close(self):
                pass

        calls = []

        class Connection:
            sock = None

            def __init__(self, host, port, **kwargs):
                calls.append((host, port))

            def request(self, *args, **kwargs):
                pass

            def getresponse(self):
                if len(calls) == 1:
                    return Response("https://release-assets.githubusercontent.com/second-hop")
                return Response("https://evil.example/payload")

            def close(self):
                pass

        with tempfile.TemporaryFile() as output:
            with mock.patch.object(installer.http.client, "HTTPSConnection", Connection):
                with self.assertRaises(installer.InstallError):
                    installer.download_into(output.fileno())
        self.assertEqual(
            calls,
            [("github.com", 443), ("release-assets.githubusercontent.com", 443)],
        )

    def test_network_stream_is_stopped_at_limit_plus_one(self):
        class Response:
            status = 200
            reason = "OK"

            def __init__(self):
                self.payload = bytearray(b"12345")

            def getheader(self, name):
                return None

            def read(self, amount):
                chunk = bytes(self.payload[:amount])
                del self.payload[:amount]
                return chunk

            def close(self):
                pass

        class Connection:
            sock = None

            def __init__(self, *args, **kwargs):
                pass

            def request(self, *args, **kwargs):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass

        with tempfile.TemporaryFile() as output:
            with (
                mock.patch.object(installer, "MAX_APPIMAGE_BYTES", 4),
                mock.patch.object(installer.http.client, "HTTPSConnection", Connection),
            ):
                with self.assertRaises(installer.InstallError):
                    installer.download_into(output.fileno())

    def test_failed_download_removes_its_staging_inode(self):
        with tempfile.TemporaryDirectory() as root:
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)

            def download_bad_bytes(destination_fd):
                installer.write_all(destination_fd, b"bad")

            try:
                with (
                    mock.patch.object(installer, "download_into", download_bad_bytes),
                    mock.patch.object(installer, "BAZECOR_SHA256", "0" * 64),
                ):
                    with self.assertRaises(installer.InstallError):
                        installer.publish_staged_appimage(directory_fd, None)
                self.assertEqual(os.listdir(root), [])
            finally:
                os.close(directory_fd)

    def test_launcher_detects_replacement_after_publication(self):
        with tempfile.TemporaryDirectory() as root:
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            real_replace = installer.os.replace

            def replace_then_attack(source, destination, **kwargs):
                real_replace(source, destination, **kwargs)
                os.unlink(destination, dir_fd=directory_fd)
                attacker_fd = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=directory_fd,
                )
                os.close(attacker_fd)

            try:
                with mock.patch.object(installer.os, "replace", replace_then_attack):
                    with self.assertRaises(installer.InstallError):
                        installer.publish_launcher(directory_fd, b"safe launcher\n")
            finally:
                os.close(directory_fd)


class SetFlagTests(unittest.TestCase):
    def make_config(self, root, mode=0o644):
        config_home = Path(root, "config")
        directory = config_home / "omarchy"
        directory.mkdir(parents=True)
        path = directory / "shell.json"
        path.write_text(
            json.dumps(
                {
                    "plugins": [
                        {
                            "id": set_flag.PLUGIN_ID,
                            "floatOverlay": True,
                        }
                    ]
                }
            )
        )
        path.chmod(mode)
        return config_home, path

    def test_updates_owned_regular_config_and_preserves_mode(self):
        with tempfile.TemporaryDirectory() as root:
            config_home, path = self.make_config(root)
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}):
                set_flag.main(["set-flag.py", "floatOverlay", "false"])
            self.assertFalse(json.loads(path.read_text())["plugins"][0]["floatOverlay"])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_rejects_unsafe_config_mode(self):
        with tempfile.TemporaryDirectory() as root:
            config_home, path = self.make_config(root, mode=0o666)
            before = path.read_bytes()
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}):
                with self.assertRaises(SystemExit):
                    set_flag.main(["set-flag.py", "floatOverlay", "false"])
            self.assertEqual(path.read_bytes(), before)

    def test_rejects_symlink_and_fifo(self):
        with tempfile.TemporaryDirectory() as root:
            config_home, path = self.make_config(root)
            target = path.with_name("target.json")
            path.replace(target)
            path.symlink_to(target)
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}):
                with self.assertRaises(SystemExit):
                    set_flag.main(["set-flag.py", "floatOverlay", "false"])

            path.unlink()
            os.mkfifo(path)
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}):
                with self.assertRaises(SystemExit):
                    set_flag.main(["set-flag.py", "floatOverlay", "false"])

    def test_detects_replacement_after_publication(self):
        with tempfile.TemporaryDirectory() as root:
            config_home, path = self.make_config(root)
            real_replace = set_flag.os.replace

            def replace_then_attack(source, destination, **kwargs):
                real_replace(source, destination, **kwargs)
                directory_fd = kwargs["dst_dir_fd"]
                os.unlink(destination, dir_fd=directory_fd)
                attacker_fd = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=directory_fd,
                )
                os.close(attacker_fd)

            with (
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}),
                mock.patch.object(set_flag.os, "replace", replace_then_attack),
            ):
                with self.assertRaises(SystemExit):
                    set_flag.main(["set-flag.py", "floatOverlay", "false"])


class ResolverTests(unittest.TestCase):
    def test_output_is_bounded_and_shell_syntax_is_rejected(self):
        self.assertEqual(resolver.main(["resolve-command.py", "sh;id"]), 1)
        self.assertEqual(
            resolver.main(["resolve-command.py", "x" * (resolver.MAX_COMMAND_CHARS + 1)]),
            1,
        )

    def test_resolves_one_executable_path(self):
        completed = subprocess.run(
            [sys.executable, ROOT / "resolve-command.py", "python3"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        value = completed.stdout
        self.assertLessEqual(len(value), resolver.MAX_OUTPUT_BYTES + 1)
        self.assertTrue(value.startswith(b"/"))
        self.assertEqual(value.count(b"\n"), 1)


if __name__ == "__main__":
    unittest.main()
