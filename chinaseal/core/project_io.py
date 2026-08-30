# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <chentaoxing@gmail.com>
# SPDX-License-Identifier: GPL-3.0-only
"""工程文件（.chinaseal，JSON）读写。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .model import SealParams, CharTransform

PROJECT_EXT = ".chinaseal"
PROJECT_MAGIC = "chinaseal-project"
PROJECT_VERSION = 1


def _resolve_project_file(raw: str) -> Path:
    """规范化工程文件路径：绝对化、拒空字节与上跳组件、统一扩展名。"""
    if not raw or "\x00" in raw:
        raise ValueError("非法文件路径")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise ValueError("工程文件路径必须为绝对路径")
    if ".." in p.parts:  # resolve 前先拒显式上跳
        raise ValueError("路径不允许包含 ..")
    p = p.resolve(strict=False)
    if ".." in p.parts:
        raise ValueError("路径不允许包含 ..")
    if p.suffix.lower() != PROJECT_EXT:
        p = p.with_suffix(PROJECT_EXT)
    return p


def save_project(params: SealParams, raw_path: str):
    target = _resolve_project_file(raw_path)
    payload = {
        "magic": PROJECT_MAGIC,
        "version": PROJECT_VERSION,
        "params": params.to_dict(),
    }
    tmp = target.with_name(target.stem + ".tmp" + PROJECT_EXT)
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def load_project(raw_path: str) -> SealParams:
    target = _resolve_project_file(raw_path)
    data = json.loads(target.read_text(encoding="utf-8"))
    if data.get("magic") != PROJECT_MAGIC:
        raise ValueError("不是 ChinaSeal 工程文件")
    valid = SealParams.field_names()
    raw = data.get("params", {})
    p = SealParams(**{k: v for k, v in raw.items() if k in valid and k != "char_transforms"})
    p.char_transforms = [CharTransform(**t) for t in raw.get("char_transforms", [])]
    p.clamp()
    return p
