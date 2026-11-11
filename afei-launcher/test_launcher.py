# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from PySide6.QtCore import QSettings
from align_catalog import add_runtime_contexts, align, messages, reuse_translations, source_coverage
from launcher import set_ui_language
import updater


def make_zip(path, files):
    with ZipFile(path, "w") as zipped:
        for name, contents in files.items():
            zipped.writestr(name, contents)


class UpdateTests(unittest.TestCase):
    def test_raw_tls_failure_and_api_403_recover_via_author_archive(self):
        manifest = {"schema": 1, "upstream": updater.UPSTREAM, "release_commit": "c" * 40,
                    "asset": {"tag": "build-150", "size": 1000, "sha256": "a" * 64},
                    "catalog": {"name": "build-150-bbbbbbbbbbbb.qm", "sha256": "b" * 64}}
        archive = BytesIO()
        make_zip(archive, {"Lichii-afei-feed/manifest.json": json.dumps(manifest)})

        def fake_curl(command, **_kwargs):
            url = command[command.index("--url") + 1]
            path = Path(command[command.index("--output") + 1])
            if "raw.githubusercontent.com" in url:
                return updater.subprocess.CompletedProcess(command, 35, b"000", b"schannel: handshake failed")
            if "api.github.com" in url:
                if "--dump-header" in command:
                    header = Path(command[command.index("--dump-header") + 1])
                    header.write_bytes(b"HTTP/1.1 403 rate limit exceeded\r\nX-RateLimit-Remaining: 0\r\n\r\n")
                return updater.subprocess.CompletedProcess(command, 22, b"403", b"The requested URL returned error: 403")
            self.assertIn("codeload.github.com", url)
            path.write_bytes(archive.getvalue())
            return updater.subprocess.CompletedProcess(command, 0, b"200", b"")

        with patch.object(updater.subprocess, "run", side_effect=fake_curl):
            checked = updater.check_release("fork")
        self.assertEqual(checked.release.asset.tag, "build-150")
        self.assertFalse(checked.cached)
        self.assertIn("归档", checked.release.source)
        self.assertIn("TLS 握手失败", checked.details)
        self.assertIn("次数已用完", checked.details)
        self.assertNotIn("schannel", checked.details)

    def test_offline_check_reuses_known_version_and_fresh_install_can_probe(self):
        release = updater.Release(updater.Asset("build-150", 1000, "a" * 64))
        with patch.object(updater, "read_url", side_effect=updater.TransferError(28, b"timed out")):
            offline = updater.check_release("fork", release)
            with self.assertRaises(updater.UpdateError) as unknown:
                updater.check_release("fork")
        self.assertTrue(offline.cached)
        self.assertEqual(offline.release.asset, release.asset)
        self.assertIn("超时", offline.details)
        self.assertIn("可先测速", str(unknown.exception))
        routes = updater.nodes(None, "https://mirror.example/{tag}/{asset}\nhttps://backup.example/{url}")
        self.assertEqual(len(routes), len(updater.DOWNLOAD_ROUTES) + 1)
        self.assertTrue(all("/releases/latest/download/" in node.url for node in routes))

    def test_catalog_routes_retry_on_digest_mismatch(self):
        contents = b"verified catalog"
        digest = hashlib.sha256(contents).hexdigest()
        release = updater.Release(updater.Asset("build-150", 1000, "a" * 64),
                                  updater.Catalog(f"build-150-{digest[:12]}.qm", digest))

        def fake_fetch(url, path, **_kwargs):
            path.write_bytes(contents if "cdn.jsdelivr.net" in url else b"wrong catalog")

        with TemporaryDirectory() as tmp, patch.object(updater, "fetch", side_effect=fake_fetch) as fetch:
            root = Path(tmp)
            updater.install_catalog(root, release)
            self.assertEqual(updater.runtime_catalog(root, release).read_bytes(), contents)
            self.assertEqual(fetch.call_count, 2)

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

    def test_wrapped_translation_uses_runtime_qt_context(self):
        live = ET.fromstring(
            '<TS><context><name>QtCommon::Content</name>'
            '<message><source>Open</source><comment>verb</comment></message></context>'
            '<context><name>QtCommon::Game</name>'
            '<message><source>Open</source><comment>verb</comment></message></context></TS>'
        )
        catalog = ET.fromstring(
            '<TS><context><name>QtCommon::Content</name>'
            '<message><source>Open</source><comment>verb</comment><translation>打开</translation></message>'
            '</context></TS>'
        )
        with TemporaryDirectory() as tmp:
            source = Path(tmp)
            cpp = source / "src/qt_common/qt_common.cpp"
            cpp.parent.mkdir(parents=True)
            cpp.write_text("const QString tr(const char* str) { return rootObject->tr(str); }", encoding="utf-8")
            add_runtime_contexts(catalog, live, source)
        runtime = live.find("context[name='QWidget']")
        self.assertEqual(len(runtime.findall("message")), 1)
        self.assertEqual(messages(catalog)[("QWidget", "Open", "verb")].findtext("translation"), "打开")


if __name__ == "__main__":
    unittest.main()
