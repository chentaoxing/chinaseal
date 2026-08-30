# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <[email protected]>
# SPDX-License-Identifier: GPL-3.0-only
r"""字体管理：系统字体文件枚举、自定义字体注册、缺字检测。

设计：以"字体文件"为中心（预览与导出的矢量管线需要文件路径）。
系统字体扫描 C:\Windows\Fonts 与用户字体目录，用 fontTools 读 name/cmap 建索引。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from fontTools.ttLib import TTFont, TTCollection

from .outlines import OutlineError

# 已知商业授权字库族名前缀：默认从字体列表隐藏（用户要求规避商用风险）。
# 开源/免费商用字体（霞鹜、思源、Noto、站酷、全字库等）不在列。
COMMERCIAL_BLOCK_PREFIXES = (
    "方正", "FZ", "汉仪", "HY", "华文", "ST", "华康", "文鼎", "蒙纳",
    "Monotype", "汉鼎", "金梅", "字魂", "造字", "汉呈", "锐字", "字制区",
    "仓耳", "玖月", "粽仿", "阿里妈妈", "佚名",
)


def is_free_family(family: str) -> bool:
    return not family.startswith(COMMERCIAL_BLOCK_PREFIXES)


@dataclass
class FontEntry:
    family: str
    path: str
    font_number: int = 0   # ttc 索引
    is_custom: bool = False


def _font_dirs():
    dirs = []
    windir = os.environ.get("WINDIR", r"C:\Windows")
    dirs.append(os.path.join(windir, "Fonts"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    return [d for d in dirs if os.path.isdir(d)]


def _open(path, font_number=0):
    if path.lower().endswith(".ttc"):
        coll = TTCollection(path, lazy=True)
        if font_number >= len(coll.fonts):
            return None
        return coll.fonts[font_number]
    return TTFont(path, fontNumber=font_number, lazy=True)


def _read_family(path, font_number=0):
    """只读 family 名（廉价，不解析 cmap）；失败返回 None。"""
    try:
        tt = _open(path, font_number)
        if tt is None:
            return None
        name = tt["name"]
        return (name.getDebugName(16) or name.getDebugName(1) or "").strip() or None
    except Exception:
        return None


def _read_cmap(path, font_number=0):
    """按需读 cmap（较贵）。"""
    try:
        tt = _open(path, font_number)
        return tt.getBestCmap() if tt else None
    except Exception:
        return None


class FontManager:
    def __init__(self, extra_dirs=None):
        self.entries: list[FontEntry] = []
        self._cmaps: dict[str, dict] = {}   # path[#n] -> cmap
        self._by_family: dict[str, FontEntry] = {}
        # 捆绑字体 / 用户下载字体优先注册（同名时优先于系统字体）
        for d in extra_dirs or []:
            if os.path.isdir(d):
                self._scan_dir(d, is_custom=False)
        self.scan_system()

    # ---- 扫描 ----

    def scan_system(self):
        keep = self.entries
        self.entries = []
        self._by_family = {}
        for e in keep:
            self.entries.append(e)
            self._by_family[e.family] = e
        for d in _font_dirs():
            self._scan_dir(d)

    def _scan_dir(self, d: str, is_custom=None):
        exts = (".ttf", ".otf", ".ttc")
        try:
            names = os.listdir(d)
        except OSError:
            return
        for fn in sorted(names):
                if not fn.lower().endswith(exts):
                    continue
                path = os.path.join(d, fn)
                if path.lower().endswith(".ttc"):
                    try:
                        nfaces = len(TTCollection(path, lazy=True).fonts)
                    except Exception:
                        continue
                    for i in range(min(nfaces, 8)):  # 中文字体包 ttc 一般 ≤8 面
                        family = _read_family(path, i)
                        if family and family not in self._by_family:
                            e = FontEntry(family, path, i)
                            self.entries.append(e)
                            self._by_family[family] = e
                else:
                    family = _read_family(path)
                    if family and family not in self._by_family:
                        e = FontEntry(family, path)
                        self.entries.append(e)
                        self._by_family[family] = e

    def families(self):
        return sorted(self._by_family.keys())

    def find(self, family: str):
        return self._by_family.get(family)

    # ---- 自定义字体 ----

    def add_custom(self, path: str) -> list:
        """注册拖入的字体文件，返回可用的 family 列表。"""
        added = []
        if path.lower().endswith(".ttc"):
            try:
                nfaces = len(TTCollection(path, lazy=True).fonts)
            except Exception:
                raise OutlineError("无法解析 TTC 字体")
            for i in range(min(nfaces, 8)):
                family = _read_family(path, i)
                if family:
                    e = FontEntry(family, path, i, is_custom=True)
                    self.entries.append(e)
                    self._by_family[family] = e
                    added.append(family)
        else:
            family = _read_family(path)
            if not family:
                raise OutlineError("无法解析字体文件（仅支持 ttf/otf/ttc）")
            e = FontEntry(family, path, 0, is_custom=True)
            self.entries.append(e)
            self._by_family[family] = e
            added.append(family)
        return added

    # ---- 缺字检测 ----

    def _cmap_of(self, entry: FontEntry):
        key = f"{entry.path}#{entry.font_number}"
        cmap = self._cmaps.get(key)
        if cmap is None:
            cmap = _read_cmap(entry.path, entry.font_number) or {}
            self._cmaps[key] = cmap
        return cmap

    def coverage(self, entry: FontEntry, text: str) -> list:
        """返回缺失字符列表。"""
        cmap = self._cmap_of(entry)
        return [ch for ch in text if not ch.isspace() and ord(ch) not in cmap]
