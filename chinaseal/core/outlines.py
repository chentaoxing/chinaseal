# -*- coding: utf-8 -*-
"""字形轮廓提取：fontTools 读取 TTF/OTF/TTC 轮廓，统一为线段/二次贝塞尔/三次贝塞尔记录。

这是全部渲染管线的唯一几何来源：
  - 画布预览  → QPainterPath（Qt 原生支持二次贝塞尔）
  - 矢量 PDF  → 二次贝塞尔精确升为三次后写入 reportlab path
坐标一律为字体单位（y 轴向上），调用方负责变换。
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field

from fontTools.ttLib import TTFont, TTLibError
from fontTools.pens.boundsPen import ControlBoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen


class OutlineError(Exception):
    pass


@functools.lru_cache(maxsize=8)
def _load_font(path: str, font_number: int = 0) -> TTFont:
    try:
        return TTFont(path, fontNumber=font_number, lazy=True)
    except TTLibError as e:
        raise OutlineError(f"无法解析字体文件: {path} ({e})") from e


@dataclass
class GlyphOutline:
    """单字轮廓。segments: (kind, points) ，kind ∈ moveTo/lineTo/qcurve/curve/close。"""
    segments: list = field(default_factory=list)
    upm: int = 1000
    bbox: tuple = (0.0, 0.0, 0.0, 0.0)  # xMin yMin xMax yMax（字体单位）

    @property
    def bbox_size(self):
        return (self.bbox[2] - self.bbox[0], self.bbox[3] - self.bbox[1])


def _quad_to_cubic(p0, c, p1):
    """二次贝塞尔精确升为三次。"""
    x0, y0 = p0; cx, cy = c; x1, y1 = p1
    c1 = (x0 + 2.0 / 3.0 * (cx - x0), y0 + 2.0 / 3.0 * (cy - y0))
    c2 = (x1 + 2.0 / 3.0 * (cx - x1), y1 + 2.0 / 3.0 * (cy - y1))
    return (c1, c2, p1)


def extract_outline(font_path: str, ch: str, font_number: int = 0) -> GlyphOutline | None:
    """提取字符轮廓；字符不在 cmap 中返回 None（缺字）。"""
    if len(ch) != 1:
        raise OutlineError("extract_outline 只接受单字符")
    tt = _load_font(str(font_path), font_number)
    cmap = tt.getBestCmap()
    if ord(ch) not in cmap:
        return None
    glyph_set = tt.getGlyphSet()
    glyph_name = cmap[ord(ch)]
    pen = DecomposingRecordingPen(glyph_set)
    try:
        glyph_set[glyph_name].draw(pen)
    except Exception as e:  # 个别字体个别字形损坏不应炸整库
        raise OutlineError(f"字形轮廓提取失败: {ch!r} in {font_path} ({e})") from e

    segments = []
    start = None      # 轮廓起点
    cur = None        # 当前点
    pending_offs = []  # 未落定的 off-curve 点（TrueType 隐式 on 点）

    def flush_quads(end_point):
        """把 pending off 点按 TrueType 规则切成连续二次贝塞尔，终点为 end_point。"""
        nonlocal cur, pending_offs
        offs = pending_offs
        pending_offs = []
        if not offs:
            return
        n = len(offs)
        for i in range(n):
            if i < n - 1:
                a, b = offs[i], offs[i + 1]
                implied = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
                segments.append(("qcurve", (cur, a, implied)))
                cur = implied
            else:
                segments.append(("qcurve", (cur, offs[i], end_point)))
                cur = end_point

    for op, args in pen.value:
        if op == "moveTo":
            if pending_offs:  # 理论不出现，防御
                flush_quads(cur)
            start = args[0]
            segments.append(("move", args[0]))
            cur = args[0]
        elif op == "lineTo":
            flush_quads(args[0])
            segments.append(("line", args[0]))
            cur = args[0]
        elif op == "qCurveTo":
            pts = list(args)
            if len(pts) == 1:
                # 单个 off 点，on 点隐含（= 下一操作终点或轮廓起点）
                pending_offs.append(pts[0])
            else:
                offs, on = pts[:-1], pts[-1]
                pending_offs.extend(offs)
                flush_quads(on)
            cur = pts[-1]
        elif op == "curveTo":
            flush_quads(args[0])
            segments.append(("curve", args))
            cur = args[-1]
        elif op == "closePath":
            if pending_offs:
                flush_quads(start)
            if start is not None and cur != start:
                segments.append(("line", start))
            segments.append(("close", ()))
            start = None
            cur = None
        elif op == "endPath":
            if pending_offs:
                flush_quads(cur)
            start = None
            cur = None

    if pending_offs:  # 未闭合轮廓兜底
        flush_quads(cur if cur is not None else (0.0, 0.0))

    # 控制点包围盒（比精确包围盒略大，但无极值求解开销，排版够用）
    bp = ControlBoundsPen(glyph_set)
    try:
        glyph_set[glyph_name].draw(bp)
        bbox = bp.bounds or (0.0, 0.0, 0.0, 0.0)
    except Exception:
        bbox = (0.0, 0.0, 0.0, 0.0)

    upm = tt["head"].unitsPerEm
    return GlyphOutline(segments=segments, upm=upm, bbox=bbox)


def outline_to_cubics(outline: GlyphOutline):
    """转换为 PDF 用的纯三次贝塞尔段序列。

    返回 list[list[subpath]]，每个 subpath 是 [(pt, c1, c2) | (pt,)]：
    长度 1 = 直线到 pt；长度 3 = 三次贝塞尔 (c1, c2, pt)。
    """
    subpaths = []
    cur_path = None
    cur = None
    for kind, pts in outline.segments:
        if kind == "move":
            if cur_path:
                subpaths.append(cur_path)
            cur_path = [(pts,)]
            cur = pts
        elif kind == "line":
            cur_path.append((pts,))
            cur = pts
        elif kind == "qcurve":
            p0, c, p1 = pts
            cur_path.append(_quad_to_cubic(p0, c, p1))
            cur = p1
        elif kind == "curve":
            cur_path.append((pts[0], pts[1], pts[2]))
            cur = pts[2]
        elif kind == "close":
            if cur_path:
                subpaths.append(cur_path)
            cur_path = None
            cur = None
    if cur_path:
        subpaths.append(cur_path)
    return subpaths
