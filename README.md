# ChinaSeal · 中国篆刻印稿工坊

[![License: GPL-3.0-only](https://img.shields.io/badge/License-GPL--3.0--only-blue.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)]()
[![Python: 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)]()
[![Release: v0.2.2](https://img.shields.io/badge/Release-v0.2.2-orange.svg)](https://github.com/chentaoxing/chinaseal/releases)

Copyright © 2024-2026 chentaoxing <chentaoxing@gmail.com>, licensed under [GPL-3.0-only](LICENSE).

> 面向**实体篆刻生产流程**的数字印稿设计工具：输入文字 → 选字体 → 生成印面 → 镜像 → 毫米级真实尺寸 → 打印上石。
> 不是电子公章生成器。完全离线运行（检测更新/字体下载需联网）。

## 快速开始

双击 `ChinaSeal.exe` 即可（绿色免安装）。

**首用必做**：工具栏 →「打印校准页」→ 打印后用真实直尺核对 100mm 标尺是否为 100.0mm。
若不符，请在打印对话框里把缩放设为「实际大小 / 100%」再校准一次。

## MVP 功能

| 模块 | 说明 |
|---|---|
| 印文 | 1-9 字网格排版；「单行长条」模式不限字数（引首章/压角章） |
| 字体 | 扫描系统字体 + 「导入字体文件」支持 ttf/otf/ttc；**缺字检测**：缺字标红并阻止导出，杜绝静默替换 |
| 内置字体 | 霞鹜文楷（简繁全覆盖）、思源宋体（Noto Serif SC）、LXGW Seal 小篆（预览版，覆盖字数有限）——开源/免费商用授权；列表默认**隐藏可能有商用风险的系统字体**（方正/汉仪/华文/华康/文鼎/蒙纳等），勾选"显示全部字体"可恢复 |
| 字体下载 | 「下载并添加其他免费开源字体…」：从 GitHub 发布页拉取字体包（因软件体积限制不全部打包），下载到用户目录即点即用 |
| 关于 | 标题行「关于」菜单：软件版本、检测更新（github.com/chentaoxing/chinaseal）、作者信息 |
| 章形 | 方形（长宽联动）、长方形（独立可调）、正圆形（直径可调），5-200mm；内置常用尺寸预设 |
| 读序 | 现代横排（左→右）/ 传统竖读（右起，右上→右下→左上→左下）/ 回文环读（右上起逆时针）；画布可显示读序编号辅助核对 |
| 装饰 | 边框（0-5mm，阴刻时边栏与底连为一体）；田字格（仅预览辅助，不导出） |
| 刻式 | 阳刻（朱文，预览红字白底）/ 阴刻（白文，预览白字红底）；导出自动转黑白印稿 |
| 微调 | 鼠标拖动单字位移；选中后滑杆旋转 ±180°、缩放 50-200%；全部支持撤销/重做 |
| 镜像 | 水平镜像（刻制正解）+ 垂直镜像；预览与导出同步 |
| 输出 | **矢量 PDF**（字形以三次贝塞尔轮廓嵌入，毫米精确）+ **PNG**（写入 DPI 元数据）；A4 居中 + 虚线裁切框 |
| 打印 | 软件内调 Windows 打印对话框，按真实毫米尺寸输出 |
| 工程 | `.chinaseal` 文件保存/打开全部参数 |

## 打印精度的三条铁律

1. **首次使用先打校准页**，确认打印机 1:1。
2. 打印 PDF/PNG 时选择「实际大小 / 100% / Actual size」，**关闭"适合页面"缩放**。
3. PDF 是矢量+毫米坐标，打印精度优于位图；优先用 PDF。

## 技术要点（给后续开发者）

- 字形几何唯一来源：fontTools 轮廓提取（TrueType 二次贝塞尔按规则切分，PDF 侧精确升三次）；画布、PNG、PDF 三端共用同一套毫米坐标路径 → 预览即所得。
- 填充规则：Qt WindingFill / PDF nonzero，y 翻转下轮廓方向一致，汉字镂空正确。
- 测试：`pytest tests/`（39 项：读序/圆形布局/轮廓提取/PNG DPI/PDF/工程文件往返/GUI 全按钮链路冒烟）。
- 样张：`out/samples/`（已视觉验收）。

## 二阶段（未实现，已在需求共识中约定）

刻制安全检查（最小线宽 0.3mm/间距 0.2mm + 自动优化）、篆书字体调研与软件内下载器（仅限中国本土源）、拼版打印、多行长文、SVG 导出、AI 设计扩展点。

---
源码：`E:\Agent\Date\ZCode\workspace\ChinaSeal\` · 需求共识：`docs/需求共识文档.md`

---

## 版权与许可

Copyright © 2024-2026 chentaoxing <chentaoxing@gmail.com>. 源代码以 [GNU General Public License v3.0 only](LICENSE) 授权发布。
SPDX-License-Identifier: GPL-3.0-only。

## 第三方组件致谢

| 组件 | 用途 | 许可证 |
|---|---|---|
| [PySide6](https://doc.qt.io/qtforpython-6/) (Qt for Python) | GUI 框架 | LGPL-3.0-only |
| [fontTools](https://github.com/fonttools/fonttools) | TrueType/OpenType 字体轮廓解析 | MIT |
| [reportlab](https://www.reportlab.com/) | 矢量 PDF 导出 | BSD |
| [Pillow](https://python-pillow.org/) | PNG 元数据写入 | HPND / MIT-CMU |
| [pytest](https://docs.pytest.org/) | 测试框架（仅开发用） | MIT |
| [PyInstaller](https://www.pyinstaller.org/) | 打包为独立 EXE（仅构建用） | GPL-2-or-later + 例外 |

PySide6 (Qt) 以 LGPL-3.0-only 授权发布。本软件以 PyInstaller 打包为单目录绿色 EXE，Qt 的动态库以独立文件形式存在，符合 LGPL 关于“传播包含 LGPL 库的可执行作品”的要求。Qt 库源码及 LGPL 全文见 [Qt 官方](https://www.qt.io/licensing/)。

## 内置字体（chinaseal/fonts/）

| 字体 | 用途 | 许可证 |
|---|---|---|
| LXGW WenKai | 默认中文字体（简繁覆盖） | OFL-1.1 |
| Noto Serif CJK SC | 思源宋体 | OFL-1.1 |
| LXGW Seal | 小篆预览版 | OFL-1.1 |

下载字体（fonts_repo/）：9 款免费开源中文字体供软件内下载（思源黑体 / 得意黑 / 站酷快乐体 / 站酷小薇 / 站酷庆科黄油 / 马善政楷 / 龙藏 / 之芒行书 / 柳建毛草），均为 OFL-1.1。

## 仓库

- 主仓库：[github.com/chentaoxing/chinaseal](https://github.com/chentaoxing/chinaseal)
- 国内镜像：[atomgit.com/chentaoxing/chinaseal](https://atomgit.com/chentaoxing/chinaseal)（只读同步）

## 作者

chentaoxing <chentaoxing@gmail.com>

