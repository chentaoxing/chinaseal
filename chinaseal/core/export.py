# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <[email protected]>
# SPDX-License-Identifier: GPL-3.0-only
"""渲染与导出：QPainter 光栅（预览/PNG/打印）+ reportlab 矢量 PDF + 校准页。"""
from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime

from PySide6.QtCore import Qt, QPointF, QBuffer, QIODevice
from PySide6.QtGui import (QColor, QImage, QPainter, QPainterPath, QPen, QFont,
                           QGuiApplication)

from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib.units import mm as RL_MM

MM_PER_INCH = 25.4
SEAL_RED = "#C0392B"
PAGE_W_MM, PAGE_H_MM = 210.0, 297.0  # A4


# ---------------- 颜色 ----------------

def seal_colors(mode: str, yinyang: str):
    """返回 (背景色, 字色, 边框色)。

    白文（阴刻）的边栏与底连为一体（同色），朱文边栏与字同色。
    preview=印泥朱红；export=黑白印稿。
    """
    if mode == "preview":
        red = QColor(SEAL_RED)
        if yinyang == "yang":
            return (QColor("white"), red, red)
        return (red, QColor("white"), red)
    black, white = QColor("black"), QColor("white")
    if yinyang == "yang":
        return (white, black, black)
    return (black, white, black)


# ---------------- Qt 光栅 ----------------

def build_char_path(subpaths) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)  # 与 PDF nonzero 一致
    for sub in subpaths:
        pts = sub[0]
        path.moveTo(QPointF(*pts[0]))
        for seg in sub[1:]:
            if len(seg) == 1:
                path.lineTo(QPointF(*seg[0]))
            else:  # (c1, c2, p)
                path.cubicTo(QPointF(*seg[0]), QPointF(*seg[1]), QPointF(*seg[2]))
        path.closeSubpath()
    return path


def paint_seal_qt(painter: QPainter, params, geo, charpaths, bg: QColor, fg: QColor,
                  show_field_grid: bool = False, border_color: QColor | None = None):
    """在已按 1单位=1mm 缩放、原点=印面左上角的 painter 上绘制完整印面。"""
    w, h = (geo.diameter_mm, geo.diameter_mm) if geo.shape == "circle" else (geo.width_mm, geo.height_mm)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # 底
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(bg)
    if geo.shape == "circle":
        painter.drawEllipse(QPointF(0, 0), w / 2, h / 2)
    else:
        painter.drawRect(0, 0, w, h)
    # 边框
    if geo.border_mm > 0:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(border_color or fg, geo.border_mm)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        if geo.shape == "circle":
            r = w / 2 - geo.border_mm / 2
            painter.drawEllipse(QPointF(w / 2, h / 2), r, r)
        else:
            painter.drawRect(geo.border_mm / 2, geo.border_mm / 2,
                             w - geo.border_mm, h - geo.border_mm)
    # 田字格（仅预览）
    if show_field_grid:
        pen = QPen(fg, 0.15)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(w / 2, 0), QPointF(w / 2, h))
        painter.drawLine(QPointF(0, h / 2), QPointF(w, h / 2))
    # 字
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fg)
    for cp in charpaths:
        painter.drawPath(build_char_path(cp["subpaths"]))
    painter.restore()


def render_seal_image(params, geo, charpaths, dpi: int, mode: str = "export") -> QImage:
    """按 DPI 渲染整枚印章（含底色），并写入 DPI 元数据。"""
    w_mm = geo.diameter_mm if geo.shape == "circle" else geo.width_mm
    h_mm = geo.diameter_mm if geo.shape == "circle" else geo.height_mm
    w_px = max(1, round(w_mm / MM_PER_INCH * dpi))
    h_px = max(1, round(h_mm / MM_PER_INCH * dpi))
    img = QImage(w_px, h_px, QImage.Format.Format_RGB32)
    bg, fg, border = seal_colors(mode, params.yinyang)
    img.fill(bg)
    p = QPainter(img)
    p.scale(w_px / w_mm, h_px / h_mm)
    paint_seal_qt(p, params, geo, charpaths, bg, fg, show_field_grid=False,
                  border_color=border)
    p.end()
    dpm = round(dpi / 0.0254)  # dots per meter
    img.setDotsPerMeterX(dpm)
    img.setDotsPerMeterY(dpm)
    return img


