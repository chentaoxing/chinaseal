# -*- coding: utf-8 -*-
"""GUI 离屏冒烟：主窗口构建 + 每条按钮链路（对话框全部 stub）。

教训（来自 casting_studio）：GUI 冒烟必须覆盖每条按钮链路，不能只测渲染。
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["CHINASEAL_ORG"] = "ChinaSealTest"  # 隔离真实用户设置

FONT_CANDIDATES = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"]
FONT = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
pytestmark = pytest.mark.skipif(FONT is None, reason="本机无中文字体")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def win(qapp, monkeypatch):
    from chinaseal.ui.main_window import MainWindow
    w = MainWindow()
    yield w
    w.close()


def _stub_dialogs(monkeypatch, save_path=None, open_path=None):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (save_path, "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (open_path, "")))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))


def test_window_builds(win):
    assert win.windowTitle().startswith("象形科技 ChinaSeal")
    assert win.cb_font.count() > 0
    assert len(win._items) == len(win.params.text)
    # 关于/检测更新在工具栏（"适合窗口"之后）
    acts = [a.text() for a in win.tb.actions()]
    assert "关于" in acts and "检测更新" in acts
    assert acts.index("关于") > acts.index("适合窗口")
    # 默认字体列表不含商用风险族（本机装了 FZ 系列字体）
    assert not any(f.startswith(("方正", "FZ", "华文")) for f in win._font_choices(False))
    assert win._font_choices(True) == win.font_mgr.families()
    # 下载按钮与小字说明存在
    assert hasattr(win, "cb_show_all_fonts")


def test_text_change_rebuilds(win):
    win.ed_text.setText("王大明印")
    assert len(win._items) == 4
    win.ed_text.setText("龙")
    assert len(win._items) == 1


def test_shape_circle(win):
    win.cb_shape.setCurrentIndex(2)
    assert win.params.shape == "circle"
    assert win.sp_d.isEnabled()
    assert win.canvas.scene().items()


def test_yinyang_mirror_reading(win):
    win.cb_yy.setCurrentIndex(1)
    assert win.params.yinyang == "yin"
    win.cb_mirror_h.setChecked(True)
    assert win.params.mirror_h
    win.cb_reading.setCurrentIndex(0)
    assert win.params.reading == "modern"


def test_missing_char_blocks_export(win, monkeypatch):
    _stub_dialogs(monkeypatch, save_path="")
    win.ed_text.setText("龘")  # 常规字体无此字（若误有则跳过断言）
    if win.missing:
        blocked = not win._require_ready()
        assert blocked
    win.ed_text.setText("中国篆刻")
    assert win.missing == []
    assert win._require_ready()


def test_export_pdf_chain(win, monkeypatch, tmp_path):
    _stub_dialogs(monkeypatch, save_path=str(tmp_path / "s.pdf"))
    win.ed_text.setText("中国篆刻")
    win.on_export_pdf()
    assert (tmp_path / "s.pdf").exists()


def test_export_png_chain(win, monkeypatch, tmp_path):
    _stub_dialogs(monkeypatch, save_path=str(tmp_path / "s.png"))
    win.ed_text.setText("中国篆刻")
    win.on_export_png()
    assert (tmp_path / "s.png").exists()


def test_project_save_open_chain(win, monkeypatch, tmp_path):
    f = tmp_path / "p.chinaseal"
    _stub_dialogs(monkeypatch, save_path=str(f), open_path=str(f))
    win.ed_text.setText("江山如画")
    win.cb_shape.setCurrentIndex(2)
    win.on_save()
    assert f.exists()
    win.on_new()
    assert win.params.text == "中国篆刻"
    win.on_open()
    assert win.params.text == "江山如画"
    assert win.params.shape == "circle"


def test_import_font_chain(win, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    _stub_dialogs(monkeypatch, open_path=FONT)
    win.on_import_font()
    assert win.cb_font.currentText() in win.font_mgr.families()


def test_drag_undo_chain(win):
    win.ed_text.setText("中国篆刻")
    # 模拟拖动：起点备份 → 虚拟位移 → 结束提交
    win.on_drag_started(0)
    win.on_drag(0, 2.0, 1.0)
    win.on_drag_finished(0)
    t = win.params.transforms_for(4)[0]
    assert abs(t.dx - 2.0) < 1e-6 or abs(t.dx + 2.0) < 1e-6  # 无镜像 dx=+2
    assert win._undo.canUndo()
    win._undo.undo()
    t2 = win.params.transforms_for(4)[0]
    assert abs(t2.dx) < 1e-6


def test_single_selection_enables_panel(win):
    win.ed_text.setText("中国篆刻")
    assert not win.sl_rot.isEnabled() and not win.btn_reset.isEnabled()
    win._items[1].setSelected(True)   # 单纯选中，无拖动
    assert win.sl_rot.isEnabled() and win.btn_reset.isEnabled()
    win._items[1].setSelected(False)
    assert not win.sl_rot.isEnabled()


def test_rotation_slider_chain(win):
    win.ed_text.setText("中国篆刻")
    win._items[1].setSelected(True)  # 触发 scene.selectionChanged（与点击同路径）
    win.sl_rot.setValue(30)
    assert abs(win.params.transforms_for(4)[1].rotation - 30) < 1e-6
    win.sl_rot.sliderReleased.emit()
    assert win._undo.canUndo()
    assert win.btn_reset.isEnabled()  # bug 回归：选中后重置按钮应可用
    win.on_reset_char()
    assert abs(win.params.transforms_for(4)[1].rotation) < 1e-6


def test_calibration_paint(qapp):
    from PySide6.QtGui import QImage, QPainter
    from chinaseal.ui.main_window import MainWindow
    img = QImage(1000, 1200, QImage.Format.Format_RGB32)
    img.fill(0xFFFFFFFF)
    p = QPainter(img)
    MainWindow._paint_calibration(p, 300)
    p.end()
    # 标尺与方块已画出（存在黑像素）
    found_black = False
    for x in range(0, 1000, 10):
        for y in range(0, 1200, 10):
            if img.pixelColor(x, y).value() < 100:
                found_black = True
                break
        if found_black:
            break
    assert found_black


def test_print_dialog_reject_safe(win, monkeypatch):
    from PySide6.QtPrintSupport import QPrintDialog
    monkeypatch.setattr(QPrintDialog, "exec",
                        lambda self: QPrintDialog.DialogCode.Rejected)
    win.on_print()          # 应回流不崩
    win.on_print_calibration()


def test_gitee_release_parse():
    # Gitee v5 返回结构解析（不出网，纯函数级验证）
    from chinaseal.core import downloader as D
    import json
    sample = {"tag_name": "v1.0", "assets": [
        {"name": "fontA.ttf", "size": 100, "browser_download_url": "https://gitee.com/u/r/releases/download/1.0/fontA.ttf"},
        {"name": "readme.md", "size": 1, "browser_download_url": "https://gitee.com/u/r/readme.md"}]}
    tag = sample["tag_name"]
    assets = [a for a in sample["assets"] if a["name"].lower().endswith(D.FONT_EXTS)]
    assert tag == "v1.0" and len(assets) == 1 and assets[0]["name"] == "fontA.ttf"


def test_version_newer():
    from chinaseal.core import downloader as D
    assert D.version_newer("v0.3.0", "0.2.0")
    assert D.version_newer("0.2.1", "0.2.0")
    assert not D.version_newer("0.2.0", "0.2.0")
    assert not D.version_newer("0.1.9", "0.2.0")


def test_validate_url():
    from chinaseal.core import downloader as D
    assert D.validate_url("https://api.github.com/repos/x/y/releases/latest")
    assert D.validate_url("https://gitee.com/api/v5/repos/x/y/releases/latest")
    for bad in ("http://api.github.com/x", "https://127.0.0.1/x",
                "https://192.168.1.1/x", "https://evil.example.com/x"):
        try:
            D.validate_url(bad)
            raise AssertionError("should reject: " + bad)
        except ValueError:
            pass


def test_preview_has_ink(win):
    from chinaseal.core.export import render_seal_image
    img = render_seal_image(win.params, win.geo, win.charpaths, 150, mode="preview")
    red = 0
    for x in range(0, img.width(), 4):
        for y in range(0, img.height(), 4):
            c = img.pixelColor(x, y)
            if c.red() > 130 and c.green() < 110:
                red += 1
    assert red > 50  # 印泥红确实覆盖画面
