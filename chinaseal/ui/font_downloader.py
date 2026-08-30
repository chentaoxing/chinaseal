# -*- coding: utf-8 -*-
"""字体下载对话框：Gitee（码云）优先、GitHub 兜底，下载免费开源字体并注册。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QProgressBar, QHBoxLayout, QLineEdit, QMessageBox)

from ..core import downloader as D


class _FetchWorker(QThread):
    fetched = Signal(str, str, list)   # source, tag, assets
    failed = Signal(str)

    def __init__(self, repo: str, parent=None):
        super().__init__(parent)
        self._repo = repo

    def run(self):
        try:
            tag, assets, src = D.list_release_assets_with_source(self._repo)
            self.fetched.emit(src, tag, assets)
        except Exception as e:
            self.failed.emit(str(e))


class _DownloadWorker(QThread):
    progress = Signal(int, int)          # done, total
    one_done = Signal(str, list)         # url, font paths
    failed = Signal(str, str)            # url, error
    all_done = Signal()

    def __init__(self, tasks, dest_dir, parent=None):
        super().__init__(parent)
        self._tasks = tasks
        self._dest = dest_dir

    def run(self):
        for url in self._tasks:
            try:
                paths = D.download_asset(url, self._dest,
                                         progress=lambda d, t: self.progress.emit(d, t))
                self.one_done.emit(url, paths)
            except Exception as e:
                self.failed.emit(url, str(e))
        self.all_done.emit()


class FontDownloaderDialog(QDialog):
    def __init__(self, font_mgr, parent=None):
        super().__init__(parent)
        self.font_mgr = font_mgr
        self.added_families: list = []
        import os as _os
        _org = _os.environ.get("CHINASEAL_ORG", "ChinaSeal")
        self.settings = QSettings(_org, _org)
        self.setWindowTitle("下载免费开源字体")
        self.resize(560, 480)

        lay = QVBoxLayout(self)
        tip = QLabel("字体包从软件发布仓库的 Release 附件下载，下载后保存在用户目录 "
                      "ChinaSeal\\fonts 下，即点即用。推荐把字体附件上传到 Gitee（码云）"
                      "仓库的发行版，国内直连速度快；GitHub 自动兜底。请留意字体授权条款。")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        row = QHBoxLayout()
        row.addWidget(QLabel("仓库路径（用户名/仓库名）："))
        self.ed_repo = QLineEdit(self.settings.value("download/repo", D.REPO))
        self.ed_repo.setPlaceholderText("例如 chentaoxing/chinaseal（Gitee 与 GitHub 同名时优先 Gitee）")
        row.addWidget(self.ed_repo, 1)
        self.btn_fetch = QPushButton("获取列表")
        row.addWidget(self.btn_fetch)
        lay.addLayout(row)

        self.listw = QListWidget()
        self.listw.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
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
        btns.addStretch(1)
        btns.addWidget(self.btn_download)
        btns.addWidget(self.btn_close)
        lay.addLayout(btns)

        self.btn_fetch.clicked.connect(self._start_fetch)
        self.btn_download.clicked.connect(self._download_selected)
        self.btn_close.clicked.connect(self.reject)

        self._assets: list = []
        self._start_fetch()

    # ---- 列表 ----

    def _start_fetch(self):
        repo = self.ed_repo.text().strip() or D.REPO
        self.settings.setValue("download/repo", repo)
        self.btn_fetch.setEnabled(False)
        self.btn_download.setEnabled(False)
        self.status.setText(f"正在获取字体列表（Gitee 优先，GitHub 兜底）：{repo} …")
        self._fetch = _FetchWorker(repo, self)
        self._fetch.fetched.connect(self._on_fetched)
        self._fetch.failed.connect(self._on_fetch_failed)
        self._fetch.start()

    def _on_fetched(self, src: str, tag: str, assets: list):
        self.btn_fetch.setEnabled(True)
        self._assets = assets
        self.listw.clear()
        for a in assets:
            mb = a["size"] / 1e6
            QListWidgetItem(f"{a['name']}（{mb:.1f} MB）", self.listw)
        src_label = "Gitee" if src == "gitee" else "GitHub"
        if not assets:
            self.status.setText(f"已连接 {src_label}（{tag}），但该发布版暂无字体附件。"
                                "请把 .ttf/.otf/.ttc/.zip 上传到发行版附件后再试。")
            return
        self.status.setText(f"已从 {src_label} 获取：{tag}，共 {len(assets)} 个可下载文件。")
        self.listw.selectAll()
        self.btn_download.setEnabled(True)

    def _on_fetch_failed(self, err: str):
        self.btn_fetch.setEnabled(True)
        self.status.setText("获取列表失败（Gitee 与 GitHub 均不可达或仓库不存在）。\n"
                            "请确认仓库路径，或手动下载字体后用「导入字体文件」添加。\n" + err)

    # ---- 下载 ----

    def _download_selected(self):
        rows = self.listw.selectedIndexes()
        urls = [self._assets[r.row()]["url"] for r in rows if self._assets[r.row()].get("url")]
        if not urls:
            return
        self.btn_download.setEnabled(False)
        self.progress.setRange(0, 100)
        self._dl = _DownloadWorker(urls, D.user_fonts_dir(), self)
        self._total_urls = len(urls)
        self._done_urls = 0
        self._dl.progress.connect(self._on_progress)
        self._dl.one_done.connect(self._on_one_done)
        self._dl.failed.connect(self._on_dl_failed)
        self._dl.all_done.connect(self._on_all_done)
        self._dl.start()

    def _on_progress(self, done: int, total: int):
        self.progress.setValue(int(done * 100 / max(1, total)))

    def _on_one_done(self, url: str, paths: list):
        self._done_urls += 1
        fams = D.register_downloaded(paths, self.font_mgr)
        self.added_families.extend(f for f in fams if f not in self.added_families)
        self.status.setText(f"已完成 {self._done_urls}/{self._total_urls}；"
                            f"新字体：{'、'.join(fams) or '（未识别到有效字体）'}")

    def _on_dl_failed(self, url: str, err: str):
        self.status.setText("下载失败：" + err)

    def _on_all_done(self):
        self.progress.setValue(100)
        self.btn_download.setEnabled(True)
        if self.added_families:
            QMessageBox.information(self, "下载完成",
                                    "已添加字体：" + "、".join(self.added_families))
            self.accept()
