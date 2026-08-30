# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <chentaoxing@gmail.com>
# SPDX-License-Identifier: GPL-3.0-only
"""生成验收样张：多种配置的 PNG/PDF 输出到 out/samples/。"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.makedirs("out/samples", exist_ok=True)

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from chinaseal.core.model import SealParams
from chinaseal.core.font_manager import FontManager
from chinaseal.core.layout import build_geometry
from chinaseal.core.paths import load_outlines
from chinaseal.core import export as E

fm = FontManager()


def find_family(*keywords):
    for f in fm.families():
        if any(k in f for k in keywords):
            return f
    return None


kai = find_family("楷", "Kai", "SimSun", "宋") or fm.families()[0]
print("样张字体:", kai)

# 1. 方章·阳刻·传统读序（未镜像）
p = SealParams(text="王大明印", font_family=kai, width_mm=30, height_mm=30,
               reading="traditional", yinyang="yang")
geo = build_geometry(p)
out, _ = load_outlines(p, fm)
E.export_png(p, geo, out, "out/samples/1_yang_rect30_trad.png")
E.export_pdf(p, geo, out, "out/samples/1_yang_rect30_trad.pdf")

# 2. 方章·阴刻·已水平镜像（刻制正稿）
p2 = SealParams(text="王大明印", font_family=kai, width_mm=30, height_mm=30,
                reading="traditional", yinyang="yin", mirror_h=True)
geo2 = build_geometry(p2)
E.export_png(p2, geo2, out, "out/samples/2_yin_rect30_trad_mirror.png")

# 3. 圆章·阳刻·4字环形
p3 = SealParams(text="江山如画", font_family=kai, shape="circle", diameter_mm=40,
                reading="traditional", yinyang="yang")
geo3 = build_geometry(p3)
E.export_png(p3, geo3, out, "out/samples/3_yang_circle40_ring.png")

# 4. 长条单行·阴刻
p4 = SealParams(text="墨缘藏书", font_family=kai, width_mm=15, height_mm=60,
                single_line=True, single_line_vertical=True, yinyang="yin")
geo4 = build_geometry(p4)
E.export_png(p4, geo4, out, "out/samples/4_yin_strip15x60.png")

# 5. 校准页
E.calibration_pdf("out/samples/5_calibration.pdf")
print("done:", os.listdir("out/samples"))
