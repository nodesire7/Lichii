# Lichii 中文启动器

启动器与汉化：**A-FEI-** · [GBTGAME 论坛](https://bbs.gbtgame.me) · [Telegram](https://t.me/GBTGame_Team)

在 [启动器发布页](https://github.com/nodesire7/Lichii/releases/latest) 下载 Windows x64 启动器，把 EXE 放在原有的 `lichii.exe` 旁边，双击运行。不需要安装 Python。若 `_game` 里只有一个游戏文件，启动器会自动选中它。也可在「设置」中选择游戏目录和游戏文件；不选文件则打开模拟器主界面。

默认启动原有版本。点击「检查更新」查看正式版；「测速」会并行读取同一个发布包的前 256 KiB，显示速度与首包延迟。「自动选最快节点下载」会重新测速，按速度依次尝试，校验上游发布包的大小和 SHA-256 后安装。双击节点可复制地址。默认节点为 GitHub 官方、GH-Proxy CDN 和 GHFast 代理；自有 CDN 或代理可在设置中填入 HTTPS 地址，支持 `{tag}`、`{asset}` 和 `{url}`。没有通过测速的节点只在已通过的节点不可用时尝试完整下载，且同样必须通过校验。

下载版本放在 `%LOCALAPPDATA%\Lichii-AFEI\runtimes`，安装后从下拉菜单选择。原有版本和旧的下载版本都可以切换回来。启动时，如原游戏目录已有便携式 `user` 文件夹，下载版本会通过目录链接使用同一份存档和设置。首次更改界面语言时，启动器会备份原来的 `qt-config.ini`。目录链接只在启动下载版本时建立，解压不会接触游戏、存档或汉化目录。升级前请先备份重要存档，尤其是在不同的模拟器分支间切换时。

## 汉化与更新渠道

- **作者仓库（同步汉化）**。发布清单提供与上游版本对应的外置 Qt 词库。扫描 `src/yuzu` 和 `src/qt_common` 的 C++ 与 `.ui` 文件，按 Qt 上下文、原文和消歧注释匹配，并补上经源码确认的实际运行上下文。上游改掉的文本会列入清单的 `needs_review`，原有译文仍留在 JSON。[发布清单](https://github.com/nodesire7/Lichii/tree/afei-feed) · [源码补译](source.zh_CN.json) · [既有定制译文](overrides.zh_CN.json)
- **上游官方（内置简体中文）**：直接读取 [Lichii 正式发布](https://github.com/zortos293/Lichii/releases)，不依赖作者词库。所选渠道不可达时会尝试另一渠道，界面会显示实际来源。
- **本地自定义汉化**。在设置中打开自定义汉化目录，将已编译的 Qt 词库命名为 `zh_CN.qm` 放入。启动器优先加载该文件，更新也会保留它。移出该文件后恢复各版本的默认词库。
- **原版 Fritchii v5.4**。启动器附带对应源码的外置词库，并沿用深色和紫色配色。已验证的 Windows 原版可通过模拟器自带的预览入口显示中文更新日志。[v5.4 源码](https://github.com/Frizti/Fritchii/tree/8ce6c1e0d49b7022d2fa87e60109c7c04f1b5a80) · [Fritchii 源码补译](source.fritchii.zh_CN.json) · [中文更新日志](fritchii-v5.4-notes.zh_CN.md)

启动器会给使用已验证 Qt 6.8.3 组件的 Windows 版本加载外置 Qt 插件。插件补译两版软件键盘的功能键和主题下拉框、Fritchii 的主机/掌机状态，以及游戏列表显示的更新版本号。启动器按已安装的 Qt 组件校验是否兼容，其他版本仍可使用对应的外置词库。单独双击 `lichii.exe` 时使用模拟器自己的界面行为。

Fork 的 `nx2` 分支镜像上游源码；`afei-zh` 保存启动器和自定义译文；`afei-feed` 发布词库及更新清单。GitHub Actions 每 5 分钟尝试同步，实际调度可能延迟；在 Actions 可手动运行。词库以正式发行标签指向的提交为准，编译过程不改上游源码。启动器不分发游戏、固件、密钥或本机数据。

## 从源码运行

在 Windows 上安装 Python 3.11+，在仓库根目录运行：

```powershell
python -m pip install PySide6
python afei-launcher/launcher.py
```

安装 PyInstaller、Visual Studio 2022 的 C++ 工具、CMake 和 Qt 6.8.3 MSVC 2022 x64 后，使用 `./afei-launcher/build.ps1 -QtDir C:/Qt/6.8.3/msvc2022_64` 打包。执行 `python -m unittest discover -s afei-launcher -p "test_*.py"` 运行启动器测试。打包依赖和源代码链接见 [THIRD-PARTY.md](THIRD-PARTY.md)。

生成词库时可向 `align_catalog.py` 传入 `--audit-output <报告.json>`，查看源码文本键数、词库覆盖数和未命中的键。品牌、渲染 API、快捷键和占位符可能保留原文，因此词库覆盖数与含中文的条目数分别统计。
