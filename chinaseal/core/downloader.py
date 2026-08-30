# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <[email protected]>
# SPDX-License-Identifier: GPL-3.0-only
"""字体下载器：从国内源（Gitee）优先拉取免费开源字体，GitHub 兜底。

软件发布/更新地址：https://github.com/chentaoxing/chinaseal
字体源：Gitee（码云）Release 附件优先，GitHub Release 兜底。
安全约束：仅 https；主机白名单；解析 IP 拒绝私网/环回/链路本地；
重定向目标逐一过同样的校验。
"""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import socket
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import chinaseal
from .outlines import OutlineError

REPO_URL = "https://github.com/chentaoxing/chinaseal"
ATOMGIT_REPO_URL = "https://atomgit.com/chentaoxing/chinaseal"
REPO = "chentaoxing/chinaseal"
FONT_EXTS = (".ttf", ".otf", ".ttc", ".zip")
ALLOWED_HOSTS = {
    "api.atomgit.com", "atomgit.com",
    "gitee.com",
    "api.github.com", "github.com", "objects.githubusercontent.com",
    "raw.githubusercontent.com", "codeload.github.com",
}
ATOMGIT_API = "https://api.atomgit.com/api/v5"


def validate_url(url: str) -> str:
    """校验下载/接口 URL：协议、主机白名单、解析 IP 边界。返回原 URL。"""
    p = urllib.parse.urlparse(url)
    if p.scheme != "https":
        raise ValueError(f"仅允许 https：{url}")
    host = p.hostname
    if not host or host not in ALLOWED_HOSTS:
        raise ValueError(f"主机不在白名单：{host}")
    for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            raise ValueError(f"主机解析到受限地址：{host} -> {ip}")
    return url


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirectHandler)


def _request(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"ChinaSeal/{chinaseal.__version__}",
        "Accept": "application/json",
    })
    return _OPENER.open(req, timeout=timeout)


def user_fonts_dir() -> Path:
    d = Path.home() / "ChinaSeal" / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _atomgit_contents(repo: str, path: str, ref: str = "main") -> bytes:
    """匿名读取仓库文件内容（base64 JSON 解码）。"""
    url = f"{ATOMGIT_API}/repos/{repo}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    with _request(validate_url(url)) as r:
        data = json.load(r)
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"])
    return str(data.get("content", "")).encode("utf-8")


def _github_contents(repo: str, path: str, ref: str = "main") -> bytes:
    """GitHub 源：仓库内容（公开仓库匿名）。小文件用 base64 content，大文件用 download_url。"""
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    with _request(validate_url(url)) as r:
        data = json.load(r)
    if isinstance(data, dict) and data.get("encoding") == "base64" and "content" in data:
        return base64.b64decode(data["content"])
    if isinstance(data, dict) and data.get("download_url"):
        with _request(validate_url(data["download_url"]), timeout=120) as r2:
            return r2.read()
    raise RuntimeError(f"GitHub contents 不可用: {path}")


def _fetch_github_manifest(repo: str) -> tuple:
    """GitHub 源：仓库根 manifest.json 定义版本与字体清单。"""
    manifest = json.loads(_github_contents(repo, "manifest.json"))
    version = str(manifest.get("version", "0"))
    assets = []
    for f in manifest.get("fonts", []):
        assets.append({"name": f.get("name") or os.path.basename(f["file"]),
                       "size": int(f.get("size", 0)),
                       "src": "github", "repo": repo,
                       "path": f["file"],
                       "ref": f.get("ref", "main")})
    return f"v{version}", assets



def _fetch_atomgit_manifest(repo: str) -> tuple:
    """AtomGit 源：fonts_repo/manifest.json 定义版本与字体清单。"""
    manifest = json.loads(_atomgit_contents(repo, "fonts_repo/manifest.json"))
    version = str(manifest.get("version", "0"))
    assets = []
    for f in manifest.get("fonts", []):
        assets.append({"name": f.get("name") or os.path.basename(f["file"]),
                       "size": int(f.get("size", 0)),
                       "src": "atomgit", "repo": repo,
                       "path": f"fonts_repo/{f['file']}",
                       "ref": f.get("ref", "main")})
    return f"v{version}", assets


