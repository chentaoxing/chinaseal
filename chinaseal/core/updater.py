# -*- coding: utf-8 -*-
"""软件自更新模块：检测、下载、生成 helper.bat 完成覆盖+重启。"""
from __future__ import annotations

import json
import os
import socket
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
    "release-assets.githubusercontent.com",
    "raw.githubusercontent.com", "uploads.github.com", "codeload.github.com",
    "atomgit.com", "file-cdn.gitcode.com",
}

ALLOWED_EXTS = (".bat", ".cmd", ".zip", ".tmp", ".log")


_DNS_CACHE = {}
_DNS_TTL = 300.0
_DNS_TIMEOUT = 5.0


def _resolve_host(host: str):
    """带缓存的 DNS 解析；daemon 线程 + join 硬超时（不能用 Executor 上下文）。"""
    import threading
    import time as _time
    now = _time.monotonic()
    cached = _DNS_CACHE.get(host)
    if cached and now - cached[0] < _DNS_TTL:
        if cached[1] is None:
            raise OSError(f"域名解析失败（缓存）：{host}")
        return cached[1]
    out = {}

    def _do():
        try:
            out["infos"] = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        except Exception as e:
            out["err"] = e

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(_DNS_TIMEOUT)
    if t.is_alive():
        _DNS_CACHE[host] = (now, None)
        raise OSError(f"域名解析超时（>{_DNS_TIMEOUT}s）：{host}")
    if "err" in out:
        _DNS_CACHE[host] = (now, None)
        raise OSError(f"域名解析失败：{host}（{out['err']}）") from out["err"]
    _DNS_CACHE[host] = (now, out["infos"])
    return out["infos"]


def validate_url(url: str) -> str:
    import ipaddress, socket
    p = urllib.parse.urlparse(url)
    if p.scheme != "https":
        raise ValueError(f"非 https：{url}")
    host = p.hostname
    if not host or host not in ALLOWED_HOSTS:
        raise ValueError(f"主机不在白名单：{host}")
    for info in _resolve_host(host):
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
    """全 bounded HTTP：urlopen（含内部 getaddrinfo）在 daemon 线程限时完成。"""
    import threading as _threading
    validate_url(url)
    h = {"User-Agent": f"ChinaSeal/{chinaseal.__version__}",
         "Accept": "application/vnd.github+json"}
    req = urllib.request.Request(url, headers=h)
    out = {}

    def _do():
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out["body"] = resp.read()
        except Exception as e:
            out["err"] = e

    t = _threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout + 5)
    if t.is_alive():
        raise OSError(f"请求超时（>{timeout + 5}s，含域名解析）：{url}")
    if "err" in out:
        raise out["err"]
    return out["body"]


def get_latest_release(repo: str = REPO):
    """返回 (version_tag, [asset])，找不到版本返回 (None, [])。"""
    try:
        data = json.loads(_request(f"{GITHUB_API}/{repo}/releases/latest"))
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


