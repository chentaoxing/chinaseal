# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <chentaoxing@gmail.com>
# SPDX-License-Identifier: GPL-3.0-only
"""全局异常兜底：PyInstaller --windowed 下 stderr 不存在，槽函数抛异常会
无声消失（弹窗蒸发、下载完成没反应）。本模块把任何未捕获异常
1) 完整 traceback 写入 chinatext.log；2) 弹窗告知（限次，防刷屏）。"""
import sys
import threading
import traceback

_dialog_quota = 3
_lock = threading.Lock()


def log_path() -> str:
    try:
        from .downloader import chinaseal_log_dir
        import os
        return os.path.join(chinaseal_log_dir(), "chinatext.log")
    except Exception:
        return ""


def _write_log(text: str) -> None:
    import datetime as _dt
    p = log_path()
    if not p:
        return
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"[{_dt.datetime.now():%H:%M:%S}] [crash] {text}\n")
    except Exception:
        pass


def _hook(etype, value, tb):
    text = "".join(traceback.format_exception(etype, value, tb))
    _write_log("未捕获异常：\n" + text)
    try:
        sys.__excepthook__(etype, value, tb)
    except Exception:
        pass
    global _dialog_quota
    with _lock:
        if _dialog_quota <= 0:
            return
        _dialog_quota -= 1
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            head = f"{etype.__name__}: {value}"
            QMessageBox.critical(
                None, "程序遇到错误",
                "发生未处理的异常（已写入日志）：\n" + head +
                "\n\n日志位置：" + (log_path() or "（未知）"))
    except Exception:
        pass


def install() -> None:
    """在 QApplication 创建前后均可；重复调用无副作用。"""
    sys.excepthook = _hook
