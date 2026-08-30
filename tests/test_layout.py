# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <chentaoxing@gmail.com>
# SPDX-License-Identifier: GPL-3.0-only
import os

import pytest

from chinaseal.core import layout as L
from chinaseal.core.layout import (order_modern, order_traditional, order_huiwen,
                                   auto_grid, layout_rect, layout_circle,
                                   build_geometry, LayoutError)
from chinaseal.core.model import SealParams


def _hypot(a, b):
    return (a * a + b * b) ** 0.5


def test_order_modern_4():
    assert order_modern(4, 2, 2) == [(0, 0), (0, 1), (1, 0), (1, 1)]


def test_order_traditional_4():
    # 右上→右下→左上→左下：(row,col)，col1=右
    assert order_traditional(4, 2, 2) == [(0, 1), (1, 1), (0, 0), (1, 0)]


def test_order_traditional_3():
    # 3 字竖排：右列上中下
    assert order_traditional(3, 3, 1) == [(0, 0), (1, 0), (2, 0)]


def test_order_huiwen_4():
    # 回文逆时针环读：右上→左上→左下→右下
    assert order_huiwen(4, 2, 2) == [(0, 1), (0, 0), (1, 0), (1, 1)]


def test_order_huiwen_9_center_last():
    order = order_huiwen(9, 3, 3)
    assert order[-1] == (1, 1)  # 中心最后
    assert order[0] == (0, 2)   # 右上起
    assert len(set(order)) == 9


def test_auto_grid():
    assert auto_grid(4) == (2, 2)
    assert auto_grid(9) == (3, 3)
    with pytest.raises(LayoutError):
        auto_grid(10)


def test_layout_rect_centers():
    cells = layout_rect(4, 30, 30, 1.2, "traditional")
    assert len(cells) == 4
    # 右上角格子中心 x 应 > 15mm（右侧）
    first = cells[0]
    assert first.cx > 15 and first.cy < 15


def test_layout_rect_border_inset():
    cells = layout_rect(1, 30, 30, 2.0, "modern")
    c = cells[0]
    assert c.cx == pytest.approx(15.0)
    assert c.cy == pytest.approx(15.0)
    assert c.cell_w <= 28  # 内区 26mm，格 ≤26


def test_layout_circle_ring():
    params = SealParams(text="天地玄黄", shape="circle", diameter_mm=40,
                        border_mm=1.0, reading="traditional")
    geo = build_geometry(params)
    assert len(geo.cells) == 4
    cx = cy = 20.0
    # 0 号字在正上方
    c0 = geo.cells[0]
    assert c0.cx == pytest.approx(cx, abs=0.5) and c0.cy < cy
    # 1 号字逆时针 → 在左侧
    c1 = geo.cells[1]
    assert c1.cx < cx
    # 各字等半径
    rs = [_hypot(c.cx - cx, c.cy - cy) for c in geo.cells]
    assert max(rs) - min(rs) < 1e-6


def test_layout_circle_center_mode():
    params = SealParams(text="龙", shape="circle", diameter_mm=30,
                        circle_center=True)
    geo = build_geometry(params)
    assert len(geo.cells) == 1
    assert geo.cells[0].cx == pytest.approx(15.0)
    assert geo.cells[0].cy == pytest.approx(15.0)


def test_single_line_vertical():
    cells = L.layout_single_line(6, 15, 60, 1.0, vertical=True)
    assert len(cells) == 6
    ys = [c.cy for c in cells]
    assert ys == sorted(ys)          # 自上而下
    xs = {c.cx for c in cells}
    assert len(xs) == 1              # 同一竖线


def test_bad_grid_rejected():
    params = SealParams(text="一二三四五六七八九十", single_line=False)
    with pytest.raises(LayoutError):
        build_geometry(params)


def test_build_geometry_rect_defaults():
    params = SealParams(text="中国篆刻")
    geo = build_geometry(params)
    assert geo.shape == "rect"
    assert len(geo.cells) == 4
    assert geo.border_mm == 0.6
