# -*- coding: utf-8 -*-
"""软件自更新模块：检测、下载、生成 helper.bat 完成覆盖+重启。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import chinaseal

REPO = "chentaoxing/chinaseal"
GITHUB_API = "https://api.github.com/repos"
ASSET_NAME_HINT = "portable.zip"

ALLOWED_HOSTS = {
    "api.github.com", "github.com", "objects.githubusercontent.com",
    "raw.githubusercontent.com", "uploads.github.com", "codeload.github.com",
}

ALLOWED_EXTS = (".bat", ".cmd", ".zip", ".tmp", ".log")


def validate_url(url: str) -> str:
    import ipaddress, socket
    p = urllib.parse.urlparse(url)
    if p.scheme != "https":
        raise ValueError(f"非 https：{url}")
    host = p.hostname
    if not host or host not in ALLOWED_HOSTS:
        raise ValueError(f"主机不在白名单：{host}")
    for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            raise ValueError(f"主机解析到受限地址：{host} -> {ip}")
    return url


def safe_path(raw: str, is_dir: bool = False) -> str:
    """规范化路径、禁止 ..；文件限定受控扩展（目录免检）。"""
    p = Path(raw).resolve()
    if any(part == ".." for part in p.parts):
        raise ValueError(f"路径不允许包含 ..：{p}")
    if not is_dir and p.suffix.lower() not in ALLOWED_EXTS:
        raise ValueError(f"文件类型不在白名单：{p}")
    return str(p)


def _request(url, timeout=30):
    validate_url(url)
    h = {"User-Agent": f"ChinaSeal/{chinaseal.__version__}",
         "Accept": "application/vnd.github+json"}
    req = urllib.request.Request(url, headers=h)
    return urllib.request.urlopen(req, timeout=timeout)


def get_latest_release(repo: str = REPO):
    """返回 (version_tag, [asset])，找不到版本返回 (None, [])。"""
    try:
        with _request(f"{GITHUB_API}/{repo}/releases/latest") as r:
            data = json.load(r)
    except Exception:
        return None, []
    tag = str(data.get("tag_name") or "").lstrip("vV")
    assets = data.get("assets", [])
    return tag, assets


def find_portable_asset(assets, hint: str = ASSET_NAME_HINT):
    for a in assets:
        if hint in a.get("name", "").lower():
            return a
    return None


def _write_bytes(path: str, data: bytes) -> None:
    p = Path(safe_path(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def download_to_file(url: str, dest_path: str, progress=None) -> None:
    """下载到指定文件路径（带进度回调 done_bytes, total_bytes）。"""
    validate_url(url)
    dest_path = safe_path(dest_path)
    h = {"User-Agent": f"ChinaSeal/{chinaseal.__version__}"}
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=600) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        buf = bytearray()
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            buf.extend(chunk)
            done += len(chunk)
            if progress and total:
                progress(done, total)
    _write_bytes(dest_path, bytes(buf))


def app_dir() -> str:
    """返回 ChinaSeal.exe 所在目录（PyInstaller onedir 形态）。"""
    return safe_path(os.path.dirname(os.path.abspath(sys.executable)), is_dir=True)


def staging_dir() -> str:
    d = Path(tempfile.gettempdir()) / "ChinaSeal-Update"
    d.mkdir(parents=True, exist_ok=True)
    return safe_path(str(d), is_dir=True)


def write_helper_bat(app_dir: str, zip_path: str, out_path: str) -> str:
    """生成自更新 helper 脚本（5KB）。

    helper.bat 行为：
      1. 解压 zip 到 %TEMP%\\ChinaSeal-Update-Staged
      2. 等当前 ChinaSeal.exe 进程消失
      3. xcopy 把新文件覆盖到 app_dir
      4. 启动新 ChinaSeal.exe
      5. 自删
    """
    app_dir_q = app_dir.replace('"', '""')
    zip_path = safe_path(zip_path)
    zip_q = zip_path.replace('"', '""')
    out_path = safe_path(out_path)

    body = f'''@echo off
setlocal
set APP_DIR="{app_dir_q}"
set ZIP=%~1
set STAGED=%TEMP%\\ChinaSeal-Update-Staged
if exist "%STAGED%" rd /S /Q "%STAGED%"
mkdir "%STAGED%"
echo [update] Extracting...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%STAGED%' -Force"
echo [update] Waiting for ChinaSeal.exe to exit...
powershell -NoProfile -Command "$p = Get-Process -Name ChinaSeal -ErrorAction SilentlyContinue; while ($p) {{ Start-Sleep -Seconds 1; $p = Get-Process -Name ChinaSeal -ErrorAction SilentlyContinue }}; exit 0"
echo [update] Replacing files...
xcopy /Y /E /Q "%STAGED%\\*" "%APP_DIR%\\" >nul
echo [update] Cleaning staging...
rd /S /Q "%STAGED%"
del /F /Q "%ZIP%" 2>nul
echo [update] Relaunching...
start "" "%APP_DIR%\\ChinaSeal.exe"
ping -n 2 127.0.0.1 >nul
del /F /Q "%~f0"
'''
    _write_text(out_path, body)
    return out_path


def _write_text(path: str, text: str) -> None:
    p = Path(safe_path(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\r\n")


def version_newer(latest: str, current: str) -> bool:
    def key(v):
        parts = []
        for seg in str(v).lstrip("vV").replace("-", ".").split("."):
            parts.append(int(seg) if seg.isdigit() else 0)
        return parts
    try:
        return key(latest) > key(current)
    except Exception:
        return False
