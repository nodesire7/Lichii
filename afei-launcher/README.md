# Lichii 中文启动器

启动器与汉化：**A-FEI-** · [GBTGAME 论坛](https://bbs.gbtgame.me) · [Telegram](https://t.me/GBTGame_Team)

在 [启动器发布页](https://github.com/nodesire7/Lichii/releases/tag/afei-launcher-v1.0.0) 下载 Windows x64 启动器，把 EXE 放在原有的 `lichii.exe` 旁边，双击运行。不需要安装 Python。若 `_game` 里只有一个游戏文件，启动器会自动选中它。也可在「设置」中选择游戏目录和游戏文件；不选文件则打开模拟器主界面。

默认启动原有版本。点击「检查更新」查看正式版；「测速」会并行读取同一个发布包的前 256 KiB，显示速度与首包延迟。「自动选最快节点下载」会重新测速，按速度依次尝试，校验上游发布包的大小和 SHA-256 后安装。双击节点可复制地址。默认节点为 GitHub 官方、GH-Proxy CDN 和 GHFast 代理；自有 CDN 或代理可在设置中填入 HTTPS 地址，支持 `{tag}`、`{asset}` 和 `{url}`。没有通过测速的节点只在已通过的节点不可用时尝试完整下载，且同样必须通过校验。

下载版本放在 `%LOCALAPPDATA%\Lichii-AFEI\runtimes`，安装后从下拉菜单选择。原有版本和旧的下载版本都可以切换回来。启动时，如原游戏目录已有便携式 `user` 文件夹，下载版本会通过目录链接使用同一份存档和设置。首次更改界面语言时，启动器会备份原来的 `qt-config.ini`。目录链接只在启动下载版本时建立，解压不会接触游戏、存档或汉化目录。升级前请先备份重要存档，尤其是在不同的模拟器分支间切换时。

## 汉化与更新渠道

- **作者仓库（同步汉化）**：从 [afei-feed](https://github.com/nodesire7/Lichii/tree/afei-feed) 取发布清单和对应的外置 Qt 词库。自定义译文在 [overrides.zh_CN.json](overrides.zh_CN.json)，按 Qt 上下文、原文和消歧注释与上游匹配。上游改掉的文本会列入清单的 `needs_review`，原有译文仍留在 JSON。未维护的条目使用上游已有简体中文；上游未译的新文本可能仍显示英文。
- **上游官方（内置简体中文）**：直接读取 [Lichii 正式发布](https://github.com/zortos293/Lichii/releases)，不依赖作者词库。所选渠道不可达时会尝试另一渠道，界面会显示实际来源。
- **本地自定义汉化**：在设置中打开自定义汉化目录，将已编译的 Qt 词库命名为 `zh_CN.qm` 放入。启动时优先加载该文件；任何更新都不会改写它。删去或移出该文件后可恢复同步词库或内置简体中文。启动原有版本时默认用其内置简体中文，若原有构建没有嵌入对应词库，可用这个入口加载外置文件。

Fork 的 `nx2` 分支镜像上游源码；`afei-zh` 保存启动器和自定义译文；`afei-feed` 发布词库及更新清单。GitHub Actions 每 5 分钟尝试同步，实际调度可能延迟；在 Actions 可手动运行。词库以正式发行标签指向的提交为准，编译过程不改上游源码。启动器不分发游戏、固件、密钥或本机数据。

## 从源码运行

在 Windows 上安装 Python 3.11+，在仓库根目录运行：

```powershell
python -m pip install PySide6
python afei-launcher/launcher.py
```

使用 [build.ps1](build.ps1) 打包（需要 PyInstaller）。执行 `python -m unittest discover -s afei-launcher -p "test_*.py"` 运行测试。打包依赖和源代码链接见 [THIRD-PARTY.md](THIRD-PARTY.md)。
