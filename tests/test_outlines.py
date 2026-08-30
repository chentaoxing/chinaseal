# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <[email protected]>
# SPDX-License-Identifier: GPL-3.0-only
import os

import pytest

from chinaseal.core.outlines import extract_outline, outline_to_cubics, OutlineError

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]


def _find_font():
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


FONT = _find_font()

pytestmark = pytest.mark.skipif(FONT is None, reason="本机无中文字体可供测试")


def test_extract_yong():
    ol = extract_outline(FONT, "永")
    assert ol is not None
    assert ol.upm >= 1000
    kinds = {k for k, _ in ol.segments}
    assert "move" in kinds and ("qcurve" in kinds or "curve" in kinds or "line" in kinds)
    x0, y0, x1, y1 = ol.bbox
    assert x1 > x0 and y1 > y0


def test_missing_char_returns_none():
    # 私有区生僻码位一般不在普通字体 cmap
    assert extract_outline(FONT, "\ue000") is None


def test_cubics_conversion():
    ol = extract_outline(FONT, "印")
    subs = outline_to_cubics(ol)
    assert len(subs) >= 1
    for sub in subs:
        assert len(sub[0]) == 1  # 首段 = moveTo
        for seg in sub[1:]:
            assert len(seg) in (1, 3)
            for pt in seg:
                assert len(pt) == 2


def test_caching_consistency():
    a = extract_outline(FONT, "国")
    b = extract_outline(FONT, "国")
    assert a.segments == b.segments and a.bbox == b.bbox  # 同字提取一致


def test_multi_char_arg_rejected():
    with pytest.raises(OutlineError):
        extract_outline(FONT, "中国")
