# 源代码与第三方组件

启动器采用 GPL-3.0-or-later。启动器、打包脚本和定制词库源文件位于 [nodesire7/Lichii](https://github.com/nodesire7/Lichii/tree/afei-launcher-v1.0.3/afei-launcher)。

- Lichii 图标来自 [上游 Lichii](https://github.com/zortos293/Lichii) 的 `dist/lichii.ico`，原始图稿作者：YOB（yobslab）。
- 附带的 Fritchii 简体中文词库基于 [Fritchii v5.4](https://github.com/Frizti/Fritchii/tree/8ce6c1e0d49b7022d2fa87e60109c7c04f1b5a80) 的 `dist/languages/zh_CN.ts`。自定义译文在 `overrides.fritchii.zh_CN.json`。使用 `align_catalog.py --source <v5.4 源码目录> --legacy-output afei-launcher/afei-fritchii-zh_CN.qm` 重新生成。
- Windows EXE 使用 [Python 3.13.7](https://www.python.org/downloads/release/python-3137/)、[PySide6 / Shiboken 6.11.0](https://code.qt.io/cgit/pyside/pyside-setup.git/tree/?h=v6.11.0) 和 [Qt 6.11.0](https://download.qt.io/official_releases/qt/6.11/6.11.0/single/)；通过 [PyInstaller 6.21.0](https://github.com/pyinstaller/pyinstaller/tree/v6.21.0) 打包。PySide6 和 Qt 组件按其开源许可分发；各项目的源代码包含相应许可和版权声明。
- 启动器调用 Windows 自带的 `curl.exe`。模拟器安装包直接从上游或其下载节点取得；启动器发行包不附带模拟器。
