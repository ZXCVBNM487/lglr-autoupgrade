<div align="center">

# 🛰️ 星际猎人 · ADB 自动挂机助手

**原名《无尽的拉格朗日》，游戏改名后为《星际猎人》。一款基于 ADB + OCR 的安卓手游自动挂机工具，支持模拟器与真机双端。**

[![Python](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-0078D6.svg)](https://www.microsoft.com/windows)
[![Releases](https://img.shields.io/badge/releases-latest-orange.svg)](https://github.com/ZXCVBNM487/lglr-autoupgrade/releases/latest)

</div>

---

## 📌 功能简介

- **自动升级建筑 `autoup`**：周期性检测建造队列，队列有空位时自动补位升级；内置**掉线自愈**、**跨天自动日常**、**领取回执校验**，可长期无人值守挂机。
- **日常领取 `daily`**：任务面板一键领取（主线 / 委托 / 支线 / 赏金）+ 活动面板领取 + 邮件附件领取。
- **一体化 `all`**：`autoup` + `daily` 一键启动。
- **图形化测试仪 `tester`**：打包为 `星际猎人测试仪.exe`，可视化选择指令、实时日志与画面预览，适合不熟悉命令行的用户。
- **双端通用**：模拟器（MuMu 等）与真机（Android）均支持，所有坐标按设备分辨率自动等比缩放。

## 🖥 界面展示

图形测试仪（`tester.py` / `星际猎人测试仪.exe`）提供：

- 顶部设备模式下拉（`auto` / `phone` / `emulator` / `serial`），一键写回配置；
- 指令按钮：`check` / `status` / `shot` / `scan` / `daily` / `autoup` / `all`；
- 右侧实时画面预览与运行日志；
- 底部状态栏（空闲 / 运行中 / 完成耗时 / 异常退出码）。

> 更直观的截图见 [Releases](https://github.com/ZXCVBNM487/lglr-autoupgrade/releases/latest)。

## 📱 设备支持

| 类型 | 说明 |
| --- | --- |
| 模拟器 | MuMu 等，需开启 ADB 调试（常见端口 `16384` / `7555`） |
| 真机 | Android 设备，开启「USB 调试」（开发者选项） |

识别方式遵循「少靠坐标、多靠 OCR 文字识别」原则，降低分辨率变化导致的误触。

## ⚙️ 环境要求

- Windows 系统
- Python 3.12+（开发与测试使用 3.14）
- [ADB](https://developer.android.com/tools/adb)（Android Debug Bridge）
- 已授权调试的模拟器或真机

## 📥 安装

### 方式一：下载 exe（推荐新手）

前往 [Releases](https://github.com/ZXCVBNM487/lglr-autoupgrade/releases/latest) 下载 `星际猎人测试仪.exe`，双击即可使用，无需安装 Python。

### 方式二：源码运行

```bash
git clone https://github.com/ZXCVBNM487/lglr-autoupgrade.git
cd lglr-autoupgrade
pip install -r requirements.txt

# 图形界面
python tester.py

# 或命令行（查看全部子命令）
python lagrange_upgrade.py --help
```

## 🚀 命令行使用

`lagrange_upgrade.py` 支持以下指令：

| 指令 | 说明 |
| --- | --- |
| `autoup` | 自动升级建筑（循环挂机） |
| `daily` | 日常领取（任务 + 活动 + 邮件） |
| `all` | 一体化（`autoup` + `daily`） |
| `check` | 连接与运行环境自检 |
| `status` | 查看建造队列状态 |

**配置**：复制 `config.example.json` 为 `config.json`，按需填写：

- `device.mode`：`auto` / `phone` / `emulator` / `serial`
- `device.serial`：指定设备序列号（`mode=serial` 时生效）
- `adb_path`：adb 可执行文件路径（默认走系统 `PATH`）
- 通知方式（QQ 机器人 / 企业微信等，按需配置）

> 真实的 `config.json` **不会被提交到仓库**，请妥善保管其中的凭据。

## 🗂 项目结构

```
lglr-autoupgrade/
├── lagrange_upgrade.py     # 核心：autoup / daily / all / check / status
├── tester.py               # 图形化测试仪（PyInstaller 打包为 exe）
├── tester.spec             # 打包配置
├── build_exe.bat           # 一键打包脚本
├── adb_locator.py          # ADB 智能定位（被核心引用）
├── qq_official.py          # QQ 机器人推送（被核心引用）
├── config.example.json     # 配置模板（脱敏，不含任何凭据）
├── requirements.txt        # 依赖清单
├── refs/                   # 测试仪 UI 参考图标
├── tests/                  # 单元测试（35 项，无需设备 / OCR）
│   └── test_lagrange.py
├── tools/                  # 辅助 / 调试脚本（非核心，按需使用）
│   ├── adb_diag.py
│   ├── element_survey.py
│   ├── game_launcher.py
│   ├── lagrange_gui.py
│   ├── notifier_extra_channels.py
│   └── _probe_gui.py
├── docs/                   # 开发笔记
├── LICENSE                 # MIT 协议
├── CONTRIBUTING.md         # 贡献指南
└── README.md
```

## ⚠️ 免责声明

> 本项目仅供**学习与研究 ADB / OCR / UI 自动化技术**之用。
>
> 使用自动化工具操作游戏可能违反游戏运营方的《用户协议》，由此产生的账号封禁、数据丢失等风险**由使用者自行承担**。请勿用于商业用途、批量养号或破坏游戏公平性的行为。
>
> 项目作者不对任何因使用本工具导致的后果负责。

## 🤝 贡献

欢迎 Issue 与 Pull Request！提交前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

- **反馈问题**：请在 Issue 中附上运行日志与设备 / 模拟器信息；
- **提交代码**：保持核心逻辑（`lagrange_upgrade.py`）风格一致，新增功能请补充单元测试。

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 开源。

## 🙏 致谢

- [RapidOCR](https://github.com/RapidAI/RapidOCR) —— 游戏内文字识别（OCR）
- [Android Debug Bridge (ADB)](https://developer.android.com/tools/adb) —— 设备控制
- [March7thAssistant（三月七小助手）](https://github.com/moesnow/March7thAssistant) —— 同类 ADB / OCR 自动化项目的思路参考
