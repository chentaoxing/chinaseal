# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <[email protected]>
# SPDX-License-Identifier: GPL-3.0-only
"""印稿数据模型（SealParams / CharTransform）。"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, asdict

READINGS = ("modern", "traditional", "huiwen")
SHAPES = ("rect", "circle")
YINYANGS = ("yang", "yin")


@dataclass
class CharTransform:
    dx: float = 0.0   # mm，相对格中心
    dy: float = 0.0
    scale: float = 1.0
    rotation: float = 0.0  # 度

    def is_identity(self):
        return abs(self.dx) < 1e-6 and abs(self.dy) < 1e-6 \
            and abs(self.scale - 1.0) < 1e-6 and abs(self.rotation) < 1e-6


@dataclass
class SealParams:
    text: str = "中国篆刻"
    font_family: str = ""
    font_path: str = ""        # 自定义字体文件路径；空 = 系统字体按 family 名渲染
    font_number: int = 0       # .ttc 中的字面索引
    # 印面
    shape: str = "rect"
    width_mm: float = 30.0
    height_mm: float = 30.0
    diameter_mm: float = 30.0
    # 排版
    single_line: bool = False          # 单行长条模式（不限字数）
    single_line_vertical: bool = True  # 单行模式：True 竖条 / False 横条
    circle_center: bool = True         # 圆章 ≤2 字时居中模式（False=强制环形）
    reading: str = "traditional"
    rows: int = 0                      # 0 = 自动
    cols: int = 0                      # 0 = 自动
    char_scale: float = 0.72           # 字占格比例
    # 装饰
    border_enabled: bool = True
    border_mm: float = 0.6
    field_grid: bool = True            # 田字格（仅预览）
    # 阴阳 / 镜像
    yinyang: str = "yang"              # yang=阳刻朱文 / yin=阴刻白文
    mirror_h: bool = False
    mirror_v: bool = False
    # 输出
    dpi: int = 600
    # 单字微调（与 text 等长；缺省为单位变换）
    char_transforms: list = field(default_factory=list)

    def transforms_for(self, n: int) -> list:
        """补齐/截断到 n 个。"""
        out = list(self.char_transforms[:n])
        while len(out) < n:
            out.append(CharTransform())
        return out

    def clamp(self):
        self.shape = self.shape if self.shape in SHAPES else "rect"
        self.reading = self.reading if self.reading in READINGS else "traditional"
        self.yinyang = self.yinyang if self.yinyang in YINYANGS else "yang"
        self.dpi = min(1200, max(300, int(self.dpi)))
        self.char_scale = min(1.0, max(0.5, float(self.char_scale)))
        self.border_mm = min(5.0, max(0.0, float(self.border_mm)))
        for k in ("width_mm", "height_mm", "diameter_mm"):
            setattr(self, k, min(200.0, max(5.0, float(getattr(self, k)))))

    def to_dict(self):
        d = asdict(self)
        d["char_transforms"] = [asdict(t) for t in self.char_transforms]
        return d

    @staticmethod
    def field_names():
        return {f.name for f in fields(SealParams)}
