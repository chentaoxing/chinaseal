# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 chentaoxing <chentaoxing@gmail.com>
# SPDX-License-Identifier: GPL-3.0-only
"""PyInstaller 打包入口（包外脚本，必须用绝对导入）。"""
from chinaseal.__main__ import main

if __name__ == "__main__":
    main()