def _fetch_release(src: str, repo: str) -> tuple:
    if src == "gitee":
        if "/" not in repo:
            raise ValueError("Gitee 仓库路径格式应为：用户名/仓库名")
        url = f"https://gitee.com/api/v5/repos/{repo}/releases/latest"
    else:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
    with _request(validate_url(url)) as r:
        data = json.load(r)
    tag = str(data.get("tag_name") or data.get("name") or "")
    assets = []
    for a in data.get("assets", []):
        name = a.get("name", "")
        if name.lower().endswith(FONT_EXTS):
            assets.append({"name": name,
                           "size": int(a.get("size") or 0),
                           "url": a.get("browser_download_url") or a.get("url") or ""})
    return tag, assets


def list_release_assets(repo: str = REPO, prefer: str = "github") -> tuple:
    """返回 (tag, [asset])。AtomGit 优先、Gitee/GitHub 兜底；全失败抛异常。"""
    tag, assets, _ = list_release_assets_with_source(repo, prefer)
    return tag, assets


def list_release_assets_with_source(repo: str = REPO, prefer: str = "github") -> tuple:
    """同 list_release_assets，但额外返回命中的源。"""
    chain = {"github": _fetch_github_manifest,
             "atomgit": _fetch_atomgit_manifest,
             "gitee": lambda r: _fetch_release("gitee", r),
             "github_release": lambda r: _fetch_release("github", r)}
    rest = [s for s in ("github", "atomgit", "gitee", "github_release") if s != prefer]
    order = [prefer if prefer in chain else "github"] + rest
    errors = []
    for src in order:
        try:
            tag, assets = chain[src](repo)
            if assets:
                return tag, assets, src
            errors.append(f"{src}: 无字体附件")
        except Exception as e:
            errors.append(f"{src}: {e}")
    raise RuntimeError("所有下载源均获取失败。\n" + "\n".join(errors))


def latest_version(repo: str = REPO, prefer: str = "github") -> str:
    tag, _, _ = list_release_assets_with_source(repo, prefer)
    return tag.lstrip("vV")


def download_asset(url, dest_dir: Path, progress=None) -> list:
    """下载并落盘；.zip 自动解出字体文件。返回新字体文件路径列表。

    url 可为 https 直链，或 AtomGit 清单条目（dict: src=atomgit + path）。
    """
    if isinstance(url, dict):
        src = url.get("src")
        if src == "atomgit":
            return _download_atomgit_file(url, dest_dir, progress)
        if src == "github":
            return _download_github_file(url, dest_dir, progress)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = os.path.basename(urllib.parse.urlparse(url).path)
    dest = dest_dir / name
    part = dest.with_suffix(dest.suffix + ".part")
    with _request(validate_url(url), timeout=120) as r, part.open("wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done, total)
    part.replace(dest)

    if dest.suffix.lower() == ".zip":
        out = []
        with zipfile.ZipFile(dest) as z:
            for m in z.namelist():
                if m.lower().endswith((".ttf", ".otf", ".ttc")):
                    target = dest_dir / os.path.basename(m)
                    target.write_bytes(z.read(m))
                    out.append(target)
        dest.unlink()
        return out
    return [dest]


def _download_atomgit_file(asset: dict, dest_dir: Path, progress=None) -> list:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / os.path.basename(asset["path"])
    part = dest.with_suffix(dest.suffix + ".part")
    data = _atomgit_contents(asset["repo"], asset["path"], ref=asset.get("ref", "main"))
    if progress:
        progress(len(data), len(data))
    part.write_bytes(data)
    part.replace(dest)
    return [dest]


def _download_github_file(asset: dict, dest_dir: Path, progress=None) -> list:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / os.path.basename(asset["path"])
    part = dest.with_suffix(dest.suffix + ".part")
    data = _github_contents(asset["repo"], asset["path"], ref=asset.get("ref", "main"))
    if progress:
        progress(len(data), len(data))
    part.write_bytes(data)
    part.replace(dest)
    return [dest]


def register_downloaded(paths, font_mgr) -> list:
    """把下载的字体注册进 FontManager，返回可用 family 列表。"""
    fams = []
    for p in paths:
        try:
            fams.extend(font_mgr.add_custom(str(p)))
        except OutlineError:
            continue
    return fams


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
