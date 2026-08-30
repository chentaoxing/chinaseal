# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <[email protected]>
# SPDX-License-Identifier: GPL-3.0-only
"""导出链路冒烟：需要 Qt 离屏平台 + 本机中文字体。"""
import os

import pytest

from chinaseal.core.model import SealParams
from chinaseal.core.font_manager import FontManager
from chinaseal.core import export as E
from chinaseal.core import project_io
from chinaseal.core.paths import char_paths_mm
from chinaseal.core.layout import build_geometry


FONT_CANDIDATES = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                   r"C:\Windows\Fonts\simsun.ttc"]
FONT = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
pytestmark = pytest.mark.skipif(FONT is None, reason="本机无中文字体")


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture()
def font_mgr(qapp):
    fm = FontManager()
    entry = fm.find_by_path(FONT) if hasattr(fm, "find_by_path") else None
    if entry is None:
        # 直接按路径添加，规避扫描差异
        fm.add_custom(FONT)
    return fm


def _make_params(fm):
    fams = [e for e in fm.entries if os.path.normcase(e.path) == os.path.normcase(FONT)]
    fam = fams[0].family
    p = SealParams(text="中国印", font_family=fam, width_mm=30, height_mm=30, dpi=600)
    p.clamp()
    return p


def _outlines(fm, p):
    from chinaseal.core.paths import load_outlines
    return load_outlines(p, fm)


def test_png_export(qapp, font_mgr, tmp_path):
    p = _make_params(font_mgr)
    geo = build_geometry(p)
    outlines, missing = _outlines(font_mgr, p)
    assert not missing
    out = tmp_path / "seal.png"
    info = E.export_png(p, geo, outlines, str(out))
    assert out.exists() and out.stat().st_size > 1000
    assert info["w_px"] == round(30 / 25.4 * 600)  # 709 px @600DPI
    from PIL import Image
    im = Image.open(str(out))
    dpi = im.info.get("dpi", (0, 0))
    assert abs(dpi[0] - 600) < 1 and abs(dpi[1] - 600) < 1


def test_png_yin_polarity(qapp, font_mgr, tmp_path):
    p = _make_params(font_mgr)
    p.yinyang = "yin"
    p.mirror_h = True
    geo = build_geometry(p)
    outlines, _ = _outlines(font_mgr, p)
    out = tmp_path / "seal_yin.png"
    E.export_png(p, geo, outlines, str(out))
    from PIL import Image
    im = Image.open(str(out)).convert("L")
    # 阴刻 = 黑底白字 → 左上角像素（底色）应偏黑
    assert im.getpixel((2, 2)) < 80


def test_pdf_export(qapp, font_mgr, tmp_path):
    p = _make_params(font_mgr)
    geo = build_geometry(p)
    outlines, _ = _outlines(font_mgr, p)
    out = tmp_path / "seal.pdf"
    E.export_pdf(p, geo, outlines, str(out))
    data = out.read_bytes()
    assert data[:5] == b"%PDF-"
    assert b"/MediaBox" in data
    assert out.stat().st_size > 2000


def test_calibration_pdf(tmp_path):
    out = tmp_path / "calib.pdf"
    E.calibration_pdf(str(out))
    assert out.exists() and b"%PDF-" in out.read_bytes()[:8]


def test_project_roundtrip(tmp_path):
    p = SealParams(text="王大明印", reading="huiwen", shape="circle",
                   diameter_mm=35, yinyang="yin", mirror_h=True)
    p.char_transforms = [__import__("chinaseal.core.model", fromlist=["CharTransform"])
                         .CharTransform(dx=1.5, dy=-0.5, scale=1.1, rotation=12)]
    f = tmp_path / "test.chinaseal"
    project_io.save_project(p, str(f))
    q = project_io.load_project(str(f))
    assert q.text == p.text and q.reading == "huiwen" and q.shape == "circle"
    assert q.diameter_mm == 35 and q.yinyang == "yin" and q.mirror_h
    assert q.char_transforms[0].dx == 1.5 and q.char_transforms[0].rotation == 12


def test_project_rejects_traversal(tmp_path):
    from chinaseal.core.project_io import _resolve_project_file
    with pytest.raises(ValueError):
        _resolve_project_file(str(tmp_path / ".." / ".." / "evil.chinaseal"))


def test_charpaths_mirror(qapp, font_mgr):
    p = _make_params(font_mgr)
    geo = build_geometry(p)
    outlines, _ = _outlines(font_mgr, p)
    normal = char_paths_mm(p, geo, outlines, mirror=True)
    p2 = _make_params(font_mgr)
    p2.mirror_h = True
    mirrored = char_paths_mm(p2, geo, outlines, mirror=True)
    # 镜像后同一路径首点 x 应不同
    n0 = normal[0]["subpaths"][0][0][0][0]
    m0 = mirrored[0]["subpaths"][0][0][0][0]
    assert abs(n0 - m0) > 1.0
