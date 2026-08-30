# -*- coding: utf-8 -*-
"""统一几何：字形轮廓 → 印面毫米坐标系路径（y 向下，原点=印面左上角）。

画布预览 / PNG 光栅 / 矢量 PDF 三端共用，保证"预览即所得、导出即所预"。
"""
from __future__ import annotations

import math

from . import layout
from .outlines import extract_outline, outline_to_cubics, OutlineError


def _rot_ccw(p, deg):
    """屏幕坐标（y 向下）中按 deg 顺时针旋转。"""
    if not deg:
        return p
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    x, y = p
    return (x * c - y * s, x * s + y * c)


def char_paths_mm(params, geo, outlines, mirror=True):
    """生成每个字的最终路径。

    outlines: {char: GlyphOutline}（缺字不在其中）
    返回 list[dict(index=i, subpaths=[[(pt,), (c1,c2,pt)...]])]，坐标 mm、y 向下，
    已含格位/旋转/微调/镜像。返回结构供各渲染端直接消费。
    """
    n = len(params.text)
    tf = params.transforms_for(n)
    result = []
    for cell in geo.cells:
        ch = params.text[cell.index]
        outline = outlines.get(ch)
        if outline is None:
            continue
        t = tf[cell.index]
        s = (cell.cell_size / outline.upm) * t.scale
        if s <= 0:
            continue
        bx = (outline.bbox[0] + outline.bbox[2]) / 2.0
        by = (outline.bbox[1] + outline.bbox[3]) / 2.0
        ccx = cell.cx + t.dx
        ccy = cell.cy + t.dy
        deg = cell.rotation + t.rotation

        subpaths_out = []
        for sub in outline_to_cubics(outline):
            out_sub = []
            for seg in sub:
                pts = []
                for p in seg:
                    x = (p[0] - bx) * s
                    y = -(p[1] - by) * s          # y 翻转为屏幕系
                    x, y = _rot_ccw((x, y), deg)
                    x, y = x + ccx, y + ccy
                    if mirror:
                        if params.mirror_h:
                            x = geo.width_mm - x if geo.shape == "rect" else geo.diameter_mm - x
                        if params.mirror_v:
                            y = geo.height_mm - y if geo.shape == "rect" else geo.diameter_mm - y
                    pts.append((x, y))
                out_sub.append(tuple(pts))
            subpaths_out.append(out_sub)
        result.append({"index": cell.index, "char": ch, "subpaths": subpaths_out})
    return result


def load_outlines(params, font_mgr) -> tuple[dict, list]:
    """加载印文所有字的轮廓。返回 (outlines, missing_chars)。"""
    entry = font_mgr.find(params.font_family) if params.font_family else None
    if entry is None and params.font_path:
        raise OutlineError(f"找不到字体：{params.font_family}")
    if entry is None:
        raise OutlineError("未选择字体")
    outlines, missing = {}, []
    for ch in params.text:
        if ch.isspace():
            continue
        if ch in outlines:
            continue
        ol = extract_outline(entry.path, ch, entry.font_number)
        if ol is None:
            missing.append(ch)
        else:
            outlines[ch] = ol
    return outlines, missing


def seal_box(params, geo):
    """印面外接框 (w, h) mm。"""
    if geo.shape == "circle":
        return geo.diameter_mm, geo.diameter_mm
    return geo.width_mm, geo.height_mm
