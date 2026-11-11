# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn curl and Windows I/O failures into actionable Chinese messages."""

import errno
import re


HTTP_FAILURES = {
    401: ("服务器要求登录才能访问", "请选择其他线路。"),
    403: ("服务器拒绝了这次访问", "可换一条线路，或稍后重试。"),
    404: ("这条线路找不到所需文件", "可换一条线路，或重新检查更新。"),
    407: ("代理服务器要求登录", "请检查网络代理的账号和密码。"),
    408: ("服务器等待响应超时", "可换一条线路，或稍后重试。"),
    410: ("这条线路上的文件已移除", "请重新检查更新，或选择其他线路。"),
    416: ("这条线路不支持所需的分段下载", "可换线测速，或尝试完整下载。"),
    429: ("服务器限制了访问频率", "请稍后重试，或换一条线路。"),
    451: ("这条线路限制了当前地区的访问", "请选择其他可用线路。"),
}

# Exit codes from https://curl.se/libcurl/c/libcurl-errors.html.
CURL_FAILURES = {
    1: "线路跳转到了不支持的地址。请选择其他线路。",
    3: "下载地址格式有误。请检查自定义线路地址。",
    5: "找不到代理服务器。请检查网络代理地址和 DNS 设置。",
    6: "无法解析服务器地址。请检查网络和 DNS，或换一条线路。",
    7: "无法连接服务器。请检查网络和代理，或换一条线路。",
    8: "服务器返回了无法识别的响应。请选择其他线路。",
    16: "与服务器通信时出现 HTTP 协议错误。可换一条线路后再试。",
    18: "文件只传输了一部分。可换一条线路后重试。",
    22: "服务器拒绝了请求。可换一条线路，或稍后重试。",
    23: "无法写入下载文件。请检查磁盘空间和目录权限。",
    26: "无法读取本地文件。请检查目录权限和文件占用。",
    28: "线路连接或传输超时。可换一条线路，或稍后重试。",
    33: "线路不支持所需的分段下载。可换线测速，或尝试完整下载。",
    35: "无法建立安全连接（TLS 握手失败）。请检查网络代理，或换一条线路。",
    47: "线路连续跳转次数过多。请选择其他线路。",
    52: "服务器未返回数据。可换一条线路后重试。",
    55: "连接在发送请求时中断。可换一条线路后重试。",
    56: "连接在接收数据时中断。可换一条线路后重试。",
    61: "服务器返回了不受支持的数据格式。请选择其他线路。",
    63: "线路返回的数据超过允许大小。请选择其他线路。",
    92: "与服务器通信时出现 HTTP 协议错误。可换一条线路后再试。",
    97: "无法通过代理建立连接。请检查网络代理设置。",
}


def describe_curl_error(returncode: int, stderr: bytes | str = b"", http_status: int = 0) -> str:
    """Use curl diagnostics only to identify the problem, never as UI text."""
    detail = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else stderr
    detail = detail.lower()
    proxy_code = re.search(r"connect tunnel failed, response (\d{3})|http code (\d{3}) from proxy", detail)
    if proxy_code:
        status = int(proxy_code[1] or proxy_code[2])
        if status != 407:
            return f"代理服务器拒绝了连接（HTTP {status}）。请检查网络代理，或换一条线路。"
        http_status = status
    if not 400 <= http_status <= 599:
        status_match = re.search(r"returned error:\s*([45]\d{2})\b", detail)
        http_status = int(status_match[1]) if status_match else 0
    if 400 <= http_status <= 599:
        message, action = HTTP_FAILURES.get(http_status, (
            "服务器暂时不可用" if http_status >= 500 else "服务器拒绝了这次请求",
            "可换一条线路，或稍后重试。",
        ))
        return f"{message}（HTTP {http_status}）。{action}"
    if returncode == 28 and "operation too slow" in detail:
        return "线路持续过慢，传输已停止。可换一条线路后重试。"
    if returncode == 35 and ("crypt_e_revocation_offline" in detail or "revocation server was offline" in detail):
        return "无法查询服务器证书状态。请检查网络代理，或换一条线路。"
    if returncode in {51, 58, 60, 77, 82, 83, 90, 91, 98} or (
        returncode == 35 and ("sec_e_untrusted_root" in detail or "certificate verify failed" in detail)
    ):
        return "服务器证书未通过验证。请检查系统日期和时间、网络代理，或换一条线路。"
    return CURL_FAILURES.get(returncode, f"网络请求未完成（错误代码 {returncode}）。可换一条线路后重试。")


def describe_io_error(error: OSError) -> str:
    """Keep local filesystem and process errors readable on any system locale."""
    windows_code = getattr(error, "winerror", None)
    if error.errno == errno.ENOSPC or windows_code in {39, 112}:
        return "磁盘空间不足。请腾出空间后重试。"
    if windows_code in {32, 33} or error.errno == errno.EBUSY:
        return "文件正被其他程序使用。请关闭占用文件的程序后重试。"
    if isinstance(error, PermissionError) or windows_code in {5, 19}:
        return "没有权限读写所需文件。请检查安装目录的权限。"
    if isinstance(error, FileNotFoundError):
        return "找不到所需文件或程序。请检查安装目录后重试。"
    return "无法读写本地文件。请检查磁盘、目录权限和文件占用后重试。"
