# FNIRSI 1013D 串口 AI 控制套件 · AI-Controlled FNIRSI 1013D Oscilloscope

> 📖 **新手从 [`docs/GETTING-STARTED.md`](docs/GETTING-STARTED.md) 开始**：开箱 → 刷机 → 认识界面 → 让 AI 接管 → AI 能拿到什么 → 实战演示 → 排错。

> 🧰 **每个软件怎么用（图文并茂，含刷固件用的 `FNIRSI_Firmware_Loader.exe`）见 [`docs/SOFTWARE-GUIDE.md`](docs/SOFTWARE-GUIDE.md)**；预编译固件与刷机工具都在 **Releases**。

> 🐣 **完全零基础**看 [`docs/BEGINNER.md`](docs/BEGINNER.md)；报错查 [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)；想懂原理看 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)；给 AI 调用见 [`docs/AI-USAGE.md`](docs/AI-USAGE.md)。
> ⚡ **一键跑通**：Windows `powershell -ExecutionPolicy Bypass -File .\run.ps1`；macOS/Linux `bash run.sh`（装依赖→自检→读数→截图）。

> 让 AI / 程序**直接操作**一台 FNIRSI / ADS 1013D 平板示波器，并读取**与屏幕逐字一致**的测量值、波形和整屏截图，实现"边看波形边调硬件"的自动化测试闭环。
> Give an AI / script full control of a FNIRSI 1013D scope, and read measurement values, waveform samples and the whole LCD screen that match the scope's own display *verbatim*.

本项目包含：**改造版固件源码**、**串口协议**、**Python 控制库 + 桥接 + 截图**、**AI Skill 包**、**MCP server**，以及刷机/编译/使用文档。
This repo ships: the **modified scope firmware source**, the **serial protocol**, **Python control library / bridge / screenshot tools**, an **AI Skill package**, an **MCP server**, plus flashing / build / usage docs.

---

## 1. 硬件与芯片 · Hardware & SoC

| 项 Item | 值 Value |
|---|---|
| 示波器型号 Model | **FNIRSI / ADS 1013D**（双通道，100 MHz，平板示波器）|
| 主控 SoC | **Allwinner F1C100S**（ARM926EJ-S，`-mcpu=arm926ej-s`，主频 408 MHz）|
| 屏幕 LCD | **800 × 480，RGB565**，帧缓冲 `maindisplaybuffer` 常驻 RAM = 768,000 字节 |
| 波形采集 FPGA | 128 MB/s 采样（FPGA 侧原始 ADC 码 0..255，128 = 通道零点）|
| USB | CDC 虚拟串口，`VID 0x0483 / PID 0x5740`，波特率 **2,000,000**，8N1 |
| 改造固件版本串 | `V1.3.0C MOD V9B4`（`#VER` 返回）|

---

## 2. 能力一览 · What it can do

- **控制**：运行/停止/单次/自动量程、时基、电压档、耦合、探头倍率、通道开关、触发电平/边沿/源、界面中英文、模拟按键。
- **读数**：`#MEAS` 直接回传**屏幕测量值原值**（12 项：Vmax/Vmin/Vavg/Vrms/Vpp/Vp/Freq/Cycle/Time±/Duty±），与屏幕逐字一致。
- **波形**：`#WAV2,3` 抓 3000 点采样 + 320 字节系统参数，本地重绘出与屏幕同口径的波形 PNG。
- **整屏截图**：`#SHOT` 抓取**真实 LCD 画面**（含菜单/文字/UI/波形）→ PNG（见下文"截图已知限制"）。
- **AI 接口**：CLI、Python 库、AI Skill 包、MCP server 四种调用方式。

---

## 3. 快速开始 · Quick start（人类用户）

```bash
# 1) 装依赖
pip install pyserial pillow

# 2) 刷入改造固件（见 docs/FLASHING.md）；不想自己编译，可直接从 Releases 下载 fnirsi_1013d_control.bin。菜单里打开 USB-Serial mode（见同文档）

# 3) 验证连通（设备管理器里 COM 号按实际改，下列以 COM26 为例）
python host/scope_ai.py status          # 设置+波形+测量 完整 JSON
python host/scope_ai.py meas-stable     # ★推荐读数：先冻结再读，不波动

# 4) 出图
python host/render_wave.py              # 还原波形图 → 当前波形.png
python host/scope_ai.py shot 截图.png   # 抓真实 LCD 整屏（需 #SHOT 固件）
```

> 端口自动查找：脚本按 `VID 0x0483` 自动识别串口，无需手填 COM 号（见 `host/scope_ai.py`）。

---

## 4. 目录结构 · Repo layout