def export_png(params, geo, outlines, out_path: str) -> dict:
    from .paths import char_paths_mm
    charpaths = char_paths_mm(params, geo, outlines, mirror=True)
    img = render_seal_image(params, geo, charpaths, params.dpi, mode="export")
    if not img.save(out_path, "PNG"):
        raise IOError(f"PNG 写入失败: {out_path}")
    return {"path": out_path, "w_px": img.width(), "h_px": img.height(), "dpi": params.dpi}


# ---------------- 矢量 PDF ----------------

def _pdf_color(qc: QColor):
    return (qc.redF(), qc.greenF(), qc.blueF())


def _add_subpaths_to_pdfpath(pdfpath, subpaths):
    for sub in subpaths:
        pts = sub[0]
        pdfpath.moveTo(*pts[0])
        for seg in sub[1:]:
            if len(seg) == 1:
                pdfpath.lineTo(*seg[0])
            else:
                pdfpath.curveTo(*seg[0], *seg[1], *seg[2])
        pdfpath.close()


def export_pdf(params, geo, outlines, out_path: str, crop_marks: bool = True):
    """矢量 PDF：A4 居中放置，字形以三次贝塞尔路径嵌入（ nonzero 填充）。

    注意：整体坐标系 scale(mm, -mm) 后使用与预览一致的 y 向下毫米坐标。
    """
    from .paths import char_paths_mm
    charpaths = char_paths_mm(params, geo, outlines, mirror=True)
    w_mm = geo.diameter_mm if geo.shape == "circle" else geo.width_mm
    h_mm = geo.diameter_mm if geo.shape == "circle" else geo.height_mm
    bg, fg, border = seal_colors("export", params.yinyang)

    c = pdf_canvas.Canvas(out_path, pagesize=(PAGE_W_MM * RL_MM, PAGE_H_MM * RL_MM))
    c.setTitle("ChinaSeal 印稿")
    x0 = (PAGE_W_MM - w_mm) / 2
    y0 = (PAGE_H_MM - h_mm) / 2

    c.saveState()
    # 原=印章左上角，单位 mm，y 向下
    c.translate(x0 * RL_MM, (PAGE_H_MM - y0) * RL_MM)
    c.scale(RL_MM, -RL_MM)

    # 底
    c.setFillColorRGB(*_pdf_color(bg))
    c.setStrokeColorRGB(*_pdf_color(bg))
    if geo.shape == "circle":
        c.circle(w_mm / 2, h_mm / 2, w_mm / 2, stroke=0, fill=1)
    else:
        c.rect(0, 0, w_mm, h_mm, stroke=0, fill=1)
    # 边框
    if geo.border_mm > 0:
        c.setFillColorRGB(*_pdf_color(border))
        c.setStrokeColorRGB(*_pdf_color(border))
        c.setLineWidth(geo.border_mm)
        if geo.shape == "circle":
            r = w_mm / 2 - geo.border_mm / 2
            c.circle(w_mm / 2, h_mm / 2, r, stroke=1, fill=0)
        else:
            c.rect(geo.border_mm / 2, geo.border_mm / 2, w_mm - geo.border_mm,
                   h_mm - geo.border_mm, stroke=1, fill=0)
    # 字
    c.setFillColorRGB(*_pdf_color(fg))
    for cp in charpaths:
        path = c.beginPath()
        _add_subpaths_to_pdfpath(path, cp["subpaths"])
        c.drawPath(path, stroke=0, fill=1, fillMode=0)  # 0 = nonzero winding
    c.restoreState()

    # 裁切框（页面坐标系，mm→pt 手动换算）
    if crop_marks:
        gap, ln = 3.0, 4.0
        cx0, cy0 = x0 - gap, PAGE_H_MM - (y0 + h_mm) - gap
        cx1, cy1 = x0 + w_mm + gap, PAGE_H_MM - y0 + gap
        c.setStrokeColorRGB(0.55, 0.55, 0.55)
        c.setLineWidth(0.2)
        c.setDash(1.2, 1.2)
        c.rect(cx0 * RL_MM, cy0 * RL_MM, (cx1 - cx0) * RL_MM, (cy1 - cy0) * RL_MM,
               stroke=1, fill=0)
        c.setDash()
    # 尺寸标注（ASCII，避免 CJK 字体嵌入问题）
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    label = (f"ChinaSeal {w_mm:g}x{h_mm:g}mm {params.dpi}dpi "
             f"{'YIN' if params.yinyang == 'yin' else 'YANG'}"
             f"{' MIRRORED' if (params.mirror_h or params.mirror_v) else ''} "
             f"{datetime.now():%Y-%m-%d %H:%M}")
    c.drawString(x0 * RL_MM, (PAGE_H_MM - y0 + 8) * RL_MM, label)
    c.showPage()
    c.save()
    return {"path": out_path, "w_mm": w_mm, "h_mm": h_mm}


