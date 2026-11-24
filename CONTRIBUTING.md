# Contributing · 贡献指南

Thanks for helping improve this project! 欢迎提 Issue 和 Pull Request。

> TL;DR: fork → 新建分支 → 改 → 本地自检 (`python mcp/test_server.py`、`python skill/verify_protocol.py`) → 提 PR。CI 会自动跑无硬件自测。

## 1. 怎么提问题 (Issues)
请尽量给出：
- 示波器型号 + `#VER` 返回的固件版本串
- 你跑的完整命令 / 报错信息
- 现象（能否复现、`#INFO` 是否为 `#OK,1013D`、端口能否打开）

## 2. 开发环境
```bash
pip install -r requirements.txt     # pyserial, pillow
```
- 固件编译需 **GCC arm-none-eabi**（本项目用 14.2，`-mcpu=arm926ej-s`）。
- 打包用 `firmware/pack_firmware.py`（复刻 mksunxi + flashfilepacker）。

## 3. 改固件 (`firmware/`)
1. 源码在 `firmware/src`（基于上游 Atlan4 Scope v0.28a）。
2. 新增串口命令：在 `usb_interface.c` 加 `#` 路由，转 `scope_control_dispatch`；命令处理写进 `scope_control.c`（注意固件无 libc `strncmp`，用 `cprefix` 前缀比较）。
3. 编译：`powershell -File firmware/build_firmware.ps1` → 打包：`python firmware/pack_firmware.py build`。
4. **刷机有风险**，务必先备份；提交时不要携带 `.bin/.img`（见 `.gitignore`，二进制走 Release）。
5. 改了命令，请同步更新 `skill/PROTOCOL.md` 与 `docs/GETTING-STARTED.md`、`docs/AI-PROMPT.md`。

## 4. 改上位机 / Skill / MCP
- 上位机在 `host/`，发布自洽副本在 `skill/`（两者 `scope_ai.py` 需保持一致，PR 时一并改）。
- MCP server 在 `mcp/server.py`，是**自包含**实现（只依赖 pyserial）。新增工具时：
  1) 在 `TOOLS` 加 schema；2) 在 `_call_tool` 加路由；
  3) 在 `mcp/test_server.py` 加无硬件用例（注入 `FakeScope`）。
- 务必保证 `python mcp/test_server.py` 全通过（CI 也跑这个）。

## 5. 代码 & 提交规范
- Python: PEP8、4 空格；命令/协议改动**必须实测或加测试**，不写未验证的功能。
- Commit 用清晰动词开头（如 `feat: add #FOO`、`fix: meas-stable freeze`）。
- 文档改动尽量中英同步（README / README_EN、AI-USAGE / AI-PROMPT）。

## 6. 诚实与安全原则（重要）
- **不夸大**：已知限制要写清（例如 `#SHOT` 稳定少 ~2048 字节，并非“分包”问题）。
- **不造假截图/数据**：示例图必须来自真实运行。
- 刷机/写物理扇区类改动务必在文档里标注**变砖风险**与备份要求。

## 7. 发布
固件与刷机工具二进制通过 **Releases** 分发（不在 git 树里），打 tag 如 `firmware-vX`、`loader-vX`，附 sha256。

— Maintainer: @muqiu2026
