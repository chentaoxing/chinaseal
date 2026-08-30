# -*- coding: utf-8 -*-
"""资源路径（开发态 / PyInstaller 冻结态双兼容）。"""
from __future__ import annotations

import os
import sys


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundled_fonts_dir() -> str:
    return os.path.join(base_dir(), "fonts")


def logo_path() -> str:
    return os.path.join(base_dir(), "ui", "logo.png")