# ---------------- 校准页 ----------------

def calibration_pdf(out_path: str):
    """毫米标尺校准页：打印后用真实直尺比对，验证打印机 1:1 输出。"""
    c = pdf_canvas.Canvas(out_path, pagesize=(PAGE_W_MM * RL_MM, PAGE_H_MM * RL_MM))
    c.setTitle("ChinaSeal 校准页")
    ox, oy = 30.0, 100.0  # 标尺原点 (mm)

    def X(v): return (ox + v) * RL_MM
    def Y(v): return (PAGE_H_MM - oy - v) * RL_MM

    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.35)
    # 水平标尺 100mm
    c.line(X(0), Y(0), X(100), Y(0))
    for i in range(101):
        tick = 3.0 if i % 10 == 0 else (2.0 if i % 5 == 0 else 1.0)
        c.line(X(i), Y(0), X(i), Y(0) + tick * RL_MM)
        if i % 10 == 0:
            c.setFont("Helvetica", 6)
            c.drawCentredString(X(i), Y(0) - 4.5 * RL_MM, str(i))
    # 垂直标尺 100mm
    c.line(X(0), Y(0), X(0), Y(100))
    for i in range(101):
        tick = 3.0 if i % 10 == 0 else (2.0 if i % 5 == 0 else 1.0)
        c.line(X(0), Y(i), X(0) - tick * RL_MM, Y(i))
        if i % 10 == 0 and i > 0:
            c.setFont("Helvetica", 6)
            c.drawRightString(X(0) - 5.5 * RL_MM, Y(i) - 1.5 * RL_MM, str(i))
    # 20x20mm 参考方块
    c.setLineWidth(0.35)
    c.rect(X(15), Y(15), 20 * RL_MM, 20 * RL_MM, stroke=1, fill=0)
    c.rect(X(15), Y(45), 20 * RL_MM, 20 * RL_MM, stroke=1, fill=0)
    # 对角线（检验纵横一致性）
    c.line(X(15), Y(15), X(35), Y(35))
    c.line(X(15), Y(35), X(35), Y(15))
    c.setFont("Helvetica", 8)
    c.drawString(X(0), Y(-12), "Calibration: measure the 100mm rulers and the 20mm squares.")
    c.drawString(X(0), Y(-18), "Both must match physical millimeters. If not, print with 100% scale.")
    c.showPage()
    c.save()
    return {"path": out_path}


# ---------------- 打印 ----------------

def paint_to_printer(printer, params, geo, charpaths):
    """把当前印稿按真实毫米尺寸画到 QPrinter。镜像/阴阳刻已在 charpaths/颜色中体现。"""
    from PySide6.QtPrintSupport import QPrinter
    w_mm = geo.diameter_mm if geo.shape == "circle" else geo.width_mm
    h_mm = geo.diameter_mm if geo.shape == "circle" else geo.height_mm
    page = printer.pageRect(QPrinter.Unit.Millimeter)  # 页面可打印区（含边距偏移）
    if w_mm > page.width() or h_mm > page.height():
        raise ValueError(f"印面 {w_mm:g}x{h_mm:g}mm 超出页面可打印区 "
                         f"{page.width():g}x{page.height():g}mm")
    p = QPainter(printer)
    scale = printer.resolution() / MM_PER_INCH
    p.scale(scale, scale)
    p.translate(-page.x(), -page.y())  # 原点移到可打印区左上角（逻辑 mm 坐标）
    bg, fg, border = seal_colors("export", params.yinyang)
    paint_seal_qt(p, params, geo, charpaths, bg, fg, show_field_grid=False,
                  border_color=border)
    p.end()
