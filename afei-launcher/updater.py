# SPDX-License-Identifier: GPL-3.0-or-later
"""Fetch verified upstream releases and install them beside the original emulator."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from threading import Event
import time
from urllib.parse import urlsplit
import zipfile

UPSTREAM = "zortos293/Lichii"
FORK = "nodesire7/Lichii"
API = f"https://api.github.com/repos/{UPSTREAM}"
FEED = f"https://raw.githubusercontent.com/{FORK}/afei-feed"
ASSET_NAME = "lichii-windows-x86_64.zip"
SAMPLE_BYTES = 256 * 1024
MIB = 1024 * 1024
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CURL = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe") if os.name == "nt" else "curl"


class UpdateError(Exception):
    pass


class Cancelled(UpdateError):
    pass


@dataclass(frozen=True)
class Asset:
    tag: str
    size: int
    sha256: str

    def __post_init__(self):
        if (not re.fullmatch(r"build-[1-9][0-9]*", self.tag)
                or type(self.size) is not int or not 0 < self.size <= 2 * 1024 * MIB
                or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)):
            raise UpdateError("上游发布信息无效或缺少 SHA-256。")

    @property
    def url(self):
        return f"https://github.com/{UPSTREAM}/releases/download/{self.tag}/{ASSET_NAME}"

    @property
    def version(self):
        return int(self.tag.split("-")[-1])

    @property
    def folder(self):
        return f"{self.tag}-{self.sha256[:12]}"


@dataclass(frozen=True)
class Catalog:
    name: str
    sha256: str

    def __post_init__(self):
        if not re.fullmatch(r"build-[1-9][0-9]*-[0-9a-f]{12}\.qm", self.name) or not re.fullmatch(
            r"[0-9a-f]{64}", self.sha256
        ):
            raise UpdateError("中文词库的地址或校验值无效。")

    @property
    def url(self):
        return f"{FEED}/catalogs/{self.name}"


@dataclass(frozen=True)
class Release:
    asset: Asset
    catalog: Catalog | None = None
    source: str = "上游官方"

    def __post_init__(self):
        if self.catalog and self.catalog.name != f"{self.asset.tag}-{self.catalog.sha256[:12]}.qm":
            raise UpdateError("中文词库与发布版本不匹配。")


@dataclass(frozen=True)
class Node:
    name: str
    url: str


@dataclass(frozen=True)
class Sample:
    node: Node
    rate: float = 0
    latency: float = 0
    error: str = ""


def nodes(asset, custom=""):
    values = [
        Node("GitHub 官方", asset.url),
        Node("GH-Proxy CDN", f"https://gh-proxy.org/{asset.url}"),
        Node("GHFast 代理", f"https://ghfast.top/{asset.url}"),
    ]
    if custom.strip():
        url = custom.strip().replace("{tag}", asset.tag).replace("{asset}", ASSET_NAME).replace("{url}", asset.url)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or "{" in url or "}" in url:
            raise UpdateError("自定义节点必须是 HTTPS 地址，可用 {tag}、{asset}、{url}。")
        values.append(Node("自定义 CDN / 代理", url))
    return values


def curl_args(url, path, timeout, limit):
    return [
        CURL, "-q", "--fail", "--silent", "--show-error", "--location", "--max-redirs", "5",
        "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "6",
        "--max-time", str(timeout), "--max-filesize", str(limit), "--user-agent", "Lichii-AFEI/1.0",
        "--output", str(path), "--url", url,
    ]


def fetch(url, path, *, timeout=15, limit=2 * MIB, extra=()):
    args = curl_args(url, path, timeout, limit)
    result = subprocess.run(args + list(extra), capture_output=True, creationflags=FLAGS)
    if result.returncode:
        raise UpdateError(result.stderr.decode("utf-8", "replace").strip() or "连接失败。")
    return result.stdout


def read_url(url, limit=2 * MIB):
    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "response"
        fetch(url, target, limit=limit)
        data = target.read_bytes()
        if len(data) > limit:
            raise UpdateError("响应超过允许大小。")
        return data


def official_asset(data):
    if data["draft"] or data["prerelease"]:
        raise UpdateError("上游发布尚未成为正式版。")
    tag = data["tag_name"]
    candidate = next((a for a in data["assets"] if a["name"] == ASSET_NAME and a["state"] == "uploaded"), None)
    if not candidate:
        raise UpdateError("上游正式版没有 Windows x64 安装包。")
    digest = candidate.get("digest") or ""
    asset = Asset(tag, candidate["size"], digest.removeprefix("sha256:"))
    if not digest.startswith("sha256:") or asset.url != candidate["browser_download_url"]:
        raise UpdateError("上游安装包的来源或校验值不匹配。")
    return asset


def from_feed(data):
    if data["schema"] != 1 or data["upstream"] != UPSTREAM:
        raise UpdateError("更新清单格式无效。")
    if not re.fullmatch(r"[0-9a-f]{40}", data["release_commit"]):
        raise UpdateError("更新清单缺少发布提交。")
    return Release(Asset(**data["asset"]), Catalog(**data["catalog"]), "作者仓库 · 同步汉化")


def check_release(channel):
    sources = {"fork": FEED + "/manifest.json", "official": API + "/releases/latest"}
    order = (channel, "official" if channel == "fork" else "fork")
    errors = []
    for key in order:
        try:
            data = json.loads(read_url(sources[key]))
            return from_feed(data) if key == "fork" else Release(official_asset(data))
        except (ValueError, KeyError, TypeError, OSError, UpdateError) as exc:
            errors.append(f"{'作者仓库' if key == 'fork' else '上游官方'}：{exc}")
    raise UpdateError("无法检查更新。\n" + "\n".join(errors))


def probe(node):
    try:
        with tempfile.TemporaryDirectory() as temp:
            sample = Path(temp) / "sample"
            info = fetch(
                node.url, sample, timeout=10, limit=SAMPLE_BYTES,
                extra=("--range", f"0-{SAMPLE_BYTES - 1}", "--write-out",
                       "%{http_code} %{speed_download} %{time_starttransfer}"),
            ).decode("ascii").split()
            with sample.open("rb") as file:
                magic = file.read(4)
            if info[0] != "206" or sample.stat().st_size != SAMPLE_BYTES or magic != b"PK\x03\x04":
                raise UpdateError("节点未返回可用的 ZIP 分段。")
            return Sample(node, float(info[1]), float(info[2]))
    except (UpdateError, OSError, ValueError, IndexError) as exc:
        return Sample(node, error=str(exc))


def measure(candidates, report=lambda *_: None):
    with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
        jobs = [pool.submit(probe, node) for node in candidates]
        results = []
        for job in as_completed(jobs):
            sample = job.result()
            results.append(sample)
            report("sample", sample)
    return sorted(results, key=lambda item: (bool(item.error), -item.rate, item.latency))


def sha256_file(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def runtime_catalog(runtime, release):
    return runtime / f"afei-{release.catalog.name}"


def install_catalog(runtime, release):
    if not release.catalog:
        return
    catalog_path = runtime_catalog(runtime, release)
    if catalog_path.is_file() and sha256_file(catalog_path) == release.catalog.sha256:
        return
    with tempfile.TemporaryDirectory(dir=runtime) as directory:
        part = Path(directory) / "catalog.qm"
        fetch(release.catalog.url, part, limit=4 * MIB)
        if sha256_file(part) != release.catalog.sha256:
            raise UpdateError("中文词库 SHA-256 不匹配。请重新检查更新后再试。")
        os.replace(part, catalog_path)


def download(node, asset, path, report, cancel):
    command = curl_args(node.url, path, 1200, asset.size)
    command += ["--speed-limit", "16384", "--speed-time", "25"]
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors, creationflags=FLAGS) as process:
            try:
                while process.poll() is None:
                    if cancel.wait(0.25):
                        raise Cancelled("已取消下载。")
                    received = path.stat().st_size if path.exists() else 0
                    report("progress", (node.name, min(received, asset.size), asset.size))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            if process.returncode:
                errors.seek(0)
                raise UpdateError(errors.read(4096).decode("utf-8", "replace").strip() or "下载中断。")
    if path.stat().st_size != asset.size or sha256_file(path) != asset.sha256:
        raise UpdateError("安装包大小或 SHA-256 不匹配。")


def extract_runtime(archive, stage, cancel):
    """Accept regular Windows paths only. Never extract portable user data."""
    with zipfile.ZipFile(archive) as zipped:
        entries = zipped.infolist()
        if len(entries) > 10_000 or sum(entry.file_size for entry in entries) > 3 * 1024 * MIB:
            raise UpdateError("安装包解压大小超出限制。")
        seen = set()
        for entry in entries:
            if cancel.is_set():
                raise Cancelled("已取消安装。")
            name = entry.filename.rstrip("/")
            path = PurePosixPath(name)
            if (not name or name != path.as_posix() or any(
                part in (".", "..") or part.endswith((".", " "))
                or any(ord(char) < 32 or char in r'<>:"\|?*' for char in part)
                or part.split(".")[0].upper() in {
                    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                    *(f"{prefix}{index}" for prefix in ("COM", "LPT") for index in "123456789¹²³"),
                }
                for part in path.parts
            ) or path.is_absolute() or path.parts[0].casefold() == "user"
                    or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or entry.external_attr & 0x400):
                raise UpdateError(f"安装包含有不支持的路径：{name}")
            key = name.casefold()
            if key in seen:
                raise UpdateError(f"安装包存在重复路径：{name}")
            seen.add(key)
            dest = stage.joinpath(*path.parts)
            if entry.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(entry) as src, dest.open("xb") as file:
                    shutil.copyfileobj(src, file)
        if not (stage / "lichii.exe").is_file():
            raise UpdateError("安装包中没有 lichii.exe。")


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as temp:
        staged = Path(temp.name)
        json.dump(data, temp, ensure_ascii=False, indent=2)
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def install(release, candidates, root, report=lambda *_: None, cancel=None):
    cancel = cancel or Event()
    asset = release.asset
    root.mkdir(parents=True, exist_ok=True)
    target = root / asset.folder
    if target.is_dir() and (target / "lichii.exe").is_file():
        marker = target / "afei-build.json"
        if release.catalog or not marker.exists():
            report("status", "正在同步此版本的中文词库…")
            install_catalog(target, release)
            if cancel.is_set():
                raise Cancelled("已取消安装。")
            atomic_json(marker, asdict(release))
        return target
    if shutil.disk_usage(root).free < asset.size + 600 * MIB:
        raise UpdateError("磁盘可用空间不足，请先腾出至少 700 MB。")
    report("status", "正在并行测速，自动选择最快节点…")
    results = measure(candidates, report)
    with tempfile.TemporaryDirectory(dir=root, prefix=".install-") as temp:
        base = Path(temp)
        part = base / "download.zip"
        errors = []
        for sample in results:
            if cancel.is_set():
                raise Cancelled("已取消下载。")
            node = sample.node
            report("status", f"正在从 {node.name} 下载…")
            part.unlink(missing_ok=True)
            try:
                download(node, asset, part, report, cancel)
                break
            except Cancelled:
                raise
            except (UpdateError, OSError) as exc:
                errors.append(f"{node.name}：{exc}")
                report("download_error", Sample(node, error=str(exc)))
        else:
            raise UpdateError("所有下载节点均失败。\n" + "\n".join(errors))
        report("status", "校验通过，正在安装到独立版本目录…")
        stage = base / "runtime"
        stage.mkdir()
        extract_runtime(part, stage, cancel)
        install_catalog(stage, release)
        if cancel.is_set():
            raise Cancelled("已取消安装。")
        atomic_json(stage / "afei-build.json", asdict(release))
        stage.rename(target)
    return target
