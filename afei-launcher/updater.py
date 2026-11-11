# SPDX-License-Identifier: GPL-3.0-or-later
"""Fetch verified upstream releases and install them beside the original emulator."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from threading import Event
from urllib.parse import urlsplit
import zipfile

from network_errors import describe_curl_error, describe_io_error

UPSTREAM = "zortos293/Lichii"
FORK = "nodesire7/Lichii"
API = f"https://api.github.com/repos/{UPSTREAM}"
FEED = f"https://raw.githubusercontent.com/{FORK}/afei-feed"
FEED_ARCHIVE = f"https://codeload.github.com/{FORK}/zip/refs/heads/afei-feed"
CATALOG_CDNS = ("cdn.jsdelivr.net", "fastly.jsdelivr.net")
ASSET_NAME = "lichii-windows-x86_64.zip"
LATEST_ASSET_URL = f"https://github.com/{UPSTREAM}/releases/latest/download/{ASSET_NAME}"
DOWNLOAD_ROUTES = (
    ("GitHub 官方", ""),
    ("GH-Proxy CDN", "https://gh-proxy.org/"),
    ("GH-Proxy 备用", "https://gh-proxy.com/"),
    ("GHProxy.net", "https://ghproxy.net/"),
    ("GHFile 代理", "https://ghfile.geekertao.top/"),
    ("IMCiel 代理", "https://ghproxy.imciel.com/"),
    ("GHFast 代理", "https://ghfast.top/"),
)
SAMPLE_BYTES = 256 * 1024
MIB = 1024 * 1024
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CURL = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe") if os.name == "nt" else "curl"


class UpdateError(Exception):
    def __init__(self, message, details=""):
        super().__init__(message)
        self.details = details


class Cancelled(UpdateError):
    pass


class TransferError(UpdateError):
    def __init__(self, code, stderr, http_status=0):
        super().__init__(describe_curl_error(code, stderr, http_status))
        self.code = code
        self.http_status = http_status


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
class ReleaseCheck:
    release: Release
    cached: bool = False
    details: str = ""


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
    upstream_url = asset.url if asset else LATEST_ASSET_URL
    values = [Node(name, prefix + upstream_url) for name, prefix in DOWNLOAD_ROUTES]
    seen = {node.url for node in values}
    for number, line in enumerate(custom.splitlines(), 1):
        template = line.strip()
        if not template or (not asset and "{tag}" in template):
            continue
        url = template.replace("{asset}", ASSET_NAME).replace("{url}", upstream_url)
        if asset:
            url = url.replace("{tag}", asset.tag)
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme == "https" and parsed.hostname and parsed.username is None
                     and parsed.password is None and not parsed.fragment and not re.search(r"[\s{}]", url))
        except ValueError:
            valid = False
        if not valid:
            raise UpdateError(f"第 {number} 行自定义线路无效。请输入 HTTPS 地址，可用 {{tag}}、{{asset}}、{{url}}。")
        if url not in seen:
            values.append(Node(f"自定义线路 {number}", url))
            seen.add(url)
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
    api_request = urlsplit(url).hostname == "api.github.com"
    headers = path.with_suffix(".headers")
    if api_request:
        args += ["--dump-header", str(headers)]
    if "--write-out" not in extra:
        args += ["--write-out", "%{http_code}"]
    try:
        result = subprocess.run(args + list(extra), capture_output=True, creationflags=FLAGS)
    except FileNotFoundError as exc:
        raise UpdateError("找不到 Windows 的 curl 下载组件。请检查系统文件后重试。") from exc
    if result.returncode:
        match = re.match(rb"^([0-9]{3})(?:\s|$)", result.stdout)
        status = int(match[1]) if match else 0
        if api_request and status in (403, 429) and headers.is_file():
            blocks = re.split(rb"\r?\n\r?\n", headers.read_bytes().strip())
            if re.search(rb"(?im)^x-ratelimit-remaining:\s*0\s*$", blocks[-1]):
                raise UpdateError("GitHub 更新接口的请求次数已用完。可稍后重试或切换网络。")
        raise TransferError(result.returncode, result.stderr, status)
    return result.stdout


def read_url(url, limit=2 * MIB, timeout=10):
    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "response"
        fetch(url, target, limit=limit, timeout=timeout)
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


def feed_archive_file(filename, limit):
    data = read_url(FEED_ARCHIVE, limit=24 * MIB, timeout=15)
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entry = archive.getinfo(f"Lichii-afei-feed/{filename}")
            if entry.is_dir() or entry.file_size > limit:
                raise UpdateError("作者归档中的文件大小异常。")
            return archive.read(entry)
    except (KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        raise UpdateError("作者归档中没有有效的更新数据。") from exc


def saved_release(data):
    return Release(Asset(**data["asset"]), Catalog(**data["catalog"]) if data.get("catalog") else None)


def best_known_release(releases):
    return max((release for release in releases if release),
               key=lambda release: (release.asset.version, bool(release.catalog)), default=None)


def check_release(channel, known=None):
    def author():
        return from_feed(json.loads(read_url(FEED + "/manifest.json")))

    def official():
        return Release(official_asset(json.loads(read_url(API + "/releases/latest"))))

    errors = []

    def read(name, operation):
        try:
            return operation()
        except (UpdateError, OSError, ValueError, KeyError, TypeError) as exc:
            reason = (str(exc) if isinstance(exc, UpdateError) else describe_io_error(exc)
                      if isinstance(exc, OSError) else "收到的版本清单格式无效。")
            errors.append(f"{name}：{reason}")
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending_official = pool.submit(read, "上游官方接口", official)
        from_author = read("作者仓库直连", author)
        from_official = pending_official.result()
    if not from_author and (channel == "fork" or not from_official):
        from_author = read("作者仓库归档", lambda: replace(
            from_feed(json.loads(feed_archive_file("manifest.json", 2 * MIB))),
            source="作者仓库归档 · 同步汉化"))
    candidates = (from_author, from_official) if channel == "fork" else (from_official, from_author)
    current = max((release for release in candidates if release),
                  key=lambda release: release.asset.version, default=None)
    if current and (not known or current.asset.version >= known.asset.version):
        return ReleaseCheck(current, details="\n".join(errors))
    if known:
        if current:
            errors.append(f"线上清单只提供 {current.asset.tag}，本机已有较新的 {known.asset.tag} 记录。")
        return ReleaseCheck(replace(known, source="本机发布记录"), True, "\n".join(errors))
    raise UpdateError("暂时无法确认最新版本。可先测速，或换网络后重新检查更新。", "\n".join(errors))


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
            if info[0] == "200" and magic == b"PK\x03\x04":
                raise UpdateError("不支持分段测速。下载时仍可尝试此线路。")
            if info[0] != "206" or sample.stat().st_size != SAMPLE_BYTES or magic != b"PK\x03\x04":
                raise UpdateError("没有返回有效的安装包分段。可换一条线路后重试。")
            return Sample(node, float(info[1]), float(info[2]))
    except TransferError as exc:
        if exc.code in (33, 63) and exc.http_status in (0, 200):
            return Sample(node, error="不支持分段测速。下载时仍可尝试此线路。")
        return Sample(node, error=str(exc))
    except UpdateError as exc:
        return Sample(node, error=str(exc))
    except OSError as exc:
        return Sample(node, error=describe_io_error(exc))
    except (ValueError, IndexError):
        return Sample(node, error="测速响应不完整。可换一条线路后重试。")


def measure(candidates, report=lambda *_: None):
    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=min(len(candidates), 8)) as pool:
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
        entry = f"catalogs/{release.catalog.name}"
        routes = [("作者仓库直连", release.catalog.url), *(
            (host, f"https://{host}/gh/{FORK}@afei-feed/{entry}") for host in CATALOG_CDNS
        ), ("作者仓库归档", None)]
        errors = []
        for name, url in routes:
            try:
                part.unlink(missing_ok=True)
                if url:
                    fetch(url, part, timeout=10, limit=4 * MIB)
                else:
                    part.write_bytes(feed_archive_file(entry, 4 * MIB))
                if sha256_file(part) != release.catalog.sha256:
                    raise UpdateError("收到的词库与发布校验值不符。")
                os.replace(part, catalog_path)
                return
            except UpdateError as exc:
                errors.append(f"{name}：{exc}")
        raise UpdateError("配套中文词库下载失败。可稍后重试补齐词库。", "\n".join(errors))


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
                raise TransferError(process.returncode, errors.read(4096))
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
    if cancel.is_set():
        raise Cancelled("已取消下载。")
    asset = release.asset
    root.mkdir(parents=True, exist_ok=True)
    target = root / asset.folder
    if target.is_dir() and (target / "lichii.exe").is_file():
        marker = target / "afei-build.json"
        if release.catalog or not marker.exists():
            report("status", "正在同步此版本的中文词库…")
            try:
                install_catalog(target, release)
            except UpdateError as exc:
                if cancel.is_set():
                    raise Cancelled("已取消安装。") from exc
                if not marker.exists():
                    atomic_json(marker, asdict(replace(release, catalog=None)))
                report("catalog_warning", exc)
                return target
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
                report("downloaded", node)
                break
            except Cancelled:
                raise
            except (UpdateError, OSError) as exc:
                message = str(exc) if isinstance(exc, UpdateError) else describe_io_error(exc)
                errors.append(f"{node.name}：{message}")
                report("download_error", Sample(node, error=message))
        else:
            raise UpdateError(f"已尝试 {len(results)} 条线路，均未能完成下载。请换网络或在设置中添加线路后重试。",
                              "\n".join(errors))
        report("status", "校验通过，正在安装到独立版本目录…")
        stage = base / "runtime"
        stage.mkdir()
        extract_runtime(part, stage, cancel)
        try:
            install_catalog(stage, release)
        except UpdateError as exc:
            release = replace(release, catalog=None)
            report("catalog_warning", exc)
        if cancel.is_set():
            raise Cancelled("已取消安装。")
        atomic_json(stage / "afei-build.json", asdict(release))
        stage.rename(target)
    return target
