# 贡献指南

感谢你关注本项目！在提交 Issue 或 Pull Request 前，请阅读以下约定。

## 问题反馈（Issue）

- 先运行 `check` / `status` 自检，并在 Issue 中附上输出日志；
- 说明设备类型（模拟器型号 / 真机型号）与分辨率；
- 如涉及坐标 / 识别错误，请附上相关界面截图，并**遮挡个人账号、基地布局等敏感信息**。

## 提交代码（PR）

1. Fork 本仓库，并基于 `main` 分支新建特性分支；
2. 保持核心逻辑 `lagrange_upgrade.py` 的代码风格与架构一致；
3. 新增或修改功能时，**务必补充 / 更新单元测试**：
   ```bash
   python -m unittest tests.test_lagrange -v
   ```
4. 确保语法检查通过：
   ```bash
   python -m py_compile lagrange_upgrade.py tester.py
   ```
5. 提交信息清晰说明「改了什么、为什么」。

## 安全与隐私

- **切勿**在 PR 中提交真实的 `config.json`、设备序列号、游戏账号或任何凭据；
- 涉及坐标 / 阈值改动，请统一走 `scale_box()` 等比缩放，禁止硬编码像素；
- 调试产物（截图、日志、构建目录 `build/`、`dist/`）已被 `.gitignore` 屏蔽，请勿强制添加。

## 代码风格

- Python 使用 4 空格缩进；
- 尽量将核心逻辑抽为纯函数，便于在无设备 / 无 OCR 环境下做单元测试。
