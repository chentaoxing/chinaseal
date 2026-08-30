# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <[email protected]>
# SPDX-License-Identifier: GPL-3.0-only
"""印稿画布：毫米坐标系 QGraphicsScene，单字项支持拖动/选择。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QPen, QBrush, QPainterPath, QPainter, QFont
from PySide6.QtWidgets import (QGraphicsScene, QGraphicsView, QGraphicsItem,
                               QGraphicsObject)

from ..core.export import build_char_path, SEAL_RED


class CharItem(QGraphicsObject):
    """单字：path 为相对自身原点（变换后格心）的毫米路径。"""

    dragged = Signal(int, float, float)     # index, 屏幕系 ddx, ddy（拖动累计）
    drag_started = Signal(int)
    drag_finished = Signal(int)

    def __init__(self, index, char, subpaths_rel, origin, fg: QColor, parent=None):
        super().__init__(parent)
        self.index = index
        self.char = char
        self.origin = QPointF(*origin)
        self.setPos(self.origin)
        self._path = build_char_path(subpaths_rel)
        self._fg = fg
        self.selected_flag = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)

    def set_appearance(self, fg: QColor):
        self._fg = fg
        self.update()

    def set_geometry(self, subpaths_rel, origin):
        self.origin = QPointF(*origin)
        self.setPos(self.origin)
        self._path = build_char_path(subpaths_rel)
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self):
        return self._path.boundingRect().adjusted(-0.5, -0.5, 0.5, 0.5)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._fg))
        painter.drawPath(self._path)
        if self.isSelected():
            painter.setPen(QPen(QColor("#1E88E5"), 0.25, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._path.boundingRect().adjusted(-0.6, -0.6, 0.6, 0.6))

    def mousePressEvent(self, event):
        self.drag_started.emit(self.index)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        delta = event.scenePos() - event.buttonDownScenePos(Qt.MouseButton.LeftButton)
        self.dragged.emit(self.index, delta.x(), delta.y())
        event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_finished.emit(self.index)
        super().mouseReleaseEvent(event)


class SealScene(QGraphicsScene):
    """场景单位 = 1mm。背景/边框/田字格/读序徽标 + CharItem 集合。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.seal_w = self.seal_h = 30.0
        self.badges: list = []
        self.field_lines: list = []

    def setup_static(self, w, h, shape, border_mm, fg_color, yinyang, field_grid,
                     border_color=None):
        """重建背景项。"""
        for it in list(self.items()):
            if not isinstance(it, CharItem) and it not in self.badges:
                self.removeItem(it)
        self.seal_w, self.seal_h = w, h
        self.setSceneRect(-6, -6, w + 12, h + 12)
        bg = QColor("white") if yinyang == "yang" else QColor(SEAL_RED)
        red = QColor(SEAL_RED)
        if shape == "circle":
            bg_item = self.addEllipse(0, 0, w, h, Qt.PenStyle.NoPen, QBrush(bg))
        else:
            bg_item = self.addRect(0, 0, w, h, Qt.PenStyle.NoPen, QBrush(bg))
        bg_item.setZValue(-10)
        if border_mm > 0:
            pen = QPen(border_color or fg_color, border_mm)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            if shape == "circle":
                r = w / 2 - border_mm / 2
                self.addEllipse(w / 2 - r, h / 2 - r, 2 * r, 2 * r, pen, Qt.BrushStyle.NoBrush)
            else:
                self.addRect(border_mm / 2, border_mm / 2, w - border_mm, h - border_mm,
                             pen, Qt.BrushStyle.NoBrush)
        if field_grid:
            pen = QPen(fg_color, 0.15, Qt.PenStyle.DashLine)
            self.addLine(w / 2, 0, w / 2, h, pen)
            self.addLine(0, h / 2, w, h / 2, pen)

    def update_badges(self, cells, show: bool, mirror_h: bool, mirror_v: bool):
        """读序徽标（仅预览）：图元持久复用，原地更新，避免视图失效。"""
        font = QFont("Microsoft YaHei", 1)
        font.setPointSizeF(1.6)
        if not show:
            for t in self.badges:
                t.setVisible(False)
            return
        n = len(cells)
        while len(self.badges) < n:
            t = self.addSimpleText("", font)
            t.setBrush(QBrush(QColor("#1565C0")))
            t.setZValue(20)
            self.badges.append(t)
        for i, cell in enumerate(cells):
            t = self.badges[i]
            x, y = cell.cx, cell.cy
            if mirror_h:
                x = self.seal_w - x
            if mirror_v:
                y = self.seal_h - y
            t.setText(str(cell.index + 1))
            t.setToolTip(f"第 {cell.index + 1} 字")
            t.setPos(x + 0.15, y - 1.6)
            t.setVisible(True)
        for t in self.badges[n:]:
            t.setVisible(False)


class SealCanvas(QGraphicsView):
    char_selected = Signal(int)            # -1 = 取消选择
    char_dragged = Signal(int, float, float)
    char_drag_started = Signal(int)
    char_drag_finished = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(SealScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setBackgroundBrush(QBrush(QColor("#5a5a5a")))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._zoom = 1.0

    def fit_seal(self):
        self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = self.transform().m11()

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)
        self._zoom *= factor

    # -- 转发 CharItem 信号 --

    def connect_items(self, items):
        for it in items:
            it.dragged.connect(self.char_dragged.emit)
            it.drag_started.connect(self.char_drag_started.emit)
            it.drag_finished.connect(self.char_drag_finished.emit)

    def selected_index(self) -> int:
        sel = self.scene().selectedItems()
        for it in sel:
            if isinstance(it, CharItem):
                return it.index
        return -1

    def clear_selection(self):
        for it in self.scene().selectedItems():
            it.setSelected(False)
        self.char_selected.emit(-1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self.itemAt(event.position().toPoint())
            if not isinstance(hit, CharItem):
                self.clear_selection()
        super().mousePressEvent(event)
        hit = self.itemAt(event.position().toPoint())
        if isinstance(hit, CharItem):
            self.char_selected.emit(hit.index)
