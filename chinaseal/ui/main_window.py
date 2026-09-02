# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <chentaoxing@gmail.com>
# SPDX-License-Identifier: GPL-3.0-only
"""ChinaSeal 主窗口。"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QPointF
import os
import sys

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtCore import QUrl
from PySide6.QtGui import (QAction, QDesktopServices, QKeySequence, QPainter, QColor, QPen, QFont,
                           QUndoStack, QIcon, QPixmap)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox, QPushButton,
    QLabel, QFileDialog, QMessageBox, QSlider, QScrollArea, QSizePolicy,
    QToolBar, QStatusBar, QApplication, QDialog, QPushButton, QHBoxLayout,
    QProgressDialog)

from PySide6.QtPrintSupport import QPrinter, QPrintDialog

import chinaseal
from .canvas import SealCanvas, CharItem
from .font_downloader import FontDownloaderDialog
from ..core import layout as L
from ..core import downloader as D
from ..core.font_manager import is_free_family
from ..core.resources import logo_path
from ..core import updater as U
import chinaseal
from ..core.model import READINGS as _READINGS
from ..core.layout import LayoutError, build_geometry
from ..core.model import SealParams, CharTransform
from ..core import project_io
from ..core.font_manager import FontManager
from ..core.outlines import OutlineError
from ..core import export as E
from ..core.paths import char_paths_mm, load_outlines, seal_box

APP_TITLE = "象形科技 ChinaSeal v" + chinaseal.__version__ + " - 中国篆刻印稿工坊"

PRESETS = [
    ("方章 30×30mm", ("rect", 30, 30, False)),
    ("方章 25×25mm", ("rect", 25, 25, False)),
    ("方章 40×40mm", ("rect", 40, 40, False)),
    ("方章 20×20mm", ("rect", 20, 20, False)),
    ("长方章 30×60mm", ("rect", 30, 60, False)),
    ("长条章 15×60mm", ("rect", 15, 60, True)),
    ("长条章 20×90mm", ("rect", 20, 90, True)),
    ("圆章 ⌀30mm", ("circle", 30, 30, False)),
    ("圆章 ⌀40mm", ("circle", 40, 40, False)),
]

MM2PT = 72.0 / 25.4


def pick_default_font(font_mgr: FontManager) -> str:
    """优先篆书/捆绑开源字体，其次楷宋，再退回能覆盖'印'字的免费字体。"""
    fams = font_mgr.families()
    # 默认字体按"覆盖广"优先：小篆字库小、缺字多，只作为可选项而非默认
    for f in ("霞鹜文楷", "LXGW WenKai", "思源宋体", "Noto Serif SC", "Source Han Serif SC",
              "思源黑体", "Noto Sans CJK SC", "Source Han Sans SC"):
        if f in fams:
            return f
    for f in fams:
        if "篆" in f or "seal" in f.lower():
            return f
    for cand in ("楷体", "SimKai", "KaiTi", "Microsoft YaHei", "微软雅黑", "SimSun", "宋体"):
        if cand in fams and is_free_family(cand):
            return cand
    free = [f for f in fams if is_free_family(f)] or fams
    for f in free:
        entry = font_mgr.find(f)
        if entry and not font_mgr.coverage(entry, "印"):
            return f
    return free[0] if free else ""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(logo_path()))
        self.resize(1280, 820)
        import os as _os
        _org = _os.environ.get("CHINASEAL_ORG", "ChinaSeal")
        self.settings = QSettings(_org, _org)
        from ..core.resources import bundled_fonts_dir
        self.font_mgr = FontManager(extra_dirs=[bundled_fonts_dir()])
        self.params = SealParams()
        self.params.font_family = pick_default_font(self.font_mgr)
        self.geo = None
        self.charpaths = []
        self.missing = []
        self._outlines_cache = {}
        self._items: list[CharItem] = []
        self._drag_backup: dict | None = None
        self._undo = QUndoStack(self)

        self._build_ui()
        self._load_settings()
        self.refresh()
        self.canvas.fit_seal()
        # 启动自动检测更新（可在弹窗中关闭；3 秒延迟避免阻塞启动）
        QTimer.singleShot(3000, self._startup_update_check)

    # ---------------- UI 构建 ----------------

    def _build_ui(self):
        # 中央部件先建（工具栏的"适合窗口"要引用 self.canvas）
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)

        root.addWidget(self._build_left_panel(), 0)
        self.canvas = SealCanvas()
        root.addWidget(self.canvas, 1)
        root.addWidget(self._build_right_panel(), 0)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.setCentralWidget(central)

        # 画布信号：selectionChanged 在 Qt 应用完选择后触发，最可靠
        self.canvas.scene().selectionChanged.connect(self._sync_char_panel)
        self.canvas.char_selected.connect(self.on_char_selected)
        self.canvas.char_drag_started.connect(self.on_drag_started)
        self.canvas.char_dragged.connect(self.on_drag)
        self.canvas.char_drag_finished.connect(self.on_drag_finished)

        # 工具栏
        self.tb = tb = QToolBar("主工具栏")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.act_new = QAction("新建", self)
        self.act_open = QAction("打开工程…", self)
        self.act_save = QAction("保存工程…", self)
        self.act_pdf = QAction("导出 PDF", self)
        self.act_png = QAction("导出 PNG", self)
        self.act_print = QAction("打印印稿", self)
        self.act_calib = QAction("打印校准页", self)
        self.act_fit = QAction("适合窗口", self)
        for a in (self.act_new, self.act_open, self.act_save):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.act_pdf, self.act_png, self.act_print, self.act_calib):
            tb.addAction(a)
        tb.addSeparator()
        self.act_undo = self._undo.createUndoAction(self, "撤销")
        self.act_redo = self._undo.createRedoAction(self, "重做")
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addAction(self.act_fit)

        # 关于（"适合窗口"按钮之后）
        tb.addSeparator()
        self.act_check_update = QAction("检测更新", self)
        self.act_log = QAction("打开日志", self)
        self.act_about = QAction("关于", self)
        tb.addAction(self.act_check_update)
        tb.addAction(self.act_log)
        tb.addAction(self.act_about)
        self.act_check_update.triggered.connect(self.on_check_update)
        self.act_log.triggered.connect(self.on_open_log)
        self.act_about.triggered.connect(self.on_about)

        for a, fn in ((self.act_new, self.on_new), (self.act_open, self.on_open),
                      (self.act_save, self.on_save), (self.act_pdf, self.on_export_pdf),
                      (self.act_png, self.on_export_png), (self.act_print, self.on_print),
                      (self.act_calib, self.on_print_calibration),
                      (self.act_fit, self.canvas.fit_seal)):
            a.triggered.connect(fn)

    def _build_left_panel(self):
        panel = QWidget()
        panel.setMaximumWidth(320)
        panel.setMinimumWidth(300)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        lay = QVBoxLayout(panel)

        # 印文与字体
        g1 = QGroupBox("印文与字体")
        f1 = QFormLayout(g1)
        self.ed_text = QLineEdit(self.params.text)
        self.ed_text.setPlaceholderText("输入印文（1-9 字，或单行模式不限）")
        f1.addRow("印文", self.ed_text)
        self.cb_font = QComboBox()
        self.cb_font.addItems(self._font_choices(False))
        if self.params.font_family:
            self.cb_font.setCurrentText(self.params.font_family)
        f1.addRow("字体", self.cb_font)
        btn_import = QPushButton("导入字体文件 (ttf/otf/ttc)…")
        f1.addRow("", btn_import)
        btn_download_font = QPushButton("下载并添加其他免费开源字体…")
        f1.addRow("", btn_download_font)
        self.lbl_dl_note = QLabel("因软件大小限制，其他字体不直接打包在软件内，需另行下载。")
        self.lbl_dl_note.setWordWrap(True)
        self.lbl_dl_note.setStyleSheet("color:#888;font-size:11px;")
        f1.addRow("", self.lbl_dl_note)
        self.cb_show_all_fonts = QCheckBox("显示全部字体（含可能有商用风险的系统字体）")
        f1.addRow("", self.cb_show_all_fonts)
        self.lbl_missing = QLabel("")
        self.lbl_missing.setWordWrap(True)
        self.lbl_missing.setStyleSheet("color:#c62828;")
        f1.addRow("", self.lbl_missing)
        lay.addWidget(g1)

        # 印面
        g2 = QGroupBox("印面")
        f2 = QFormLayout(g2)
        self.cb_shape = QComboBox()
        self.cb_shape.addItems(["方形", "长方形", "正圆形"])
        self.cb_shape.setCurrentIndex(0)  # 默认方形
        f2.addRow("章形", self.cb_shape)
        self.sp_w = QDoubleSpinBox(); self.sp_w.setRange(5, 200); self.sp_w.setSuffix(" mm")
        self.sp_h = QDoubleSpinBox(); self.sp_h.setRange(5, 200); self.sp_h.setSuffix(" mm")
        self.sp_d = QDoubleSpinBox(); self.sp_d.setRange(5, 200); self.sp_d.setSuffix(" mm")
        f2.addRow("宽", self.sp_w)
        f2.addRow("高", self.sp_h)
        f2.addRow("直径", self.sp_d)
        self.cb_preset = QComboBox()
        self.cb_preset.addItem("— 尺寸预设 —")
        self.cb_preset.addItems([p[0] for p in PRESETS])
        f2.addRow("", self.cb_preset)
        self.cb_border = QCheckBox("边框")
        self.cb_border.setChecked(self.params.border_enabled)
        self.sp_border = QDoubleSpinBox(); self.sp_border.setRange(0, 5)
        self.sp_border.setSingleStep(0.1); self.sp_border.setSuffix(" mm")
        self.sp_border.setValue(self.params.border_mm)
        f2.addRow(self.cb_border, self.sp_border)
        self.cb_field = QCheckBox("田字格（仅预览）")
        self.cb_field.setChecked(self.params.field_grid)
        f2.addRow("", self.cb_field)
        lay.addWidget(g2)

        # 排版
        g3 = QGroupBox("排版")
        f3 = QFormLayout(g3)
        self.cb_layout = QComboBox()
        self.cb_layout.addItems(["网格布局（1-9 字）", "单行长条（不限字数）"])
        f3.addRow("模式", self.cb_layout)
        self.cb_line_dir = QComboBox()
        self.cb_line_dir.addItems(["竖条（上→下）", "横条（左→右）"])
        f3.addRow("单行方向", self.cb_line_dir)
        self.cb_reading = QComboBox()
        for k in _READINGS:
            self.cb_reading.addItem(L.ORDER_LABELS[k], k)
        self.cb_reading.setCurrentIndex(list(_READINGS).index(self.params.reading))
        f3.addRow("读序", self.cb_reading)
        self.sp_rows = QSpinBox(); self.sp_rows.setRange(0, 9); self.sp_rows.setSpecialValueText("自动")
        self.sp_cols = QSpinBox(); self.sp_cols.setRange(0, 9); self.sp_cols.setSpecialValueText("自动")
        f3.addRow("行数", self.sp_rows)
        f3.addRow("列数", self.sp_cols)
        self.sl_scale = QSlider(Qt.Orientation.Horizontal)
        self.sl_scale.setRange(50, 100)
        self.sl_scale.setValue(int(self.params.char_scale * 100))
        f3.addRow("字占格比", self.sl_scale)
        self.cb_badges = QCheckBox("显示读序编号（仅预览）")
        self.cb_badges.setChecked(True)
        f3.addRow("", self.cb_badges)
        lay.addWidget(g3)

        # 阴阳刻与镜像
        g4 = QGroupBox("刻式")
        f4 = QFormLayout(g4)
        self.cb_yy = QComboBox()
        self.cb_yy.addItem("阳刻（朱文·红字）", "yang")
        self.cb_yy.addItem("阴刻（白文·红底）", "yin")
        self.cb_yy.setCurrentIndex(0 if self.params.yinyang == "yang" else 1)
        f4.addRow("刻式", self.cb_yy)
        self.cb_mirror_h = QCheckBox("水平镜像（刻制用）")
        self.cb_mirror_v = QCheckBox("垂直镜像")
        f4.addRow(self.cb_mirror_h, self.cb_mirror_v)
        lay.addWidget(g4)

        # 输出
        g5 = QGroupBox("输出")
        f5 = QFormLayout(g5)
        self.cb_dpi = QComboBox()
        self.cb_dpi.addItems(["300", "600", "1200"])
        self.cb_dpi.setCurrentText("600")
        f5.addRow("DPI", self.cb_dpi)
        lay.addWidget(g5)
        lay.addStretch(1)

        # 信号
        self.ed_text.textChanged.connect(self._p_text)
        self.cb_font.currentTextChanged.connect(self._p_font)
        btn_import.clicked.connect(self.on_import_font)
        btn_download_font.clicked.connect(self.on_download_fonts)
        self.cb_show_all_fonts.toggled.connect(self._rebuild_font_list)
        self.cb_shape.currentIndexChanged.connect(self._p_shape)
        self.sp_w.valueChanged.connect(self._p_size)
        self.sp_h.valueChanged.connect(self._p_size)
        self.sp_d.valueChanged.connect(self._p_size)
        self.cb_preset.currentIndexChanged.connect(self._p_preset)
        self.cb_border.toggled.connect(self._p_border)
        self.sp_border.valueChanged.connect(self._p_border)
        self.cb_field.toggled.connect(self._p_flag)
        self.cb_layout.currentIndexChanged.connect(self._p_layout_mode)
        self.cb_line_dir.currentIndexChanged.connect(self._p_flag)
        self.cb_reading.currentIndexChanged.connect(self._p_flag)
        self.sp_rows.valueChanged.connect(self._p_flag)
        self.sp_cols.valueChanged.connect(self._p_flag)
        self.sl_scale.valueChanged.connect(self._p_scale)
        self.cb_badges.toggled.connect(lambda _: self.refresh())
        self.cb_yy.currentIndexChanged.connect(self._p_flag)
        self.cb_mirror_h.toggled.connect(self._p_flag)
        self.cb_mirror_v.toggled.connect(self._p_flag)
        self.cb_dpi.currentTextChanged.connect(self._p_dpi)
        return scroll

    def _build_right_panel(self):
        panel = QWidget()
        panel.setMaximumWidth(220)
        panel.setMinimumWidth(190)
        lay = QVBoxLayout(panel)
        g = QGroupBox("选中单字")
        f = QFormLayout(g)
        self.lbl_char = QLabel("（未选中）")
        f.addRow("字", self.lbl_char)
        self.sl_rot = QSlider(Qt.Orientation.Horizontal)
        self.sl_rot.setRange(-180, 180)
        f.addRow("旋转", self.sl_rot)
        self.lbl_rot = QLabel("0°")
        f.addRow("", self.lbl_rot)
        self.sl_cscale = QSlider(Qt.Orientation.Horizontal)
        self.sl_cscale.setRange(50, 200)
        f.addRow("缩放", self.sl_cscale)
        self.lbl_cscale = QLabel("100%")
        f.addRow("", self.lbl_cscale)
        self.btn_reset = QPushButton("重置此字")
        f.addRow("", self.btn_reset)
        lay.addWidget(g)
        tip = QLabel("操作说明：\n· 鼠标拖动单字微调位置\n· 点选单字后可旋转/缩放\n· 滚轮缩放画布\n· 撤销/重做支持所有微调")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#666;font-size:12px;")
        lay.addWidget(tip)
        lay.addStretch(1)

        self.sl_rot.setEnabled(False)
        self.sl_cscale.setEnabled(False)
        self.btn_reset.setEnabled(False)
        self.sl_rot.valueChanged.connect(self._on_rot_live)
        self.sl_rot.sliderReleased.connect(self._on_rot_commit)
        self.sl_cscale.valueChanged.connect(self._on_cscale_live)
        self.sl_cscale.sliderReleased.connect(self._on_cscale_commit)
        self.btn_reset.clicked.connect(self.on_reset_char)
        return panel

    # ---------------- 参数联动 ----------------

    def _font_choices(self, show_all: bool):
        fams = self.font_mgr.families()
        if show_all:
            return fams
        return [f for f in fams if is_free_family(f)]

    def _rebuild_font_list(self, show_all: bool):
        cur = self.cb_font.currentText()
        self.cb_font.blockSignals(True)
        self.cb_font.clear()
        self.cb_font.addItems(self._font_choices(show_all))
        if cur in self._font_choices(show_all):
            self.cb_font.setCurrentText(cur)
        self.cb_font.blockSignals(False)

    def on_download_fonts(self):
        dlg = FontDownloaderDialog(self.font_mgr, self)
        if dlg.exec() == FontDownloaderDialog.DialogCode.Accepted and dlg.added_families:
            self.cb_show_all_fonts.setChecked(False)
            self._rebuild_font_list(False)
            self.cb_font.setCurrentText(dlg.added_families[0])
            self.params.font_family = dlg.added_families[0]
            self.refresh()

    def _update_log(self, msg):
        """更新流程诊断日志（与字体下载/打开日志共用 chinatext.log）。"""
        import datetime as _dt
        log_dir = D.chinaseal_log_dir()
        if ".." in Path(log_dir).parts:
            return
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "chinatext.log"), "a", encoding="utf-8") as f:
            f.write(f"[{_dt.datetime.now():%H:%M:%S}] [update] {msg}\n")

    def on_check_update(self, silent: bool = False):
        """检测更新：silent=True 时（启动自动检测）不弹"已是最新/失败"，仅发现新版才提示。

        网络检测在后台线程执行（UI 不冻结）；完成后经信号回调弹窗。
        """
        from PySide6.QtWidgets import QMessageBox
        if getattr(self, "_update_chk_running", False):
            if not silent:
                QMessageBox.information(self, "检测中", "正在检测更新，请稍候…")
            return
        repo = self.settings.value("download/repo", D.REPO) or D.REPO
        prefer = str(self.settings.value("download/last_good_src", "github"))
        self._update_chk_running = True
        self.act_check_update.setEnabled(False)
        self.status.showMessage("正在检测更新…（GitHub 优先，AtomGit 兜底）", 15000)
        self._update_log(f"检测更新开始 silent={silent} prefer={prefer}")

        class _W(QThread):
            done = Signal(object)
            failed = Signal(str)

            def run(w_self):
                try:
                    tag, assets, src = D.list_release_assets_with_source(
                        repo, prefer=prefer, probe_timeout=8)
                    w_self.done.emit({"tag": tag, "assets": assets, "src": src})
                except Exception as e:
                    w_self.failed.emit(str(e))

        self._update_chk = _W(self)
        self._update_chk.done.connect(
            lambda r: self._on_update_checked(r, silent))
        self._update_chk.failed.connect(
            lambda e: self._on_update_check_failed(e, silent))
        self._update_chk.start()
        # 看门狗：60 秒未完成视为卡死，复位状态并提示（诊断线索写入日志）
        def _watchdog():
            if getattr(self, "_update_chk_running", False):
                self._update_log("看门狗：检测更新 60s 未完成，疑似卡死")
                self._update_chk_running = False
                self.act_check_update.setEnabled(True)
                self.status.clearMessage()
                if not silent:
                    QMessageBox.warning(self, "检测超时",
                                        "检测更新超过 60 秒未完成（网络异常）。\n"
                                        "请稍后重试，或打开日志查看详情。")
        QTimer.singleShot(60000, _watchdog)

    def _on_update_check_failed(self, err: str, silent: bool):
        self._update_log(f"检测失败：{err[:200]}")
        self._update_chk_running = False
        self.act_check_update.setEnabled(True)
        self.status.clearMessage()
        if not silent:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "检测更新失败",
                                "无法连接更新服务器（GitHub/AtomGit）：\n" + str(err) +
                                "\n\n可手动访问：" + D.REPO_URL + "/releases")
        else:
            self.status.showMessage("启动检测更新：服务器不可达，已跳过", 5000)

    def _on_update_checked(self, result: dict, silent: bool):
        from PySide6.QtWidgets import QMessageBox
        self._update_log(f"检测完成 src={result.get('src')} tag={result.get('tag')} "
                         f"资产={len(result.get('assets') or [])}")
        self._update_chk_running = False
        self.act_check_update.setEnabled(True)
        self.status.clearMessage()
        tag, assets = result.get("tag"), result.get("assets", [])
        if not tag:
            if not silent:
                QMessageBox.warning(self, "检测更新失败", "发布服务器未返回版本信息。")
            return
        if not U.version_newer(tag, chinaseal.__version__):
            if not silent:
                QMessageBox.information(self, "已是最新",
                    "当前版本 v" + chinaseal.__version__ + " 已是最新。")
            return
        asset = U.find_portable_asset(assets)
        size_mb = f"{asset.get('size', 0)/1e6:.1f} MB）" if asset else "约 100 MB）"
        if asset is None:
            # 清单源（AtomGit）资产里没有便携包：用确定性 GitHub Release 下载地址
            asset = {"name": f"ChinaSeal-{tag}-portable.zip",
                     "browser_download_url": (f"https://github.com/{repo}/releases/"
                                              f"download/v{tag}/ChinaSeal-{tag}-portable.zip"),
                     "size": 0}
            if not silent:
                self.status.showMessage("清单源无便携包资产，改用 GitHub Release 直链下载", 8000)

        self._update_tag = tag
        self._update_asset_url = asset.get("browser_download_url") or asset.get("url")
        box = QMessageBox(QMessageBox.Icon.Question, "发现新版本",
                          "当前版本 v" + chinaseal.__version__ + "，最新版本 v" + tag +
                          "（" + size_mb + "。\n是否现在下载更新？",
                          parent=self)
        from PySide6.QtWidgets import QCheckBox
        cb = QCheckBox("每次启动自动检测更新")
        cb.setChecked(self.settings.value("update/auto_check", True, type=bool))
        box.setCheckBox(cb)
        btn_dl = box.addButton("下载更新", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        self.settings.setValue("update/auto_check", cb.isChecked())
        if box.clickedButton() is not btn_dl:
            return
        self._download_update(asset, tag)

    def _download_update(self, asset: dict, tag: str):
        """后台下载更新包到暂存目录，完成后询问安装。"""
        from PySide6.QtWidgets import QProgressDialog
        url = asset.get("browser_download_url") or asset.get("url")
        dest = os.path.join(U.staging_dir(), f"ChinaSeal-{tag}-portable.zip")
        self._update_dest = dest
        self._update_tag = tag
        prog = QProgressDialog(f"正在下载 v{tag} 更新包…", None, 0, 100, self)
        prog.setWindowTitle("软件更新")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)
        prog.show()

        class _W(QThread):
            progress = Signal(int, int)
            done = Signal(str)
            failed = Signal(object)

            def __init__(w_self):
                super().__init__(prog)
                w_self._url, w_self._dest = url, dest

            def run(w_self):
                try:
                    U.download_to_file(w_self._url, w_self._dest,
                                       progress=lambda d, t: w_self.progress.emit(d, t))
                    w_self.done.emit(w_self._dest)
                except Exception as e:
                    w_self.failed.emit(str(e))

        self._update_dl = _W()

        def on_prog(d, t):
            prog.setValue(int(d * 100 / max(1, t)))

        def on_done(path):
            prog.close()
            self._log(f"更新包下载完成：{path}")
            self._prompt_install(path, tag)

        def on_fail(err):
            prog.close()
            self._log(f"更新包下载失败：{err}")
            QMessageBox.warning(self, "下载失败", "更新包下载失败：\n" + err)

        self._update_dl.progress.connect(on_prog)
        self._update_dl.done.connect(on_done)
        self._update_dl.failed.connect(on_fail)
        self._update_dl.start()

    def _prompt_install(self, zip_path: str, tag: str):
        """下载完成后询问是否立即安装（生成 helper 并重启）。"""
        from PySide6.QtWidgets import QMessageBox
        frozen = getattr(sys, "frozen", False)
        size_mb = os.path.getsize(zip_path) / 1e6 if os.path.exists(zip_path) else 0
        box = QMessageBox(QMessageBox.Icon.Question, "安装更新",
                          f"更新包已下载（{size_mb:.1f} MB）。\n"
                          "点击「立即安装」后，本程序将关闭并自动完成覆盖升级，然后重新启动。",
                          parent=self)
        btn_now = box.addButton("立即安装并重启", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("稍后手动安装", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not btn_now:
            QMessageBox.information(self, "已保存更新包",
                                    "更新包位置：\n" + zip_path +
                                    "\n关闭本程序后，解压覆盖安装目录即可完成升级。")
            return
        if not frozen:
            QMessageBox.warning(self, "开发模式",
                                "当前为源码运行，无法自动覆盖安装。\n更新包位置：" + zip_path)
            return
        try:
            app_dir = U.app_dir()
            helper = os.path.join(U.staging_dir(), "ChinaSeal-Update.bat")
            U.write_helper_bat(app_dir, zip_path, helper)
            import subprocess as _sp
            _sp.Popen(["cmd", "/c", helper, zip_path], cwd=app_dir,
                      creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PG
            self._log(f"启动更新 helper：{helper}")
            QApplication.quit()
        except Exception as e:
            QMessageBox.critical(self, "安装失败",
                                 "更新启动失败：\n" + str(e) +
                                 "\n\n可手动解压更新包覆盖安装目录。")

    def _startup_update_check(self):
        if self.settings.value("update/auto_check", True, type=bool):
            self.on_check_update(silent=True)

    def on_open_log(self):
        import subprocess as _sp
        from PySide6.QtWidgets import QMessageBox as _QMB
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        log_path = os.path.join(base, "ChinaSeal", "chinatext.log")
        if os.path.exists(log_path):
            _sp.Popen(["notepad.exe", log_path])
        else:
            _QMB.information(self, "日志", "日志文件尚未生成：\n" + log_path)

    def on_about(self):
        dlg = AboutDialog(self)
        dlg.exec()

    def _p_text(self, text):
        self.params.text = text
        self.refresh()

    def _p_font(self, family):
        self.params.font_family = family
        self.refresh()

    def _p_shape(self, idx):
        if idx == 2:
            self.params.shape = "circle"
            self.params.diameter_mm = self.sp_d.value()
        else:
            self.params.shape = "rect"
            if idx == 0:
                self.params.height_mm = self.params.width_mm
        self._sync_size_widgets()
        self.refresh()

    def _p_size(self):
        self.params.width_mm = self.sp_w.value()
        self.params.height_mm = self.sp_h.value()
        self.params.diameter_mm = self.sp_d.value()
        self.refresh()

    def _p_preset(self, idx):
        if idx <= 0:
            return
        shape, a, b, single = PRESETS[idx - 1]
        self.params.shape = "circle" if shape == "circle" else "rect"
        self.params.width_mm, self.params.height_mm = a, b
        self.params.diameter_mm = a
        self.params.single_line = single
        self.params.single_line_vertical = True
        self._sync_size_widgets()
        self._sync_mode_widgets()
        self.refresh()

    def _p_border(self, *_):
        self.params.border_enabled = self.cb_border.isChecked()
        self.params.border_mm = self.sp_border.value()
        self.refresh()

    def _p_flag(self, *_):
        self.params.single_line_vertical = self.cb_line_dir.currentIndex() == 0
        self.params.reading = self.cb_reading.currentData() or "traditional"
        self.params.rows = self.sp_rows.value()
        self.params.cols = self.sp_cols.value()
        self.params.yinyang = self.cb_yy.currentData() or "yang"
        self.params.mirror_h = self.cb_mirror_h.isChecked()
        self.params.mirror_v = self.cb_mirror_v.isChecked()
        self.params.field_grid = self.cb_field.isChecked()
        self.refresh()

    def _p_layout_mode(self, idx):
        self.params.single_line = idx == 1
        self._sync_mode_widgets()
        self.refresh()

    def _p_scale(self, v):
        self.params.char_scale = v / 100.0
        self.refresh()

    def _p_dpi(self, text):
        self.params.dpi = int(text)

    def _sync_size_widgets(self):
        circle = self.params.shape == "circle"
        self.sp_d.setEnabled(circle)
        self.sp_w.setEnabled(not circle)
        self.sp_h.setEnabled(not circle and not (self.cb_shape.currentIndex() == 0))
        self.sp_w.blockSignals(True); self.sp_h.blockSignals(True); self.sp_d.blockSignals(True)
        self.sp_w.setValue(self.params.width_mm)
        self.sp_h.setValue(self.params.height_mm)
        self.sp_d.setValue(self.params.diameter_mm)
        self.sp_w.blockSignals(False); self.sp_h.blockSignals(False); self.sp_d.blockSignals(False)
        if circle:
            self.sp_h.setValue(self.params.diameter_mm)

    def _sync_mode_widgets(self):
        single = self.params.single_line
        self.cb_line_dir.setEnabled(single)
        self.cb_reading.setEnabled(not single)
        self.sp_rows.setEnabled(not single)
        self.sp_cols.setEnabled(not single)
        self.cb_layout.blockSignals(True)
        self.cb_layout.setCurrentIndex(1 if single else 0)
        self.cb_layout.blockSignals(False)

    # ---------------- 刷新与渲染 ----------------

    def _get_outlines(self):
        entry = self.font_mgr.find(self.params.font_family)
        if entry is None:
            self.missing = list(self.params.text.replace(" ", ""))
            return {}
        try:
            outlines, missing = load_outlines(self.params, self.font_mgr)
        except OutlineError as e:
            self.status.showMessage(str(e), 5000)
            self.missing = list(self.params.text)
            return {}
        self.missing = missing
        return outlines

    def _fallback_font_entry(self):
        """缺字预览回显用的兜底字体（覆盖最广的内置字体），与当前字体不同才返回。"""
        for cand in ("霞鹜文楷", "LXGW WenKai", "思源宋体", "Noto Serif CJK SC",
                     "Noto Sans CJK SC", "思源黑体", "Microsoft YaHei", "微软雅黑"):
            if cand == self.params.font_family:
                continue
            e = self.font_mgr.find(cand)
            if e is not None:
                return e
        return None

    def refresh(self):
        """按当前参数重建几何与全部画布内容。"""
        text = self.params.text
        if not text:
            self.lbl_missing.setText("")
            scene0 = self.canvas.scene()
            scene0.clear()
            scene0.badges = []  # clear() 已销毁徽标图元，引用作废
            self._items = []
            self.charpaths = []
            self._update_status()
            return
        try:
            self.geo = build_geometry(self.params)
        except LayoutError as e:
            self.status.showMessage(f"排版错误：{e}", 8000)
            return
        keep_sel = self.canvas.selected_index()  # 重建后恢复选中
        outlines = self._get_outlines()
        fallback_used = set()
        if self.missing:
            self.lbl_missing.setText("⚠ 字体缺字：" + " ".join(self.missing) + "（已阻止导出/打印）")
            # 缺字预览回显：用兜底大字库渲染成琥珀色（导出仍阻止，不静默替换）
            fb = self._fallback_font_entry()
            if fb is not None:
                from chinaseal.core.outlines import extract_outline as _eo
                for ch in self.missing:
                    if ch in outlines:
                        continue
                    try:
                        ol = _eo(fb.path, ch, fb.font_number)
                    except Exception:
                        ol = None
                    if ol is not None:
                        outlines[ch] = ol
                        fallback_used.add(ch)
        else:
            self.lbl_missing.setText("")
        self.charpaths = char_paths_mm(self.params, self.geo, outlines, mirror=True)

        scene = self.canvas.scene()
        w, h = seal_box(self.params, self.geo)
        _, fg, border = E.seal_colors("preview", self.params.yinyang)
        scene.setup_static(w, h, self.geo.shape,
                           self.geo.border_mm if self.params.border_enabled else 0.0,
                           fg, self.params.yinyang, self.params.field_grid,
                           border_color=border)
        scene.update_badges(self.geo.cells, self.cb_badges.isChecked(),
                            self.params.mirror_h, self.params.mirror_v)

        # 单字项原地同步（复用图元，避免删除重建导致视图失效）
        by_idx = {it.index: it for it in self._items}
        new_items = []
        for cp in self.charpaths:
            i = cp["index"]
            cell = self.geo.cells[i]
            ox = (w - cell.cx) if self.params.mirror_h else cell.cx
            oy = (h - cell.cy) if self.params.mirror_v else cell.cy
            rel = []
            for sub in cp["subpaths"]:
                rel.append(tuple(tuple((p[0] - ox, p[1] - oy) for p in seg) for seg in sub))
            item = by_idx.pop(i, None)
            if item is None:
                item = CharItem(i, cp["char"], rel, (ox, oy), fg)
                scene.addItem(item)
                item.dragged.connect(self.canvas.char_dragged.emit)
                item.drag_started.connect(self.canvas.char_drag_started.emit)
                item.drag_finished.connect(self.canvas.char_drag_finished.emit)
            else:
                item.char = cp["char"]
                item.set_geometry(rel, (ox, oy))
                item.set_appearance(fg)
            if cp["char"] in fallback_used:
                item.set_appearance(QColor("#FF8F00"))  # 缺字回显：琥珀色警示
            new_items.append(item)
        for leftover in by_idx.values():
            scene.removeItem(leftover)
        self._items = new_items
        if 0 <= keep_sel < len(self._items):
            self._items[keep_sel].setSelected(True)
        # 强制视口重绘（根治"预览偶尔不刷新"）
        scene.update()
        self.canvas.viewport().update()
        self._sync_char_panel()
        self._update_status()

    def _update_status(self):
        w, h = seal_box(self.params, self.geo) if self.geo else (0, 0)
        n = len(self.params.text.replace(" ", ""))
        mirror = " · 已镜像" if (self.params.mirror_h or self.params.mirror_v) else ""
        self.status.showMessage(
            f"印文 {n} 字 · {w:g}×{h:g}mm · {self.params.font_family or '未选字体'}"
            f" · {self.params.dpi} DPI{mirror}")

    # ---------------- 单字交互 ----------------

    def _sync_char_panel(self):
        idx = self.canvas.selected_index()
        has = 0 <= idx < len(self.params.text)
        self.sl_rot.setEnabled(has)
        self.sl_cscale.setEnabled(has)
        self.btn_reset.setEnabled(has)
        if has:
            t = self.params.transforms_for(len(self.params.text))[idx]
            self.lbl_char.setText(f"第 {idx + 1} 字「{self.params.text[idx]}」")
            for w, val in ((self.sl_rot, int(t.rotation)), (self.sl_cscale, int(t.scale * 100))):
                w.blockSignals(True); w.setValue(val); w.blockSignals(False)
            self.lbl_rot.setText(f"{t.rotation:g}°")
            self.lbl_cscale.setText(f"{int(t.scale * 100)}%")
        else:
            self.lbl_char.setText("（未选中）")
            self.lbl_rot.setText("0°")
            self.lbl_cscale.setText("100%")

    def on_char_selected(self, idx):
        self._sync_char_panel()

    def on_drag_started(self, idx):
        t = self.params.transforms_for(len(self.params.text))[idx]
        self._drag_backup = {"index": idx, "old": CharTransform(t.dx, t.dy, t.scale, t.rotation)}

    def on_drag(self, idx, sdx, sdy):
        item = next((it for it in self._items if it.index == idx), None)
        if item is None or self._drag_backup is None:
            return
        item.setPos(item.origin + QPointF(sdx, sdy))

    def on_drag_finished(self, idx):
        if self._drag_backup is None or self._drag_backup["index"] != idx:
            return
        item = next((it for it in self._items if it.index == idx), None)
        old = self._drag_backup["old"]
        self._drag_backup = None
        if item is None:
            return
        sdx = item.pos().x() - item.origin.x()
        sdy = item.pos().y() - item.origin.y()
        dx = sdx * (-1 if self.params.mirror_h else 1)
        dy = sdy * (-1 if self.params.mirror_v else 1)
        if abs(dx) < 1e-4 and abs(dy) < 1e-4:
            return
        t = self.params.transforms_for(len(self.params.text))[idx]
        new = CharTransform(t.dx + dx, t.dy + dy, t.scale, t.rotation)
        self._apply_and_push(idx, old, new)

    def _apply_transform(self, idx, t):
        n = len(self.params.text)
        while len(self.params.char_transforms) < n:
            self.params.char_transforms.append(CharTransform())
        self.params.char_transforms[idx] = t
        self.refresh()

    def _apply_and_push(self, idx, old, new):
        from PySide6.QtGui import QUndoCommand

        w = self

        class Cmd(QUndoCommand):
            def __init__(self):
                super().__init__(f"调整第 {idx + 1} 字")

            def redo(self):
                w._apply_transform(idx, new)

            def undo(self):
                w._apply_transform(idx, old)

        self._undo.push(Cmd())

    # 旋转/缩放滑杆

    def _on_rot_live(self, v):
        idx = self.canvas.selected_index()
        if idx < 0:
            return
        self.lbl_rot.setText(f"{v}°")
        t = self.params.transforms_for(len(self.params.text))[idx]
        if getattr(self, "_rot_backup", None) is None:
            self._rot_backup = CharTransform(t.dx, t.dy, t.scale, t.rotation)
        self._apply_transform(idx, CharTransform(t.dx, t.dy, t.scale, float(v)))

    def _on_rot_commit(self):
        idx = self.canvas.selected_index()
        bak, self._rot_backup = getattr(self, "_rot_backup", None), None
        if idx < 0 or bak is None:
            return
        t = self.params.transforms_for(len(self.params.text))[idx]
        self._apply_and_push(idx, bak, CharTransform(t.dx, t.dy, t.scale, t.rotation))

    def _on_cscale_live(self, v):
        idx = self.canvas.selected_index()
        if idx < 0:
            return
        self.lbl_cscale.setText(f"{v}%")
        t = self.params.transforms_for(len(self.params.text))[idx]
        if getattr(self, "_cs_backup", None) is None:
            self._cs_backup = CharTransform(t.dx, t.dy, t.scale, t.rotation)
        self._apply_transform(idx, CharTransform(t.dx, t.dy, float(v) / 100.0, t.rotation))

    def _on_cscale_commit(self):
        idx = self.canvas.selected_index()
        bak, self._cs_backup = getattr(self, "_cs_backup", None), None
        if idx < 0 or bak is None:
            return
        t = self.params.transforms_for(len(self.params.text))[idx]
        self._apply_and_push(idx, bak, CharTransform(t.dx, t.dy, t.scale, t.rotation))

    def on_reset_char(self):
        idx = self.canvas.selected_index()
        if idx < 0:
            return
        t = self.params.transforms_for(len(self.params.text))[idx]
        old = CharTransform(t.dx, t.dy, t.scale, t.rotation)
        self._apply_and_push(idx, old, CharTransform())

    # ---------------- 工程文件 ----------------

    def on_new(self):
        self.params = SealParams(font_family=self.params.font_family,
                                 dpi=int(self.cb_dpi.currentText()))
        self._undo.clear()
        self._sync_all_widgets()
        self.refresh()
        self.canvas.fit_seal()

    def on_save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存工程", self._last_dir("project", "ChinaSeal印稿.chinaseal"),
            "ChinaSeal 工程 (*.chinaseal)")
        if not path:
            return
        try:
            project_io.save_project(self.params, path)
            self._set_last_dir("project", path)
            self.status.showMessage(f"已保存：{path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def on_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程", self._last_dir("project", ""),
            "ChinaSeal 工程 (*.chinaseal)")
        if not path:
            return
        try:
            self.params = project_io.load_project(path)
            if self.params.font_path and not os.path.exists(self.params.font_path):
                added = None
                if self.params.font_family in self.font_mgr.families():
                    added = self.params.font_family
                if not added:
                    QMessageBox.warning(self, "字体缺失",
                                        f"工程使用的字体「{self.params.font_family}」未在本机找到，已回退默认字体。")
                    self.params.font_family = pick_default_font(self.font_mgr)
            self._undo.clear()
            self._sync_all_widgets()
            self.refresh()
            self.canvas.fit_seal()
            self._set_last_dir("project", path)
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))

    def _sync_all_widgets(self):
        p = self.params
        self.ed_text.setText(p.text)
        if p.font_family in self.font_mgr.families():
            self.cb_font.setCurrentText(p.font_family)
        self.cb_shape.setCurrentIndex({"rect": 1, "circle": 2}[p.shape]
                                      if p.width_mm != p.height_mm or p.shape == "circle" else 0)
        self._sync_size_widgets()
        self.cb_border.setChecked(p.border_enabled)
        self.sp_border.setValue(p.border_mm)
        self.cb_field.setChecked(p.field_grid)
        self._sync_mode_widgets()
        self.cb_line_dir.setCurrentIndex(0 if p.single_line_vertical else 1)
        i = list(_READINGS).index(p.reading) if p.reading in _READINGS else 1
        self.cb_reading.setCurrentIndex(i)
        self.sp_rows.setValue(p.rows)
        self.sp_cols.setValue(p.cols)
        self.sl_scale.setValue(int(p.char_scale * 100))
        yy = 0 if p.yinyang == "yang" else 1
        self.cb_yy.setCurrentIndex(yy)
        self.cb_mirror_h.setChecked(p.mirror_h)
        self.cb_mirror_v.setChecked(p.mirror_v)
        self.cb_dpi.setCurrentText(str(p.dpi))

    # ---------------- 导出与打印 ----------------

    def _require_ready(self) -> bool:
        if not self.params.text:
            QMessageBox.information(self, "提示", "请先输入印文。")
            return False
        if self.missing:
            QMessageBox.warning(self, "缺字无法导出",
                                "当前字体缺少以下字形：" + " ".join(self.missing) +
                                "\n请更换字体或修改印文。")
            return False
        return True

    def _default_export_name(self, ext):
        return os.path.join(self._last_dir("export", ""),
                            f"ChinaSeal_{datetime.now():%Y%m%d_%H%M%S}.{ext}")

    def on_export_pdf(self):
        if not self._require_ready():
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 PDF（矢量·毫米精确）",
                                              self._default_export_name("pdf"), "PDF (*.pdf)")
        if not path:
            return
        try:
            info = E.export_pdf(self.params, self.geo, self._get_outlines(), path)
            self._set_last_dir("export", path)
            QMessageBox.information(self, "导出成功",
                                    f"矢量 PDF 已生成：\n{info['path']}\n"
                                    f"印面 {info['w_mm']:g}×{info['h_mm']:g}mm，打印时请选择 100% 实际大小。")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def on_export_png(self):
        if not self._require_ready():
            return
        path, _ = QFileDialog.getSaveFileName(self, f"导出 PNG（{self.params.dpi} DPI）",
                                              self._default_export_name("png"), "PNG (*.png)")
        if not path:
            return
        try:
            info = E.export_png(self.params, self.geo, self._get_outlines(), path)
            self._set_last_dir("export", path)
            QMessageBox.information(self, "导出成功",
                                    f"PNG 已生成：\n{info['path']}\n"
                                    f"{info['w_px']}×{info['h_px']}px @ {info['dpi']} DPI（已写入 DPI 元数据）")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def on_print(self):
        if not self._require_ready():
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("打印印稿")
        if dlg.exec() != QPrintDialog.DialogCode.Accepted:
            return
        try:
            charpaths = char_paths_mm(self.params, self.geo, self._get_outlines(), mirror=True)
            E.paint_to_printer(printer, self.params, self.geo, charpaths)
            self.status.showMessage("已发送到打印机（请确认驱动缩放为 100%/实际大小）", 8000)
        except Exception as e:
            QMessageBox.critical(self, "打印失败", str(e))

    def on_print_calibration(self):
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("打印校准页")
        if dlg.exec() != QPrintDialog.DialogCode.Accepted:
            return
        p = QPainter(printer)
        self._paint_calibration(p, printer.resolution())
        p.end()
        self.status.showMessage("校准页已发送。打印后用直尺比对 100mm 标尺是否为 100.0mm。", 10000)

    @staticmethod
    def _paint_calibration(p: QPainter, dpi: int):
        s = dpi / 25.4
        p.scale(s, s)
        ox, oy = 30.0, 60.0
        pen = QPen(QColor("black"), 0.35)
        p.setPen(pen)
        p.drawLine(QPointF(ox, oy), QPointF(ox + 100, oy))
        p.drawLine(QPointF(ox, oy), QPointF(ox, oy + 100))
        for i in range(101):
            tick = 3.0 if i % 10 == 0 else (2.0 if i % 5 == 0 else 1.0)
            p.drawLine(QPointF(ox + i, oy), QPointF(ox + i, oy + tick))
            p.drawLine(QPointF(ox, oy + i), QPointF(ox - tick, oy + i))
        p.drawRect(ox + 15, oy + 15, 20, 20)
        p.drawRect(ox + 15, oy + 45, 20, 20)
        p.setPen(QPen(QColor("black"), 0.2))
        p.drawLine(QPointF(ox + 15, oy + 15), QPointF(ox + 35, oy + 35))
        p.drawLine(QPointF(ox + 15, oy + 35), QPointF(ox + 35, oy + 15))
        f = QFont("Arial", 8)
        p.setFont(f)
        p.resetTransform()
        p.drawText(30 * s, (60 + 112) * s,
                   "校准页：请用直尺核对 100mm 标尺与 20mm 方块。若不符，请在打印设置中选 100%/实际大小。")

    def on_import_font(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入字体文件", "", "字体文件 (*.ttf *.otf *.ttc)")
        if not path:
            return
        try:
            added = self.font_mgr.add_custom(path)
            self._rebuild_font_list(self.cb_show_all_fonts.isChecked())
            self.cb_font.setCurrentText(added[0])
            QMessageBox.information(self, "导入成功", "已加载字体：" + "、".join(added))
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))

    # ---------------- 设置持久化 ----------------

    def _last_dir(self, key, default):
        return self.settings.value(f"lastdir/{key}", default) or default

    def _set_last_dir(self, key, path):
        self.settings.setValue(f"lastdir/{key}", os.path.dirname(path))

    def _load_settings(self):
        geo = self.settings.value("window/geometry")
        if geo:
            self.restoreGeometry(geo)
        s = self.settings
        p = self.params
        if s.contains("last/text"):
            p.text = s.value("last/text") or p.text
        fam = s.value("last/font_family")
        if fam and fam in self.font_mgr.families():
            p.font_family = fam
        if s.contains("last/shape"):
            p.shape = s.value("last/shape")
        for k in ("width_mm", "height_mm", "diameter_mm", "border_mm"):
            if s.contains(f"last/{k}"):
                try:
                    setattr(p, k, float(s.value(f"last/{k}")))
                except (TypeError, ValueError):
                    pass
        if s.contains("last/yinyang"):
            p.yinyang = s.value("last/yinyang")
        p.clamp()
        self.ed_text.setText(p.text)
        self.cb_font.setCurrentText(p.font_family)
        self.cb_shape.setCurrentIndex(2 if p.shape == "circle"
                                      else (1 if p.width_mm != p.height_mm else 0))
        self._sync_size_widgets()
        self.cb_border.setChecked(p.border_enabled)
        self.sp_border.setValue(p.border_mm)
        self.cb_yy.setCurrentIndex(0 if p.yinyang == "yang" else 1)

    def closeEvent(self, ev):
        self.settings.setValue("window/geometry", self.saveGeometry())
        p = self.params
        s = self.settings
        s.setValue("last/text", p.text)
        s.setValue("last/font_family", p.font_family)
        s.setValue("last/shape", p.shape)
        for k in ("width_mm", "height_mm", "diameter_mm", "border_mm"):
            s.setValue(f"last/{k}", float(getattr(p, k)))
        s.setValue("last/yinyang", p.yinyang)
        super().closeEvent(ev)


class AboutDialog(QDialog):
    """关于对话框：LOGO、版本、公司、可点击跳转的更新地址、检测更新。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("关于 ChinaSeal")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        logo = QLabel()
        try:
            pm = QPixmap(logo_path())
            if not pm.isNull():
                logo.setPixmap(pm.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
        except Exception:
            pass
        top.addWidget(logo)
        head = QLabel("<b style='font-size:16pt'>中国篆刻印稿工坊</b>"
                      "<br><span style='color:#666'>ChinaSeal v" + chinaseal.__version__ + "</span>")
        head.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(head)
        top.addStretch(1)
        lay.addLayout(top)

        info = QLabel(
            "面向实体篆刻生产流程的数字印稿设计工具。"
            "<br><br>开发者：chentaoxing <a href=\"mailto:chentaoxing@gmail.com\">chentaoxing@gmail.com</a>"
            "<br>版本与更新地址（点击打开）：<br>"
            "<a href='" + D.REPO_URL + "'>" + D.REPO_URL + "</a>"
            "<br>国内镜像（AtomGit，点击打开）：<br>"
            "<a href='" + D.ATOMGIT_REPO_URL + "'>" + D.ATOMGIT_REPO_URL + "</a>"
            "<br><br><span style='color:#888;font-size:12px'>"
            "内置字体：霞鹜文楷、思源宋体、LXGW Seal（小篆·预览版），"
            "均为开源或免费商用授权；字体列表默认隐藏可能有商用风险的系统字体。</span>")
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setOpenExternalLinks(False)  # 关闭自动打开，改走 linkActivated 显式调起
        info.setTextInteractionFlags(Qt.TextBrowserInteraction)  # 使链接可点击并触发 linkActivated
        info.setWordWrap(True)
        lay.addWidget(info)

        # 链接点击 → 显式调起系统浏览器（QLabel setOpenExternalLinks 在部分环境不生效）
        info.linkActivated.connect(lambda u: QDesktopServices.openUrl(QUrl(u)))

        btns = QHBoxLayout()
        btn_update = QPushButton("检测更新")
        btn_close = QPushButton("关闭")
        btns.addStretch(1)
        btns.addWidget(btn_update)
        btns.addWidget(btn_close)
        lay.addLayout(btns)
        btn_update.clicked.connect(parent.on_check_update)
        btn_close.clicked.connect(self.accept)
