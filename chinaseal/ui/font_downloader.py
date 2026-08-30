# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <chentaoxing@gmail.com>
# SPDX-License-Identifier: GPL-3.0-only
"""字体下载对话框：GitHub 为主、AtomGit 兜底，下载免费开源字体并注册。"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QProgressBar, QHBoxLayout, QLineEdit, QMessageBox, QComboBox)

from ..core import downloader as D
import chinaseal


class _FetchWorker(QThread):
    fetched = Signal(str, str, list)   # source, tag, assets
    failed = Signal(str)

    def __init__(self, repo: str, prefer: str, strict: bool, parent=None):
        super().__init__(parent)
        self._repo = repo
        self._prefer = prefer
        self._strict = strict

    def run(self):
        try:
            tag, assets, src = D.list_release_assets_with_source(
                self._repo, prefer=self._prefer, strict=self._strict)
            self.fetched.emit(src, tag, assets)
        except Exception as e:
            self.failed.emit(str(e))


class _DownloadWorker(QThread):
    progress = Signal(int, int)          # done, total
    one_done = Signal(object, object)    # url (dict), font paths (list)
    failed = Signal(object, object)      # url, error
    all_done = Signal()
    status = Signal(str)

    def __init__(self, tasks, dest_dir, parent=None, log_path=None):
        super().__init__(parent)
        self._tasks = tasks
        self._dest = dest_dir
        self._log_path = log_path

    def _log(self, msg):
        if not getattr(self, "_log_path", None):
            return
        try:
            import datetime as _dt, os as _os
            _d = _os.path.dirname(self._log_path)
            if _d and not _os.path.isdir(_d):
                _os.makedirs(_d, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"[{_dt.datetime.now():%H:%M:%S}] {msg}" + chr(10))
        except Exception as e:
            print(f"[chinatext.log 写入失败] {e}", file=sys.stderr)

    def run(self):
        self._log(f"开始下载 {len(self._tasks)} 项")
        for url in self._tasks:
            n = url.get("name", "?") if isinstance(url, dict) else str(url)
            self._log(f"尝试 {n} src={url.get('src') if isinstance(url,dict) else 'url'}")
            ok, paths, last_err = self._try_one(url)
            if ok:
                self._log(f"  OK  {n}  -> {paths[0]}")
                self.one_done.emit(url, paths)
                continue
            self._log(f"  FAIL  {n}  {last_err}")
            try:
                fallback = self._resolve_other_source(url)
            except Exception as e:
                self._log(f"  切源异常  {e}")
                self.failed.emit(url, f"{last_err} → 切源失败：{e}")
                continue
            if fallback is None:
                self._log(f"  无备用源")
                self.failed.emit(url, f"主源失败且无备用源：{last_err}")
                continue
            self._log(f"  切到 {fallback.get('src')} 重试 {fallback.get('name')}")
            self.status.emit(f"主源失败，切换为 {fallback.get('src','?')} 重试：{n}")
            ok2, paths2, err2 = self._try_one(fallback)
            if ok2:
                self._log(f"  OK (切源) {fallback.get('name')}")
                self.one_done.emit(fallback, paths2)
            else:
                self._log(f"  切源也失败  {err2}")
                self.failed.emit(url, f"主源：{last_err}；切源({fallback.get('src')})：{err2}")
        self._log(f"全部完成")
        self.all_done.emit()

    def _try_one(self, url):
        try:
            paths = D.download_asset(url, self._dest,
                                     progress=lambda d, t: self.progress.emit(d, t))
            return True, paths, None
        except Exception as e:
            import traceback as _tb
            self._log("  完整 traceback:" + chr(10) + _tb.format_exc())
            return False, None, str(e)

    def _resolve_other_source(self, failed_asset):
        try:
            tag, assets, used_src = D.list_release_assets_with_source()
        except Exception as e:
            self._log(f"  resolve_other_source 取清单失败  {e}")
            return None
        if not assets:
            return None
        want_name = failed_asset.get("name")
        want_size = failed_asset.get("size")
        other_src = "atomgit" if failed_asset.get("src") == "github" else "github"
        for a in assets:
            if a.get("src") != other_src:
                continue
            if (a.get("name") == want_name and
                    (want_size is None or a.get("size") == want_size)):
                return a
        return None


class FontDownloaderDialog(QDialog):
    def __init__(self, font_mgr, parent=None):
        super().__init__(parent)
        self.font_mgr = font_mgr
        self.added_families: list = []
        import os as _os
        _org = _os.environ.get("CHINASEAL_ORG", "ChinaSeal")
        self.settings = QSettings(_org, _org)
        self.setWindowTitle(f"下载免费开源字体 v{chinaseal.__version__}")
        self.resize(560, 480)

        lay = QVBoxLayout(self)
        tip = QLabel("字体包从软件发布仓库的 manifest.json 列表下载（主源 GitHub，国内源 AtomGit 兜底）。"
                      "下载后保存在用户目录 ChinaSeal\\fonts 下，即点即用。"
                      "请留意字体授权条款。")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        row = QHBoxLayout()
        row.addWidget(QLabel("仓库路径（用户名/仓库名）："))
        self.ed_repo = QLineEdit(self.settings.value("download/repo", D.REPO))
        self.ed_repo.setPlaceholderText("例如 chentaoxing/chinaseal")
        row.addWidget(self.ed_repo, 1)
        self.btn_fetch = QPushButton("获取列表")
        row.addWidget(self.btn_fetch)
        lay.addLayout(row)

        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("下载源："))
        self.cb_src = QComboBox()
        self.cb_src.addItem("自动（记忆优先，另一源兜底）", "auto")
        self.cb_src.addItem("GitHub（国际主源）", "github")
        self.cb_src.addItem("AtomGit（国内源）", "atomgit")
        self.cb_src.setCurrentIndex(max(0, self.cb_src.findData(
            self.settings.value("download/source_mode", "auto"))))
        self.cb_src.currentIndexChanged.connect(
            lambda _: self.settings.setValue("download/source_mode", self.cb_src.currentData()))
        src_row.addWidget(self.cb_src, 1)
        src_row.addStretch(1)
        lay.addLayout(src_row)

        self.listw = QListWidget()
        self.listw.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        lay.addWidget(self.listw)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        lay.addWidget(self.progress)

        self.status = QLabel("正在获取字体列表…")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        btns = QHBoxLayout()
        self.btn_download = QPushButton("下载所选")
        self.btn_download.setEnabled(False)
        self.btn_close = QPushButton("关闭")
        self.btn_all = QPushButton("全选")
        self.btn_all.clicked.connect(self._toggle_all)
        btns.addWidget(self.btn_all)
        btns.addStretch(1)
        btns.addWidget(self.btn_download)
        btns.addWidget(self.btn_close)
        lay.addLayout(btns)

        self.btn_fetch.clicked.connect(self._start_fetch)
        self.btn_download.clicked.connect(self._download_selected)
        self.btn_close.clicked.connect(self.reject)

        self._assets: list = []
        import os as _os2
        _base = _os2.environ.get("LOCALAPPDATA") or _os2.path.expanduser("~")
        _log_dir = _os2.path.join(_base, "ChinaSeal")
        _os2.makedirs(_log_dir, exist_ok=True)   # 关键：父目录必须先创建
        self._log_path = _os2.path.join(_log_dir, "chinatext.log")
        self._log("对话框打开")
        self._start_fetch()

    def _log(self, msg):
        import datetime as _dt, os as _os
        try:
            _d = _os.path.dirname(self._log_path) if hasattr(self, "_log_path") else None
            if _d and not _os.path.isdir(_d):
                _os.makedirs(_d, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"[{_dt.datetime.now():%H:%M:%S}] {msg}\n")
        except Exception as e:
            # 日志失败不能静默——打到 stderr 方便诊断
            print(f"[chinatext.log 写入失败] {e}", file=sys.stderr)

    def _toggleable_items(self):
        from PySide6.QtCore import Qt as _Qt
        return [self.listw.item(i) for i in range(self.listw.count())
                if not self.listw.item(i).data(_Qt.ItemDataRole.UserRole)]

    def _toggle_all(self):
        """全选/全不选互斥切换（跳过已下载的禁用项）。"""
        from PySide6.QtCore import Qt as _Qt
        toggleable = self._toggleable_items()
        all_checked = bool(toggleable) and all(
            it.checkState() == _Qt.CheckState.Checked for it in toggleable)
        state = _Qt.CheckState.Unchecked if all_checked else _Qt.CheckState.Checked
        for it in toggleable:
            it.setCheckState(state)
        self.btn_all.setText("全不选" if all_checked else "全选")

    def _refresh_toggle_all(self):
        """无可下载项时禁用全选按钮；否则按勾选状态显示全选/全不选。"""
        from PySide6.QtCore import Qt as _Qt
        toggleable = self._toggleable_items()
        self.btn_all.setEnabled(bool(toggleable))
        all_checked = bool(toggleable) and all(
            it.checkState() == _Qt.CheckState.Checked for it in toggleable)
        self.btn_all.setText("全不选" if all_checked else "全选")

    # ---- 列表 ----

    def _start_fetch(self):
        repo = self.ed_repo.text().strip() or D.REPO
        self.settings.setValue("download/repo", repo)
        self.btn_fetch.setEnabled(False)
        self.btn_download.setEnabled(False)
        mode = self.cb_src.currentData() or "auto"
        if mode == "auto":
            prefer = str(self.settings.value("download/last_good_src", "github"))
            strict = False
            self.status.setText(f"正在获取字体列表（自动：优先 {prefer}，另一源兜底）：{repo} …")
        else:
            prefer, strict = mode, True
            label = "GitHub" if mode == "github" else "AtomGit"
            self.status.setText(f"正在获取字体列表（指定源 {label}，失败不兜底）：{repo} …")
        self._fetch = _FetchWorker(repo, prefer, strict, self)
        self._fetch.fetched.connect(self._on_fetched)
        self._fetch.failed.connect(self._on_fetch_failed)
        self._fetch.start()

    def _on_fetched(self, src: str, tag: str, assets: list):
        self.btn_fetch.setEnabled(True)
        self._assets = assets
        self.listw.clear()
        from PySide6.QtCore import Qt as _Qt
        import os as _os
        ufd = str(D.user_fonts_dir())
        for a in assets:
            mb = a["size"] / 1e6
            fname = _os.path.basename(a["path"])
            already = D.is_downloaded(fname) or _os.path.exists(_os.path.join(ufd, fname))
            it = QListWidgetItem(f"{a['name']}（{mb:.1f} MB）" + ("　—— 已下载" if already else ""),
                                 self.listw)
            it.setFlags(it.flags() | _Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(_Qt.CheckState.Checked)
            if already:
                it.setFlags(it.flags() & ~_Qt.ItemFlag.ItemIsUserCheckable)
                it.setForeground(QBrush(QColor("#9e9e9e")))
                it.setData(_Qt.ItemDataRole.UserRole, True)
        self.settings.setValue("download/last_good_src", src)
        src_label = "GitHub" if src == "github" else "AtomGit"
        if not assets:
            self.status.setText(f"已连接 {src_label}（{tag}），但该发布版暂无字体附件。"
                                "请把 .ttf/.otf/.ttc/.zip 上传到发行版附件后再试。")
            return
        self.status.setText(f"已从 {src_label} 获取：{tag}，共 {len(assets)} 个可下载文件（已全选，点方框可取消）。")
        self.btn_download.setEnabled(True)
        self._refresh_toggle_all()

    def _on_fetch_failed(self, err: str):
        self.btn_fetch.setEnabled(True)
        self.status.setText("获取列表失败（GitHub 与 AtomGit 均不可达或仓库不存在）。\n"
                            "请确认仓库路径，或手动下载字体后用「导入字体文件」添加。\n" + err)

    # ---- 下载 ----

    def _download_selected(self):
        # 按复选框勾选收集资产（字典：src/repo/path/ref）
        from PySide6.QtCore import Qt as _Qt
        try:
            downloadable, any_downloaded = [], False
            for i in range(self.listw.count()):
                it = self.listw.item(i)
                if it.data(_Qt.ItemDataRole.UserRole):          # 已下载条目，跳过
                    any_downloaded = True
                    continue
                if it.checkState() == _Qt.CheckState.Checked:
                    downloadable.append(self._assets[i])
            if not downloadable:
                if any_downloaded:
                    QMessageBox.information(self, "均已下载",
                                            "所选字体均已下载到本地，无需重复下载。")
                else:
                    QMessageBox.warning(self, "未勾选",
                                        "请先勾选要下载的字体（点击条目左侧的方框）。")
                return
            assets = downloadable
            self._log(f"开始下载 {len(assets)} 项（勾选）")
        except Exception:
            import traceback as _tb
            self._log("_download_selected 异常：" + _tb.format_exc())
            QMessageBox.critical(self, "内部错误", "收集勾选项失败，详见日志。")
            return
        self.btn_download.setEnabled(False)
        self.progress.setRange(0, 100)
        self._dl = _DownloadWorker(assets, D.user_fonts_dir(), self, log_path=self._log_path)
        self._total_urls = len(assets)
        self._done_urls = 0
        self._dl.progress.connect(self._on_progress)
        self._dl.one_done.connect(self._on_one_done)
        self._dl.failed.connect(self._on_dl_failed)
        self._dl.all_done.connect(self._on_all_done)
        self._dl.start()

    def _on_progress(self, done: int, total: int):
        self.progress.setValue(int(done * 100 / max(1, total)))

    def _on_one_done(self, url: dict, paths: list):
        self._done_urls += 1
        fams = D.register_downloaded(paths, self.font_mgr)
        self.added_families.extend(f for f in fams if f not in self.added_families)
        if isinstance(url, dict):
            fname = _os.path.basename(url.get("path", "")) if (_os := __import__("os")) else ""
            D.record_downloaded_name(fname, fams[0] if fams else "")
        # 对应条目即时标记为已下载（置灰+禁用勾选）
        want = url.get("path") if isinstance(url, dict) else None
        for i, a in enumerate(self._assets):
            if a.get("path") == want:
                it = self.listw.item(i)
                it.setText(it.text().split("　")[0] + "　—— 已下载")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                it.setForeground(QBrush(QColor("#9e9e9e")))
                it.setData(Qt.ItemDataRole.UserRole, True)
                self._refresh_toggle_all()
                break
        name = url.get("name", "?") if isinstance(url, dict) else str(url)
        self.status.setText(f"已完成 {self._done_urls}/{self._total_urls}：{name}；"
                            f"新字体：{'、'.join(fams) or '（未识别到有效字体）'}")

    def _on_dl_failed(self, url: str, err: str):
        from PySide6.QtWidgets import QMessageBox
        name = url.get("name", "?") if isinstance(url, dict) else str(url)
        QMessageBox.warning(self, "下载失败", "字体「" + name + "」\n\n" + err)
        self.status.setText("下载失败：" + err)

    def _on_all_done(self):
        self.progress.setValue(100)
        self.btn_download.setEnabled(True)
        from PySide6.QtWidgets import QMessageBox
        if self.added_families:
            QMessageBox.information(self, "下载完成",
                                    "已添加字体：" + "、".join(self.added_families))
            self.accept()
        else:
            QMessageBox.warning(self, "下载失败",
                                "无字体下载成功。详细错误见上方状态栏或日志："
                                + (self._log_path or "（日志路径不可用）"))
