# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <chentaoxing@gmail.com>
# SPDX-License-Identifier: GPL-3.0-only
"""把 GitHub Release 的便携 zip 同步到 AtomGit 同名 Release（发布端 CI 用）。

用法: python mirror_release_zip.py <tag>    例: mirror_release_zip.py v0.4.19
令牌只从环境变量读取：GITHUB_TOKEN / ATOMGIT_TOKEN，绝不写盘、绝不入日志。
幂等：AtomGit 已有同名附件时跳过上传。
所有出站 URL 过 https+白名单+私网校验，重定向逐跳复检。
"""
import http.client
import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

GH_REPO = "chentaoxing/chinaseal"
GH_API = f"https://api.github.com/repos/{GH_REPO}"
AG_API = f"https://api.atomgit.com/api/v5/repos/{GH_REPO}"
UA = "ChinaSeal-mirror"

ALLOWED_HOSTS = {
    "api.github.com", "github.com", "objects.githubusercontent.com",
    "release-assets.githubusercontent.com", "uploads.github.com",
    "api.atomgit.com", "atomgit.com", "file-cdn.gitcode.com",
    "file.gitcode.com",
}
# AtomGit 附件走华为云 OBS 预签名 PUT，域名动态下发，按平台后缀放行
OBS_SUFFIX = ".myhuaweicloud.com"


def check_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    if p.scheme != "https":
        raise ValueError(f"仅允许 https：{url.split('?')[0]}")
    host = p.hostname or ""
    if host not in ALLOWED_HOSTS and not host.endswith(OBS_SUFFIX):
        raise ValueError(f"主机不在白名单：{host}")
    for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            raise ValueError(f"主机解析到受限地址：{host} -> {ip}")
    return url


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirect)


def _req(url, method="GET", data=None, headers=None, timeout=300):
    check_url(url)
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with _OPENER.open(req, timeout=timeout) as r:
        return r.status, r.read()


def _req_retry(url, method="GET", data=None, headers=None, timeout=300, tries=3):
    """下载类请求重试包装：GitHub 直连在部分网络下会中途 reset/IncompleteRead。"""
    last = None
    for n in range(1, tries + 1):
        try:
            return _req(url, method=method, data=data, headers=headers, timeout=timeout)
        except (ConnectionResetError, TimeoutError, OSError,
                http.client.HTTPException) as e:
            last = e
            print(f"  retry {n}/{tries - 1}: {type(e).__name__}: {e}")
    raise last


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else ""
    assert tag.startswith("v"), f"用法: mirror_release_zip.py v<x.y.z>（收到 {tag!r}）"
    ver = tag[1:]
    fname = f"ChinaSeal-{ver}-portable.zip"
    ght = os.environ["GITHUB_TOKEN"]
    agt = os.environ["ATOMGIT_TOKEN"]

    # 1) GitHub Release 资产元数据
    st, data = _req(f"{GH_API}/releases/tags/{tag}",
                    headers={"Authorization": f"Bearer {ght}",
                             "Accept": "application/vnd.github+json",
                             "User-Agent": UA})
    rel = json.loads(data)
    asset = next((a for a in rel.get("assets", []) if a["name"] == fname), None)
    if asset is None:
        print(f"SKIP: GitHub Release {tag} 无 {fname}")
        return

    # 2) AtomGit Release（已存在则忽略冲突码）
    body = json.dumps({"tag_name": tag, "name": f"ChinaSeal {ver}",
                       "body": "国内镜像发行版（自动同步自 GitHub Release）。",
                       "target_commitish": tag, "prerelease": False,
                       "draft": False}).encode()
    try:
        _req(f"{AG_API}/releases?access_token={agt}", method="POST", data=body,
             headers={"Content-Type": "application/json", "User-Agent": UA})
        print("AtomGit release 已创建")
    except urllib.error.HTTPError as e:
        if e.code in (409, 422):
            print("AtomGit release 已存在")
        else:
            raise

    # 3) 幂等：已有同名附件直接跳过（放在下载之前，省 100MB 流量）
    st, data = _req(f"{AG_API}/releases/{tag}?access_token={agt}",
                    headers={"User-Agent": UA})
    if any(a.get("name") == fname for a in json.loads(data).get("assets", [])):
        print("SKIP: AtomGit 已有同名附件")
        return

    # 4) 取得 zip 字节：--local <路径> 直读本地文件（与 GH 资产同源，
    #    并按 size 字段核对，防传错版本）；否则从 GH 资产下载（CI 内网
    #    GitHub→GitHub 带宽充足，带重试）
    blob = None
    if "--local" in sys.argv:
        lp = os.path.abspath(sys.argv[sys.argv.index("--local") + 1])
        assert os.path.isfile(lp), f"本地文件不存在：{lp}"
        with open(lp, "rb") as f:
            blob = f.read()
        if asset.get("size") and len(blob) != int(asset["size"]):
            raise SystemExit(f"本地文件大小与 GH 资产不符：{len(blob)} != {asset['size']}")
        print(f"本地文件直读: {os.path.basename(lp)} {len(blob)/1e6:.1f} MB（与 GH 资产一致）")
    else:
        st, blob = _req_retry(asset["url"],
                              headers={"Authorization": f"Bearer {ght}",
                                       "Accept": "application/octet-stream",
                                       "User-Agent": UA},
                              timeout=1800)
        print(f"GH asset: {fname} {len(blob)/1e6:.1f} MB")

    # 5) 取上传地址（含 OBS 必需头）并 PUT
    st, data = _req(f"{AG_API}/releases/{tag}/upload_url?access_token={agt}"
                    f"&file_name={urllib.parse.quote(fname)}",
                    headers={"User-Agent": UA})
    up = json.loads(data)
    headers = {"User-Agent": UA}
    headers.update(up.get("headers") or {})
    _req(up["url"], method="PUT", data=blob, headers=headers, timeout=3600)
    print(f"PUT OK: {fname} -> AtomGit ({len(blob)/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