```
fnirsi-1013d-scope-ai/
├── README.md                 本文档
├── LICENSE                   MIT（含上游 Atlan4 致谢说明）
├── .gitignore
├── firmware/                 改造版固件源码与构建
│   ├── src/                  Scope v0.28a 源码（含新增 #MEAS/#SHOT 的 usb_interface.c、scope_control.c）
│   ├── build_firmware.ps1    GCC14 arm-none-eabi 全量编译脚本
│   └── pack_firmware.py      mksunxi + flashfilepacker 复刻打包
├── host/                     上位机 Python（库/桥接/截图/工具/自检）
├── skill/                    AI Skill 包（SKILL.md + PROTOCOL.md + 同名脚本）
├── mcp/                      MCP server + 各客户端配置示例
├── docs/                     FLASHING / AI-USAGE / CHANGELOG / 协议改动等专章
└── assets/                   屏幕实测.png、界面重建.html 等展示素材
```

---

## 5. 串口协议 · Protocol（摘要）

命令以 `#` 开头、`\n` 结尾，应答单行文本（`#WAV2` 为长行，`#SHOT` 头后跟二进制）。完整字段/偏移/档位表见 [`skill/PROTOCOL.md`](skill/PROTOCOL.md)。

| 命令 | 作用 |
|---|---|
| `#VER` / `#INFO` | 固件版本 / 型号 |
| `#MEAS[,CH1\|CH2]` | 屏幕测量值原值（**读前先 `#SET,STOP`** 才不波动）|
| `#WAV2,3` | 一帧完整波形：320B 参数 + CH1/CH2 各 3000 点 |
| `#SHOT` | 整屏 RGB565 帧缓冲二进制（768000B）|
| `#READ` | 320 字节 SysParam（HEX）|
| `#SET,RUN/STOP/SINGLE/AUTOSET` | 运行控制 |
| `#SET,TDIV,<idx>` / `#SET,CHx,VDIV,<idx>` | 时基 / 电压档 |
| `#SET,CHx,ON/OFF/AC/DC/PROBE/POS` | 通道配置 |
| `#SET,TRIG,CHx/AUTO/NORMAL/SINGLE/RISE/FALL/LEVEL` | 触发配置 |
| `#SET,LANG,CN/EN` | 界面语言 |
| `#KEY,<K>` | 模拟按键 |

> **读数铁律**：RUN 态信号边采边变，读数会跳。读 `#MEAS` / 抓 `#WAV2` 前先 `#SET,STOP` 冻结，STOP 后连读 3 次逐字一致。`meas_stable()` / `capture_stable()` 已把这条规则做进代码。

---

## 6. 给 AI 怎么调用 · How an AI calls this

三种推荐路径，详见 [`docs/AI-USAGE.md`](docs/AI-USAGE.md)：

1. **MCP（最适合 Claude / Cursor 等支持 MCP 的客户端）**
   ```json
   {
     "mcpServers": {
       "fnirsi-scope": {
         "command": "python",
         "args": ["<绝对路径>/mcp/server.py", "--port", "COM26"]
       }
     }
   }
   ```
   AI 即可看到 `scope_status / scope_measure / scope_set_* / scope_run / scope_stop / scope_capture / scope_screenshot` 等工具直接调用。

2. **Skill 包**：把 `skill/` 交给 AI（`SKILL.md` 含选择指引与规则），AI 用 `python scope_ai.py <子命令>` 即可。

3. **CLI / 库**：`scope_ai.py` 子命令，或 `from scope_ai import ScopeAI` 编程调用。

---

## 7. 截图已知限制（如实说明）· Screenshot caveat

`#SHOT` 实机可正常出图，**颜色 R/B 通道正确、画面上下不颠倒**（均已肉眼核对）。但稳定少收最后 2048 字节（`got=765952`）。
调试结论：该现象与"分包大小 / 排空重试 / 发送超时 / 收尾等待 TX"**均无关**（逻辑不同的多版固件丢的都是同一个 `765952 = 1496 × 512`），故**并非"数据量太大需要分包"**——固件一直分包。上位机 `_read_binary_idlean` 已"无新数据即停、缺失按黑补齐"，画面其余真实可用。根治需进一步定位（计划用固件侧字节计数诊断命令 `#SHOTS,<sent>` 区分"设备没发满 / USB 端点 / 主机 usbser"哪一层丢）。
`#SHOT` works and the colors/orientation are verified correct, but the last 2048 bytes are stably missing. Experiments proved this is **not** a packet-size/timeout/flush issue, so it is **not** a "need to split packets" problem (the firmware already splits). The host fills the missing ~1.3 bottom rows with black and the rest of the image is authentic. A firmware-side byte counter (`#SHOTS,<sent>`) is the planned next step to localize the loss.

---

## 8. 上游与许可 · Upstream & License

- 本固件基于社区开源 **Atlan4** FNIRSI 1013D 改造固件（见 `LICENSE` 与 `docs/` 内论坛/GitHub 链接）。FatFs、字库等第三方组件各自遵循其原始许可。
- 本项目 muqiu2026 的改动与配套工具采用 **MIT License**（详见 `LICENSE`）。
- 刷机有**变砖风险**，请自行先做备份，按 `docs/FLASHING.md` 操作。刷写第三方固件可能影响保修。

---

*If this saved you time, a ⭐ is appreciated. Issues & PRs welcome.*
