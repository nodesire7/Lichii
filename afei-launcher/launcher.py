# SPDX-License-Identifier: GPL-3.0-or-later
"""Chinese desktop launcher. The upstream executable is run as a separate process."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from threading import Event
import traceback
import zipfile

from PySide6.QtCore import QLockFile, QObject, QRunnable, QSettings, Qt, QThreadPool, QTimer, QTranslator, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHeaderView, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import updater as updates

AUTHOR = "A-FEI-"
FORUM = "https://bbs.gbtgame.me"
TELEGRAM = "https://t.me/GBTGame_Team"
REPOSITORY = f"https://github.com/{updates.FORK}"
ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Lichii-AFEI"
RUNTIMES = ROOT / "runtimes"
CUSTOM_QM = ROOT / "translation" / "zh_CN.qm"
REPO_ICON = Path(__file__).resolve().parent.parent / "dist" / "lichii.ico"
BINARY_ICON = Path(__file__).resolve().with_name("lichii.ico")
GAME_EXTENSIONS = {".nup", ".xci", ".nsp", ".nro", ".nca", ".nsz", ".xcz"}

STYLE = """
QMainWindow, QDialog, QWidget#surface { background: #f3f5f1; color: #1d2d25; }
QLabel { color: #1d2d25; }
QLabel#title { font-size: 27px; font-weight: 700; }
QLabel#section { font-size: 16px; font-weight: 700; }
QLabel#muted { color: #4b6254; }
QLabel#message[state="error"] { color: #923a2c; }
QLabel#message[state="done"] { color: #286041; }
QPushButton, QComboBox, QLineEdit {
    background: #ffffff; color: #1d2d25; border: 1px solid #a6b5ac;
    border-radius: 5px; padding: 7px 12px; min-height: 19px;
}
QPushButton:hover, QComboBox:hover { background: #e9f0e9; border-color: #608172; }
QPushButton:pressed { background: #dbe7df; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus { border: 1px solid #1b6347; }
QPushButton:disabled, QComboBox:disabled { background: #e8ece7; color: #6a776e; border-color: #c3cec5; }
QPushButton#primary { background: #245c43; color: #fff; border-color: #245c43; font-weight: 700; }
QPushButton#primary:hover { background: #2a7151; }
QPushButton#primary:pressed { background: #1d4b37; }
QPushButton#primary:focus { border: 2px solid #80ad95; padding: 6px 11px; }
QPushButton#primary:disabled { background: #e2e9e2; color: #617268; border-color: #becbc1; }
QFrame#rule { color: #cbd6ce; }
QTableWidget { background: #fff; alternate-background-color: #f5f8f4; border: 1px solid #b9c7bd;
    border-radius: 5px; color: #1d2d25; gridline-color: #e2e9e3; }
QHeaderView::section { background: #e8eee8; color: #31483a; padding: 7px 9px; border: 0; }
QTableWidget::item { padding-left: 8px; }
QProgressBar { border: 0; border-radius: 3px; background: #dde7dd; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #377b57; border-radius: 3px; }
"""


@dataclass(frozen=True)
class Build:
    label: str
    directory: Path
    release: updates.Release | None = None


def installed_builds(original, root):
    items = [Build("原有模拟器", original)]
    managed = []
    for marker in root.glob("build-*/afei-build.json"):
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            asset = updates.Asset(**data["asset"])
            catalog = updates.Catalog(**data["catalog"]) if data["catalog"] else None
            release = updates.Release(asset, catalog)
            if marker.parent.name == asset.folder and (marker.parent / "lichii.exe").is_file():
                managed.append(Build(asset.tag + "  ·  上游 Windows x64", marker.parent, release))
        except (ValueError, KeyError, TypeError, OSError, updates.UpdateError):
            continue
    return items + sorted(managed, key=lambda build: build.release.asset.version, reverse=True)


def valid_catalog(path):
    if not path.is_file():
        return False
    return QTranslator().load(path.as_posix(), ":/languages/")


def language_for(build, custom_path):
    if custom_path.is_file():
        if not valid_catalog(custom_path):
            raise updates.UpdateError("自定义中文词库无法加载。请替换该 .qm 文件，或把它移出汉化目录。")
        return custom_path.resolve().as_posix()
    if build.release and build.release.catalog:
        path = updates.runtime_catalog(build.directory, build.release)
        if valid_catalog(path):
            return path.resolve().as_posix()
    return "zh_CN"


def set_ui_language(config, language):
    """Use Qt to encode the two keys, then keep the rest of the user's INI byte-for-byte."""
    config.parent.mkdir(parents=True, exist_ok=True)
    original = config.read_bytes() if config.exists() else b""
    with tempfile.TemporaryDirectory(dir=config.parent, prefix=".afei-") as temporary:
        tmp = Path(temporary)
        encoded = tmp / "language.ini"
        settings = QSettings(str(encoded), QSettings.Format.IniFormat)
        settings.setValue("UI/Paths/language", language)
        settings.setValue("UI/Paths/language/default", False)
        settings.sync()
        if settings.status() != QSettings.Status.NoError:
            raise updates.UpdateError("无法保存模拟器界面语言。")
        replacement = [line for line in encoded.read_bytes().splitlines() if line.startswith(b"Paths\\language")]
        if len(replacement) != 2:
            raise updates.UpdateError("无法生成模拟器界面语言设置。")
        eol = b"\r\n" if b"\r\n" in original else b"\n"
        lines = original.splitlines(keepends=True)
        keys = {line.partition(b"=")[0] for line in replacement}
        section = next((i for i, line in enumerate(lines) if line.strip().removeprefix(b"\xef\xbb\xbf") == b"[UI]"), None)
        block = [line + eol for line in replacement]
        if section is None:
            separator = (eol if original and not original.endswith(b"\n") else b"")
            updated = original + separator + b"[UI]" + eol + b"".join(block)
        else:
            end = next((i for i in range(section + 1, len(lines)) if lines[i].lstrip().startswith(b"[")), len(lines))
            header = lines[section] if lines[section].endswith(b"\n") else lines[section] + eol
            body = [line for line in lines[section + 1:end] if line.partition(b"=")[0].strip() not in keys]
            updated = b"".join(lines[:section] + [header] + block + body + lines[end:])
        if updated == original:
            return
        if original:
            backup = config.with_name("qt-config.afei-original.ini")
            if not backup.exists():
                shutil.copy2(config, backup)
        staged = tmp / "qt-config.ini"
        staged.write_bytes(updated)
        os.replace(staged, config)


def profile_for(build, original):
    original_user = original / "user"
    target = build.directory / "user"
    if build.release and original_user.is_dir() and not target.exists():
        # Portable mode uses a directory beside lichii.exe. A junction keeps one copy of the saves.
        script = "New-Item -ItemType Junction -Path $env:LICHII_AFEI_LINK -Target $env:LICHII_AFEI_PROFILE -ErrorAction Stop | Out-Null"
        env = dict(os.environ, LICHII_AFEI_LINK=str(target), LICHII_AFEI_PROFILE=str(original_user.resolve()))
        ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run([str(ps), "-NoProfile", "-NonInteractive", "-Command", script],
                                env=env, capture_output=True, creationflags=updates.FLAGS)
        if result.returncode:
            raise updates.UpdateError("无法连接原有 user 目录。请确认安装盘支持 NTFS 目录链接。")
    if target.is_dir():
        return target
    return Path(os.environ.get("APPDATA", Path.home())) / "lichii"


def emulator_running():
    tasklist = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/tasklist.exe"
    result = subprocess.run([str(tasklist), "/FI", "IMAGENAME eq lichii.exe", "/FO", "CSV", "/NH"],
                            check=True, capture_output=True, creationflags=updates.FLAGS, timeout=8)
    return b'"lichii.exe"' in result.stdout.lower()


def find_game(folder):
    library = folder / "_game"
    if not library.is_dir():
        return ""
    matches = [item for item in library.iterdir() if item.is_file() and item.suffix.lower() in GAME_EXTENSIONS]
    return str(matches[0]) if len(matches) == 1 else ""


class WorkerSignals(QObject):
    event = Signal(str, object)
    done = Signal(object, str)


class Worker(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.done.emit(self.fn(self.signals.event.emit), "")
        except (updates.UpdateError, OSError, ValueError, zipfile.BadZipFile) as exc:
            self.signals.done.emit(None, str(exc))
        except Exception:
            with (ROOT / "launcher-error.log").open("a", encoding="utf-8") as log:
                traceback.print_exc(file=log)
            self.signals.done.emit(None, "启动器遇到意外错误。详情见 launcher-error.log。")


def label(text, role=""):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setObjectName(role)
    return result


def rule():
    separator = QFrame()
    separator.setObjectName("rule")
    separator.setFrameShape(QFrame.Shape.HLine)
    return separator


def button(text, slot, role=""):
    result = QPushButton(text)
    result.setObjectName(role)
    result.clicked.connect(slot)
    return result


class Preferences(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("启动器设置")
        self.setMinimumWidth(580)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        form = QFormLayout()
        self.path = QLineEdit(str(parent.original))
        self.path.setAccessibleName("游戏目录")
        row = QHBoxLayout()
        row.addWidget(self.path)
        row.addWidget(button("浏览…", self.pick_folder))
        form.addRow("游戏目录", row)
        self.game = QLineEdit(parent.game)
        self.game.setAccessibleName("游戏文件")
        row = QHBoxLayout()
        row.addWidget(self.game)
        row.addWidget(button("浏览…", self.pick_game))
        form.addRow("游戏文件", row)
        self.channel = QComboBox()
        self.channel.addItem("作者仓库（同步汉化）", "fork")
        self.channel.addItem("上游官方（内置简体中文）", "official")
        self.channel.setCurrentIndex(self.channel.findData(parent.channel))
        form.addRow("更新渠道", self.channel)
        self.custom = QLineEdit(parent.custom)
        self.custom.setAccessibleName("自定义下载节点")
        self.custom.setPlaceholderText("https://mirror.example/{tag}/{asset}")
        form.addRow("自定义节点", self.custom)
        layout.addLayout(form)
        help_text = label("节点地址可用 {tag}、{asset} 或 {url}。游戏文件留空时只打开模拟器。", "muted")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        layout.addWidget(button("打开自定义汉化目录", parent.open_translation))
        self.message = label("", "message")
        self.message.setProperty("state", "error")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        actions = QDialogButtonBox()
        actions.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        actions.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        actions.accepted.connect(self.save)
        actions.rejected.connect(self.reject)
        layout.addWidget(actions)

    def pick_folder(self):
        selected = QFileDialog.getExistingDirectory(self, "选择含 lichii.exe 的目录", self.path.text())
        if selected:
            self.path.setText(selected)
            self.game.setText(find_game(Path(selected)))

    def pick_game(self):
        selected, _ = QFileDialog.getOpenFileName(
            self, "选择本地游戏文件", self.game.text() or self.path.text(),
            "游戏文件 (*.nup *.xci *.nsp *.nro *.nca *.nsz *.xcz);;所有文件 (*)",
        )
        if selected:
            self.game.setText(selected)

    def save(self):
        folder = Path(self.path.text().strip())
        if not folder.is_absolute() or not (folder / "lichii.exe").is_file():
            self.message.setText("请选择包含 lichii.exe 的绝对路径。")
            return
        game = self.game.text().strip()
        if game and (not Path(game).is_absolute() or not Path(game).is_file()):
            self.message.setText("找不到游戏文件。请选择现有文件，或留空。")
            return
        try:
            updates.nodes(updates.Asset("build-1", 1, "0" * 64), self.custom.text())
        except (ValueError, updates.UpdateError) as exc:
            self.message.setText(str(exc))
            return
        self.accept()


class Launcher(QMainWindow):
    def __init__(self, start_folder):
        super().__init__()
        self.settings = QSettings(str(ROOT / "launcher.ini"), QSettings.Format.IniFormat)
        self.original = Path(self.settings.value("original", str(start_folder)))
        self.game = self.settings.value("game", find_game(self.original))
        self.channel = self.settings.value("channel", "fork")
        if self.channel not in ("fork", "official"):
            self.channel = "fork"
        self.custom = self.settings.value("custom", "")
        self.release = None
        self.busy = False
        self.cancel = Event()
        self.worker = None
        self.setWindowTitle("Lichii 中文启动器")
        self.resize(750, 555)
        self.setMinimumSize(680, 520)
        root = QWidget(objectName="surface")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 24, 28, 19)
        layout.setSpacing(13)

        header = QHBoxLayout()
        header.addWidget(label("Lichii 中文启动器", "title"), 1)
        self.settings_button = button("设置", self.preferences)
        header.addWidget(self.settings_button)
        layout.addLayout(header)

        self.game_name = label("", "muted")
        self.game_name.setWordWrap(True)
        layout.addWidget(self.game_name)
        launch = QHBoxLayout()
        self.versions = QComboBox()
        self.versions.setAccessibleName("启动版本")
        self.versions.currentIndexChanged.connect(self.save_version)
        launch.addWidget(self.versions, 1)
        self.play = button("启动模拟器", self.start_game, "primary")
        launch.addWidget(self.play)
        layout.addLayout(launch)
        layout.addWidget(rule())

        release_row = QHBoxLayout()
        release_row.addWidget(label("上游更新", "section"))
        self.version_label = label("尚未检查", "muted")
        self.version_label.setWordWrap(True)
        release_row.addWidget(self.version_label, 1, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(release_row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["下载节点", "抽样速度", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 125)
        self.table.setColumnWidth(2, 182)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setToolTip("双击节点复制下载地址")
        self.table.cellDoubleClicked.connect(self.copy_node)
        self.table.setMinimumHeight(126)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.check = button("检查更新", lambda: self.run_job("check"))
        self.speed = button("测速", lambda: self.run_job("speed"))
        self.download = button("自动选最快节点下载", lambda: self.run_job("install"))
        self.stop = button("取消", self.cancel_job)
        self.stop.hide()
        actions.addWidget(self.check)
        actions.addWidget(self.speed)
        actions.addStretch(1)
        actions.addWidget(self.stop)
        actions.addWidget(self.download)
        layout.addLayout(actions)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setAccessibleName("下载进度")
        layout.addWidget(self.bar)
        self.message = label("下载的版本会单独保留，可在上方切换。", "message")
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message.setMaximumHeight(68)
        layout.addWidget(self.message)
        layout.addWidget(rule())

        footer = QLabel(
            f'启动器与汉化：<b>{AUTHOR}</b>　·　<a style="color:#245c43" href="{FORUM}">GBTGAME 论坛</a>　'
            f'·　<a style="color:#245c43" href="{TELEGRAM}">Telegram</a>　'
            f'·　<a style="color:#245c43" href="{REPOSITORY}">GitHub</a>'
        )
        footer.setOpenExternalLinks(True)
        footer.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse |
                                       Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        layout.addWidget(footer)
        self.refresh_builds()
        self.reset_nodes()

    def set_message(self, text, state=""):
        self.message.setText(text)
        self.message.setToolTip(text)
        self.message.setProperty("state", state)
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)

    def refresh_builds(self):
        selected = self.settings.value("version", "")
        self.versions.blockSignals(True)
        self.versions.clear()
        self.versions.setPlaceholderText("选择启动版本")
        for build in installed_builds(self.original, RUNTIMES):
            self.versions.addItem(build.label, build)
            if build.directory.name == selected:
                self.versions.setCurrentIndex(self.versions.count() - 1)
        if self.versions.currentIndex() < 0:
            self.versions.setCurrentIndex(0)
        self.versions.blockSignals(False)
        self.save_version()
        self.game_name.setText(Path(self.game).name if self.game else "未指定游戏文件，将打开模拟器主界面")
        self.game_name.setToolTip(self.game or str(self.original))
        self.play.setText("启动游戏" if self.game else "启动模拟器")

    def save_version(self):
        build = self.versions.currentData()
        if build:
            self.settings.setValue("version", build.directory.name if build.release else "")

    def open_translation(self):
        CUSTOM_QM.parent.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(CUSTOM_QM.parent)))

    def preferences(self):
        dialog = Preferences(self)
        if not dialog.exec():
            return
        self.original = Path(dialog.path.text().strip())
        self.game = dialog.game.text().strip()
        self.channel = dialog.channel.currentData()
        self.custom = dialog.custom.text().strip()
        for key, value in (("original", str(self.original)), ("game", self.game),
                           ("channel", self.channel), ("custom", self.custom)):
            self.settings.setValue(key, value)
        self.settings.sync()
        self.release = None
        self.version_label.setText("尚未检查")
        self.refresh_builds()
        self.reset_nodes()
        self.set_message("设置已保存。")

    def reset_nodes(self):
        example = self.release.asset if self.release else updates.Asset("build-1", 1, "0" * 64)
        candidates = updates.nodes(example, self.custom)
        self.table.setRowCount(len(candidates))
        for row, node in enumerate(candidates):
            item = QTableWidgetItem(node.name)
            if self.release:
                item.setData(Qt.ItemDataRole.UserRole, node.url)
                item.setToolTip(node.url)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem("—"))
            self.table.setItem(row, 2, QTableWidgetItem("待测速" if self.release else "待检查"))

    def copy_node(self, row, _column):
        url = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if url:
            QApplication.clipboard().setText(url)
            self.set_message("已复制下载地址。", "done")

    def show_sample(self, sample, override=""):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).text() != sample.node.name:
                continue
            if not override:
                speed = f"{sample.rate / updates.MIB:.2f} MB/s" if not sample.error else "—"
                self.table.item(row, 1).setText(speed)
            status = override or ("抽样不可用" if sample.error else f"首包 {sample.latency * 1000:.0f} ms")
            self.table.item(row, 2).setText(status)
            self.table.item(row, 2).setToolTip(sample.error or status)
            break

    def start_game(self):
        build = self.versions.currentData()
        try:
            if not build or not (build.directory / "lichii.exe").is_file():
                raise updates.UpdateError("找不到 lichii.exe。请在设置中选择原有游戏目录，或下载上游版本。")
            if self.game and not Path(self.game).is_file():
                raise updates.UpdateError("找不到游戏文件。请在设置中重新选择。")
            if emulator_running():
                raise updates.UpdateError("Lichii 已在运行。关闭现有窗口后再切换或启动。")
            language = language_for(build, CUSTOM_QM)
            profile = profile_for(build, self.original)
            set_ui_language(profile / "config" / "qt-config.ini", language)
            command = [str(build.directory / "lichii.exe")]
            if self.game:
                command += ["-g", self.game]
            env = dict(os.environ)
            # A frozen launcher can otherwise give the child its bundled Qt library location.
            env.pop("QT_PLUGIN_PATH", None)
            env.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
            if getattr(sys, "frozen", False):
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            subprocess.Popen(command, cwd=build.directory, env=env, close_fds=True)
            self.set_message(f"已启动：{build.label}", "done")
        except (OSError, subprocess.SubprocessError, updates.UpdateError) as exc:
            self.set_message(str(exc), "error")

    def run_job(self, operation):
        self.busy = True
        self.cancel.clear()
        for control in (self.play, self.versions, self.settings_button, self.check, self.speed, self.download):
            control.setEnabled(False)
        self.stop.setVisible(operation == "install")
        self.stop.setEnabled(True)
        self.set_message("正在检查上游正式版…")
        self.bar.setRange(0, 0)
        channel, custom = self.channel, self.custom
        known = None if operation == "check" else self.release

        def run(report):
            release = known or updates.check_release(channel)
            report("release", release)
            candidates = updates.nodes(release.asset, custom)
            if operation == "speed":
                report("status", "正在并行抽样测速…")
                return updates.measure(candidates, report)
            if operation == "install":
                return updates.install(release, candidates, RUNTIMES, report, self.cancel)
            return release

        self.worker = Worker(run)
        self.worker.signals.event.connect(self.event_received)
        self.worker.signals.done.connect(self.job_finished)
        QThreadPool.globalInstance().start(self.worker)

    def cancel_job(self):
        self.cancel.set()
        self.stop.setEnabled(False)
        self.set_message("正在取消…")

    def event_received(self, kind, value):
        if kind == "release":
            self.release = value
            self.version_label.setText(f"{value.asset.tag}  ·  {value.source}")
            self.reset_nodes()
        elif kind == "sample":
            self.show_sample(value)
        elif kind == "download_error":
            self.show_sample(value, "下载失败，尝试下一节点")
        elif kind == "progress" and not self.cancel.is_set():
            node, received, total = value
            self.bar.setRange(0, 100)
            self.bar.setValue(received * 100 // total)
            self.set_message(f"{node}  ·  {received / updates.MIB:.1f} / {total / updates.MIB:.1f} MB")
        elif kind == "status" and not self.cancel.is_set():
            self.set_message(value)

    def job_finished(self, value, error):
        self.worker = None
        self.busy = False
        self.stop.hide()
        for control in (self.play, self.versions, self.settings_button, self.check, self.speed, self.download):
            control.setEnabled(True)
        self.bar.setRange(0, 100)
        self.bar.setValue(100 if isinstance(value, Path) and not error else 0)
        if error:
            self.set_message(error, "" if self.cancel.is_set() else "error")
        elif isinstance(value, Path):
            self.refresh_builds()
            self.set_message(f"{self.release.asset.tag} 已安装。可在上方选择该版本启动。", "done")
        elif isinstance(value, list):
            best = next((sample for sample in value if not sample.error), None)
            if best:
                self.show_sample(best, "最快")
                self.set_message(f"最快节点：{best.node.name}。下载时会重新测速。", "done")
            else:
                self.set_message("没有节点支持本次分段测速。下载时仍可逐个尝试。", "error")
        elif isinstance(value, updates.Release):
            installed = any(build.release and build.release.asset == value.asset
                            for build in installed_builds(self.original, RUNTIMES))
            status = "已安装，可点击下载补齐词库。" if installed else "可下载新版本。"
            self.set_message(f"{value.asset.tag} · {status}", "done")

    def closeEvent(self, event):
        if self.busy:
            self.set_message("任务正在运行。可点击取消下载，或等待任务结束。")
            event.ignore()
        else:
            super().closeEvent(event)


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setApplicationName("Lichii 中文启动器")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(STYLE)
    icon = BINARY_ICON if BINARY_ICON.is_file() else REPO_ICON
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    lock = QLockFile(str(ROOT / "launcher.lock"))
    if not lock.tryLock(0):
        return 0
    start = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    window = Launcher(start)
    window.show()
    # Check metadata on startup; installation always starts with the download button.
    QTimer.singleShot(350, lambda: window.run_job("check") if not window.busy and not window.release else None)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
