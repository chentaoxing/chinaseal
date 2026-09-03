# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <chentaoxing@gmail.com>
# SPDX-License-Identifier: GPL-3.0-only
"""ChinaSeal 入口。

- `python -m chinaseal`：包上下文
- PyInstaller 打包走项目根的 launcher.py（绝对导入）
"""
import sys


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from chinaseal.core import crashlog
    crashlog.install()
    import threading

    def _thread_hook(args):
        crashlog._write_log("子线程异常：\n" + "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    import traceback
    threading.excepthook = _thread_hook

    app = QApplication(sys.argv)
    app.setOrganizationName("ChinaSeal")
    app.setApplicationName("ChinaSeal")
    from chinaseal.ui import MainWindow  # 绝对导入，包内包外都成立
    try:
        from chinaseal.core.resources import logo_path
        app.setWindowIcon(QIcon(logo_path()))
    except Exception:
        pass
    win = MainWindow()
    win.show()
    if "--smoke" in sys.argv:  # 打包验证用：显示窗口 6 秒后自退
        QTimer.singleShot(6000, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
