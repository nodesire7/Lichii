# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PySide6.QtCore import QSettings
from align_catalog import align, messages, reuse_translations, source_coverage
from launcher import set_ui_language
import updater


def make_zip(path, files):
    with ZipFile(path, "w") as zipped:
        for name, contents in files.items():
            zipped.writestr(name, contents)


class UpdateTests(unittest.TestCase):
    def test_bad_archive_never_writes_outside_stage(self):
        for invalid in ("../escape.txt", "sub/../../escape.txt", "User/config/test.ini",
                        "C:/file", "sub/file:stream", "sub/COM1.txt", "sub/file."):
            with self.subTest(invalid=invalid), TemporaryDirectory() as tmp:
                base = Path(tmp)
                stage = base / "stage"
                stage.mkdir()
                package = base / "archive.zip"
                make_zip(package, {invalid: b"not permitted"})
                with self.assertRaises(updater.UpdateError):
                    updater.extract_runtime(package, stage, Event())
                self.assertEqual(list(stage.iterdir()), [])

    def test_bad_fast_node_falls_back_to_verified_package(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            package = base / "official.zip"
            make_zip(package, {"lichii.exe": b"stand-in", "plugins/platforms/qt.dll": b"dependency"})
            payload = package.read_bytes()
            release = updater.Release(updater.Asset("build-1", len(payload), hashlib.sha256(payload).hexdigest()))
            fast = updater.Node("fast", "https://mirror.invalid/file")
            official = updater.Node("official", release.asset.url)
            custom = base / "translation/zh_CN.qm"
            custom.parent.mkdir()
            custom.write_bytes(b"local translation")

            def fake_curl(command, **kwargs):
                path = Path(command[command.index("--output") + 1])
                url = command[command.index("--url") + 1]
                path.write_bytes(payload if url == official.url else bytes(len(payload)))
                return unittest.mock.MagicMock(__enter__=lambda proc: proc, poll=lambda: 0, returncode=0)

            with patch.object(updater, "measure", return_value=[updater.Sample(fast, 2), updater.Sample(official, 1)]), \
                 patch.object(updater.subprocess, "Popen", side_effect=fake_curl) as curl:
                installed = updater.install(release, [official, fast], base / "runtimes")
            self.assertEqual(curl.call_count, 2)
            self.assertEqual((installed / "lichii.exe").read_bytes(), b"stand-in")
            self.assertEqual(custom.read_bytes(), b"local translation")

    def test_qt_config_preserves_original_bytes_and_backup(self):
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "qt-config.ini"
            original = b"[UI]\r\nPaths\\language=en\r\nPaths\\language\\default=true\r\nsetting=Keep\r\n\r\n[Other]\r\nfoo=bar\r\n"
            config.write_bytes(original)
            custom = "F:/Test 路径/我的;词库.qm"
            set_ui_language(config, custom)
            qt = QSettings(str(config), QSettings.Format.IniFormat)
            self.assertEqual(qt.value("UI/Paths/language"), custom)
            self.assertEqual(qt.value("UI/Paths/language/default", type=bool), False)
            self.assertIn(b"setting=Keep\r\n\r\n[Other]\r\nfoo=bar\r\n", config.read_bytes())
            self.assertEqual(config.with_name("qt-config.afei-original.ini").read_bytes(), original)

    def test_retired_text_is_reported_and_active_translation_kept(self):
        base = ET.fromstring('<TS><context><name>Main</name><message><source>Play</source>'
                             '<translation>运行</translation></message></context></TS>')
        live = ET.fromstring('<TS><context><name>Main</name><message><source>Play</source>'
                             '</message></context></TS>')
        applied, pending = align(base, live, [
            {"context": "Main", "source": "Play", "translation": "启动"},
            {"context": "Main", "source": "Old", "translation": "旧"},
        ])
        self.assertEqual(applied, [("Main", "Play", "")])
        self.assertEqual(pending, [{"context": "Main", "source": "Old"}])
        self.assertEqual(base.findtext(".//translation"), "启动")

    def test_source_coverage_checks_namespace_and_disambiguation(self):
        catalog = ET.fromstring(
            '<TS><context><name>ErrorManager</name>'
            '<message><source>Open</source><comment>verb</comment><translation>打开</translation></message>'
            '</context></TS>'
        )
        live = ET.fromstring(
            '<TS><context><name>NetworkMessage::ErrorManager</name>'
            '<message><source>Open</source><comment>verb</comment></message>'
            '<message><source>Open</source><comment>adjective</comment></message>'
            '</context></TS>'
        )
        reuse_translations(catalog, live)
        key = ("NetworkMessage::ErrorManager", "Open", "verb")
        self.assertEqual(messages(catalog)[key].findtext("translation"), "打开")
        coverage = source_coverage(catalog, live)
        self.assertEqual(coverage["entries_with_catalog_text"], 1)
        self.assertEqual(coverage["without_catalog_text"][0]["comment"], "adjective")


if __name__ == "__main__":
    unittest.main()
