# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <[email protected]>
# SPDX-License-Identifier: GPL-3.0-only
"""排版引擎（纯数学，不依赖 Qt）。

输出：每个字的"格子"（中心点 mm 坐标 + 格子尺寸 mm + 字的旋转角度）。
坐标系：mm，原点在印面左上角，y 向下（与屏幕/打印一致）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


class LayoutError(Exception):
    pass


@dataclass
class CharCell:
    index: int            # 字在印文中的序号
    cx: float             # 格中心 x (mm)
    cy: float             # 格中心 y (mm)
    cell_w: float
    cell_h: float
    rotation: float = 0.0  # 逆时针度数（渲染时用）

    @property
    def cell_size(self):
        return min(self.cell_w, self.cell_h)


@dataclass
class SealGeometry:
    shape: str                    # rect | circle
    width_mm: float               # rect 宽 / circle 无用
    height_mm: float              # rect 高
    diameter_mm: float            # circle 直径
    border_mm: float              # 边框线宽（0 = 无框）
    cells: list = field(default_factory=list)      # list[CharCell]
    field_grid: bool = False      # 田字格（仅预览）

    @property
    def content_rect(self):
        """去掉边框后的可用内区 (x, y, w, h)。"""
        inset = self.border_mm if self.border_mm > 0 else 0.0
        return (inset, inset, self.width_mm - 2 * inset, self.height_mm - 2 * inset)

    @property
    def content_circle(self):
        """圆形章可用内圆 (cx, cy, r)。"""
        cx = self.diameter_mm / 2.0
        r = cx - (self.border_mm if self.border_mm > 0 else 0.0)
        return (cx, cx, r)


# ---- 读序：返回"字序号 -> 网格 (row, col)"，row/col 从 0 起，row 向下、col 向右 ----

def order_modern(n: int, rows: int, cols: int):
    """现代横排：左→右，上→下。"""
    return [(i // cols, i % cols) for i in range(n)]


def order_traditional(n: int, rows: int, cols: int):
    """传统右起竖读：右→左逐列，每列上→下（四字印即 右上→右下→左上→左下）。"""
    out = []
    for c in range(cols - 1, -1, -1):
        for r in range(rows):
            if len(out) < n:
                out.append((r, c))
    return out


def order_huiwen(n: int, rows: int, cols: int):
    """回文（环读，右上起逆时针）：右上→左上→左下→右下……外圈向内圈螺旋。"""
    seen, out = set(), []
    r0, r1, c0, c1 = 0, rows - 1, 0, cols - 1
    while r0 <= r1 and c0 <= c1:
        ring = []
        ring += [(r0, c) for c in range(c1, c0 - 1, -1)]          # 上行 右→左
        if r1 != r0:
            ring += [(r, c0) for r in range(r0 + 1, r1 + 1)]      # 左列 上→下
        if r1 != r0:
            ring += [(r1, c) for c in range(c0 + 1, c1 + 1)]      # 下行 左→右
        if r1 - r0 > 1 and c1 != c0:
            ring += [(r, c1) for r in range(r1 - 1, r0, -1)]      # 右列 下→上（回环）
        for rc in ring:
            if rc not in seen:
                seen.add(rc)
                out.append(rc)
        r0, r1, c0, c1 = r0 + 1, r1 - 1, c0 + 1, c1 - 1
    return out[:n]


ORDER_FUNCS = {"modern": order_modern, "traditional": order_traditional, "huiwen": order_huiwen}
ORDER_LABELS = {"modern": "现代横排（左→右）", "traditional": "传统竖读（右起）", "huiwen": "回文环读"}


def auto_grid(n: int):
    """1-9 字默认网格 (rows, cols)。"""
    table = {1: (1, 1), 2: (2, 1), 3: (3, 1), 4: (2, 2), 5: (3, 2),
             6: (3, 2), 7: (3, 3), 8: (3, 3), 9: (3, 3)}
    if n not in table:
        raise LayoutError(f"网格模式仅支持 1-9 字（当前 {n} 字请用单行模式）")
    return table[n]


def layout_rect(n, width_mm, height_mm, border_mm, reading, rows=None, cols=None,
                spacing=1.0, gap_mm=0.0):
    """矩形章布局。spacing: 字占格比例（0.5-1.0）；gap_mm: 字间额外空隙。"""
    if rows is None or cols is None:
        rows, cols = auto_grid(n)
    if rows * cols < n:
        raise LayoutError(f"网格 {rows}×{cols} 放不下 {n} 个字")
    x, y, w, h = (0.0, 0.0, width_mm, height_mm)
    inset = border_mm if border_mm > 0 else 0.0
    x += inset; y += inset; w -= 2 * inset; h -= 2 * inset
    if w <= 0 or h <= 0:
        raise LayoutError("印面内区尺寸为负，请检查边框宽度")
    cw = w / cols
    ch = h / rows
    order = ORDER_FUNCS[reading](n, rows, cols)
    cells = []
    for i, (r, c) in enumerate(order):
        cx = x + (c + 0.5) * cw
        cy = y + (r + 0.5) * ch
        cells.append(CharCell(i, cx, cy,
                              cell_w=(cw - gap_mm) * spacing, cell_h=(ch - gap_mm) * spacing))
    return cells


def layout_circle(n, diameter_mm, border_mm, reading, spacing=1.0,
                  rotation_dir="outward", single_center=False):
    """圆形章布局。

    single_center=True（≤2 字）：字居中，不旋转。
    否则环形排布：chars 沿圆周均匀分布；rotation_dir='outward' 字头朝圆外，
    'inward' 字头朝圆心。reading 语义：modern=顺时针，traditional/huiwen=逆时针（右上起）。
    """
    cx = cy = diameter_mm / 2.0
    inner = diameter_mm / 2.0 - (border_mm if border_mm > 0 else 0.0)
    if inner <= 0:
        raise LayoutError("圆形章直径过小")
    cells = []
    if single_center or n <= 2:
        if n == 1:
            size = inner * 2 * spacing * 0.72
            cells.append(CharCell(0, cx, cy, size, size))
        else:  # 2 字上下（传统）或左右（现代）
            vertical = reading != "modern"
            step = inner * spacing * 0.72
            for i in range(2):
                dy = -step / 2 if i == 0 else step / 2
                dx = step / 2 if not vertical and i == 1 else (-step / 2 if not vertical and i == 0 else 0)
                if vertical:
                    dx = 0
                    cells.append(CharCell(i, cx, cy + dy, step, step))
                else:
                    cells.append(CharCell(i, cx + dx, cy, step, step))
        return cells

    ring_r = inner * 0.62          # 字中心所在半径
    cell = inner * 0.58 * spacing  # 每字占位尺寸
    clockwise = (reading == "modern")
    for i in range(n):
        # 0 号字在正上方（90°），modern 顺时针 / 传统逆时针
        t = i / n
        if clockwise:
            ang = 90.0 - 360.0 * t
        else:
            ang = 90.0 + 360.0 * t
        rad = math.radians(ang)
        px = cx + ring_r * math.cos(rad)
        py = cy - ring_r * math.sin(rad)   # 屏幕 y 向下
        if rotation_dir == "outward":
            rot = 90.0 - ang               # 字头朝圆外
        else:
            rot = 270.0 - ang              # 字头朝圆心
        cells.append(CharCell(i, px, py, cell, cell, rotation=rot))
    return cells


def build_geometry(params) -> SealGeometry:
    """按 SealParams 生成完整几何。"""
    n = len(params.text)
    if n == 0:
        raise LayoutError("印文为空")
    if params.shape == "circle":
        cells = layout_circle(n, params.diameter_mm, params.border_mm, params.reading,
                              spacing=params.char_scale,
                              single_center=params.circle_center and n <= 2)
        geo = SealGeometry("circle", 0, 0, params.diameter_mm,
                           params.border_mm if params.border_enabled else 0.0,
                           cells=cells, field_grid=params.field_grid)
    else:
        rows, cols = (params.rows, params.cols) if (params.rows and params.cols) else (None, None)
        if params.single_line:
            cells = layout_single_line(n, params.width_mm, params.height_mm,
                                       params.border_mm if params.border_enabled else 0.0,
                                       vertical=params.single_line_vertical,
                                       spacing=params.char_scale)
        else:
            cells = layout_rect(n, params.width_mm, params.height_mm,
                                params.border_mm if params.border_enabled else 0.0,
                                params.reading, rows, cols, spacing=params.char_scale)
        geo = SealGeometry("rect", params.width_mm, params.height_mm, 0,
                           params.border_mm if params.border_enabled else 0.0,
                           cells=cells, field_grid=params.field_grid)
    return geo


def layout_single_line(n, width_mm, height_mm, border_mm, vertical=False, spacing=1.0):
    """单行不限字：长条章。horizontal=横条，vertical=竖条。"""
    inset = border_mm if border_mm > 0 else 0.0
    w = width_mm - 2 * inset
    h = height_mm - 2 * inset
    if w <= 0 or h <= 0:
        raise LayoutError("印面内区尺寸为负")
    if vertical:
        step = h / n
        cell = min(w, step) * spacing
        return [CharCell(i, width_mm / 2, inset + (i + 0.5) * step, cell, cell)
                for i in range(n)]
    step = w / n
    cell = min(h, step) * spacing
    return [CharCell(i, inset + (i + 0.5) * step, height_mm / 2, cell, cell)
            for i in range(n)]