def _ua() -> dict:
    return {"User-Agent": f"ChinaSeal/{chinaseal.__version__}"}


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """每次重定向都重新过协议/白名单/解析 IP 校验，防跳转绕过与 DNS rebinding。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirectHandler)


def _validated_open(url: str, headers: dict = None, timeout: int = 60):
    """一切对外 HTTP 的唯一出口：先校验再请求，重定向逐跳再校验。"""
    validate_url(url)
    req = urllib.request.Request(url, headers={**_ua(), **(headers or {})})
    return _OPENER.open(req, timeout=timeout)


def _open_download(path: str, mode: str):
    """下载文件唯一落盘通道：函数内 safe_path 规范化禁 ..，且限定暂存目录内。"""
    p = Path(safe_path(path))
    root = Path(safe_path(tempfile.gettempdir(), is_dir=True))
    try:
        p.relative_to(root)
    except ValueError:
        raise ValueError(f"仅允许写入系统临时目录树：{p}")
    return open(str(p), mode)


def _sidecar(dest_path: str, tag: str) -> str:
    """dest 同目录旁路临时文件（如 .part0）：base 先过 safe_path，再验同目录。"""
    base = Path(safe_path(dest_path))
    p = base.parent / (base.name + tag)
    if ".." in p.parts or p.parent != base.parent:
        raise ValueError(f"旁路文件越出目标目录：{p}")
    return str(p.resolve())


def _cleanup_sidecars(dest_path: str) -> None:
    """清掉 dest 的全部旁路临时文件（各轮 .part*/.single*），被占用则跳过。"""
    base = Path(safe_path(dest_path))
    for pat in (base.name + ".part*", base.name + ".single*"):
        for p in base.parent.glob(pat):
            try:
                p.unlink()
            except OSError:
                pass


def _probe_total(url: str) -> tuple:
    """Range 0-0 探测文件大小与断点支持。返回 (total 或 0, 是否支持 Range)。"""
    with _validated_open(url, {"Range": "bytes=0-0"}, timeout=30) as r:
        cr = r.headers.get("Content-Range") or ""
        r.read(1)
        if r.status == 206 and "/" in cr:
            try:
                return int(cr.rsplit("/", 1)[1]), True
            except ValueError:
                pass
        cl = r.headers.get("Content-Length") or "0"
        try:
            return int(cl), False
        except ValueError:
            return 0, False


class _SpeedWatch:
    """下载看门狗（三态）：
    - "stall"：window 秒窗口内零进度（真停滞，始终判定失败）；
    - "slow" ：窗口内增量不足 min_bytes（默认 15s < 15MB 即 <1MB/s）；
    - None   ：正常。
    最后一个候选源只对 stall 判死，slow 允许磨完（聊胜于无）。"""

    def __init__(self, window: float = 15.0, min_bytes: int = 15 * 1024 * 1024):
        self.window, self.min_bytes = window, min_bytes
        self.hist = []

    def tick(self, done: int):
        import time as _t
        now = _t.monotonic()
        self.hist.append((now, done))
        # 以次新点为参照弹出过期基准：保证基准点年龄可超过 window，
        # 否则 pop 条件（<=window）与判满条件（>=window）冲突，永远判不满
        while len(self.hist) > 2 and now - self.hist[1][0] > self.window:
            self.hist.pop(0)
        base = self.hist[0]
        if now - base[0] < self.window:
            return None  # 窗口未满，尚不判定
        delta = done - base[1]
        if delta <= 0:
            return "stall"
        if delta < self.min_bytes:
            return "slow"
        return None


def _download_single(url, dest_path, progress, stop, stall_seconds, last=False,
                     rnd=0):
    """整文件流式下载（无 Range 支持时的通道），带低速切源检测。

    写入独立旁路文件再原子改名：换源后上一轮的僵死线程可能仍握着
    旧 dest 句柄（阻塞中的 socket read 最长 60s 才退出），不能直接写 dest。
    """
    import threading as _th
    counter = {"done": 0}
    box = {}
    watch = _SpeedWatch(stall_seconds)
    tmp = _sidecar(dest_path, f".single.r{rnd}.tmp")

    def _do():
        try:
            with _validated_open(url, timeout=60) as r, \
                    _open_download(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                while not stop["flag"]:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    counter["done"] += len(chunk)
                    if progress and total:
                        progress(counter["done"], total)
            box["ok"] = True
        except Exception as e:
            box["err"] = e

    t = _th.Thread(target=_do, daemon=True)
    t.start()
    while t.is_alive():
        t.join(2)
        verdict = watch.tick(counter["done"])
        if verdict == "stall" or (verdict == "slow" and not last):
            stop["flag"] = True
            raise OSError("下载停滞（15s 零进度）" if verdict == "stall"
                          else "下载过慢（15s 窗口不足 15MB）")
    if box.get("err"):
        raise box["err"]
    os.replace(tmp, dest_path)


def _download_parallel(url, dest_path, total, progress, stop, stall_seconds,
                       workers, last=False, rnd=0):
    """Range 分块并行下载：workers 路同时拉，写各自 .part 后顺序合并。

    旁路文件带轮次号：换源后上一轮僵死线程（最长 60s 才从阻塞 read 退出）
    仍握着旧文件句柄，新轮次用不同文件名避免 WinError 32。
    """
    import threading as _th
    import time as _t
    segs = [(i * total // workers, (i + 1) * total // workers - 1)
            for i in range(workers)]
    counter = {"done": 0}
    lock = _th.Lock()
    errs = []

    def worker(i, lo, hi):
        part = _sidecar(dest_path, f".part{i}.r{rnd}.tmp")
        try:
            with _validated_open(url, {"Range": f"bytes={lo}-{hi}"}, timeout=60) as r, \
                    _open_download(part, "wb") as f:
                if r.status != 206:
                    raise OSError(f"服务器未按 Range 响应（HTTP {r.status}）")
                while not stop["flag"]:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    with lock:
                        counter["done"] += len(chunk)
                        done = counter["done"]
                    if progress:
                        progress(done, total)
            got = os.path.getsize(part)
            if got != hi - lo + 1:
                raise OSError(f"分块 {i} 不完整：{got}/{hi - lo + 1}")
        except Exception as e:
            with lock:
                errs.append(f"分块 {i}: {e}")
            stop["flag"] = True

    ts = [_th.Thread(target=worker, args=(i, lo, hi), daemon=True)
          for i, (lo, hi) in enumerate(segs)]
    for t in ts:
        t.start()
    watch = _SpeedWatch(stall_seconds)
    while any(t.is_alive() for t in ts):
        _t.sleep(2)
        with lock:
            now = counter["done"]
        verdict = watch.tick(now)
        if verdict == "stall" or (verdict == "slow" and not last):
            stop["flag"] = True
            raise OSError("下载停滞（15s 零进度）" if verdict == "stall"
                          else "下载过慢（15s 窗口不足 15MB）")
    for t in ts:
        t.join(10)
    with lock:
        if errs:
            raise OSError("; ".join(errs[:3]))
    with _open_download(dest_path, "wb") as out:
        for i in range(workers):
            part = _sidecar(dest_path, f".part{i}.r{rnd}.tmp")
            with _open_download(part, "rb") as f:
                while True:
                    blk = f.read(1 << 20)
                    if not blk:
                        break
                    out.write(blk)
            os.remove(part)


def download_update(candidates, dest_path: str, progress=None,
                    stall_seconds: int = 15, workers: int = 4) -> None:
    """更新包下载：候选 URL 依序换源 + 多路并行分块。

    candidates：https 直链列表（如 AtomGit → GitHub）。某源异常，或
    stall_seconds 秒窗口内进度不足 5MB（龟速/停滞），即换下一个源；
    全部失败抛 RuntimeError（含各源原因）。
    URL 与每次重定向都在 _validated_open 过协议/白名单/IP 校验；
    所有落盘走 _open_download（safe_path + 临时目录 containment）。
    """
    dest_path = safe_path(dest_path)
    errors = []
    for idx, url in enumerate(candidates):
        stop = {"flag": False}
        last = idx == len(candidates) - 1
        host = urllib.parse.urlparse(url).netloc
        try:
            total, ranged = _probe_total(url)
            if total and ranged and workers > 1:
                _download_parallel(url, dest_path, total, progress,
                                   stop, stall_seconds, workers,
                                   last=last, rnd=idx)
            else:
                _download_single(url, dest_path, progress, stop,
                                 stall_seconds, last=last, rnd=idx)
            if not os.path.exists(dest_path) or os.path.getsize(dest_path) < 1024:
                raise OSError("下载文件缺失或过小")
            return
        except Exception as e:
            stop["flag"] = True
            errors.append(f"{host}: {e}")
            _cleanup_sidecars(dest_path)  # 半成品旁路文件全清，换源重头下
            try:
                os.remove(dest_path)
            except OSError:
                pass
    raise RuntimeError("所有更新源均失败：\n" + "\n".join(errors))


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
    app_dir = safe_path(app_dir, is_dir=True)
    zip_path = safe_path(zip_path)
    out_path = safe_path(out_path)

    body = f'''@echo off
setlocal
set "APP_DIR={app_dir}"
set "ZIP=%~1"
set "STAGED=%TEMP%\\ChinaSeal-Update-Staged"
if exist "%STAGED%" rd /S /Q "%STAGED%"
mkdir "%STAGED%"
echo [update] Extracting...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%STAGED%' -Force"
echo [update] Waiting for ChinaSeal.exe to exit...
powershell -NoProfile -Command "$p = Get-Process -Name ChinaSeal -ErrorAction SilentlyContinue; while ($p) {{ Start-Sleep -Seconds 1; $p = Get-Process -Name ChinaSeal -ErrorAction SilentlyContinue }}; exit 0"
echo [update] Replacing files...
xcopy /Y /E /Q "%STAGED%\\*" "%APP_DIR%\\" >nul
if not exist "%APP_DIR%\\ChinaSeal.exe" (
  echo [update] ERROR: ChinaSeal.exe missing after copy - keeping old files.
  rd /S /Q "%APP_DIR%\\.old" 2>nul
  exit /b 1
)
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
