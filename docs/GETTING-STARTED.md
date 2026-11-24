# 完整上手教程 · From a brand-new scope to AI control

> 本文从**刚拿到一台示波器**开始，一步步走到**让 AI 接管它**。命令、应答、界面、菜单均为本项目实机验证后的真实内容（含 37/37 串口自检通过记录）。
> 阅读顺序：① 开箱确认 → ② 刷机 → ③ 认识新固件界面 → ④ 让 AI 接管 → ⑤ AI 能拿到什么 → ⑥ 实战演示 → ⑦ 排错。

---

## 0. 你需要准备

| 需要的东西 | 说明 |
|---|---|
| FNIRSI / ADS 1013D 示波器 ×1 | 双通道平板示波器，主控 **全志 F1C100S** |
| 一张已格式化为 FAT32 的 microSD 卡 | 示波器系统盘（固件写在卡内保留区，不破坏分区） |
| USB-A↔Micro/Type-C 数据线 ×1 | 必须是**能传数据**的线（充电线不行） |
| Windows / Linux / macOS 电脑 ×1 | 跑上位机与 AI |
| Python 3.8+ | `pip install pyserial pillow` |

---

## 1. 开箱确认（拿到新机器）

1. 开机，默认是**原厂固件**，界面可中文。
2. 用数据线连电脑，菜单里开 **USB Connection**：此时电脑会把它当成一个 **U 盘**（出厂默认是 U 盘模式，不是串口）。
3. 记下型号：**1013D**（后面 `#INFO` 会回 `#OK,1013D` 验证）。

> 这一阶段什么都还不用改，只是确认设备与数据线正常。

---

## 2. 刷机（写入本项目的改造固件）

改造固件相比原厂/上游多了 `#MEAS`（读屏幕测量值）和 `#SHOT`（截整屏）等串口能力。**完整刷机步骤见 [`docs/FLASHING.md`](FLASHING.md)**，刷固件工具 `FNIRSI_Firmware_Loader.exe` 的图文用法见 [`docs/SOFTWARE-GUIDE.md`](SOFTWARE-GUIDE.md)（该 exe 已打包在本仓库 **Releases**）。这里给要点：

1. **先备份**：SD 卡整盘镜像 + 原固件 .bin 各存一份（刷机有变砖风险）。
2. **构建固件**（可选，直接用 Release 里的 .bin 也行）：
   ```powershell
   powershell -File firmware/build_firmware.ps1   # GCC14 arm-none-eabi 全量编译
   python firmware/pack_firmware.py build          # 打包成 fnirsi_1013d_control.bin
   ```
   或直接从本仓库 **Releases** 下载 `fnirsi_1013d_control.bin`。
3. **Windows loader 法（推荐首次）**：菜单开 USB Connection → 管理员运行 `FNIRSI_Firmware_Loader.exe` → 设备选名为 **F1C100S** 的那个（**千万别选本地磁盘**）→ 选 .bin → Write to device。
4. **Linux FEL 法（备选）**：`sudo sunxi-fel -p write 0x7FFFFFE0 fnirsi_1013d_scope.bin exe 0x80000000`，或写卡 `sudo dd if=fnirsi_1013d.bin of=/dev/sdc bs=1024 seek=8`。
5. 刷完重新开机，进新 bootloader，**菜单里做一次基础校准（Calibration）**。

> 想退回原厂界面：**拔掉 SD 卡开机**即回原厂固件。

---

## 3. 认识新固件界面

屏幕 800×480。布局（见仓库 `assets/屏幕实测.png`，是 `#SHOT` 抓的真实屏）：

- **顶部菜单条**：MENU / 亮度 / RUN-STOP / CH1 耦合&档位 / CH2 耦合&档位 / ACQ / 采样率 / 触发 / 电池。
- **右侧按钮列**（用 CTRL 翻页）：CTRL / RUN-STOP / 自动 SET / CURSOR / 测量 / 保存 PIC / 保存 WAVE。
- **主区**：CH1 黄、CH2 青波形 + 网格。
- **底部黄底测量条**：Vmax/Vmin/Vavg/Vrms/Vpp/Freq/Cycle/Duty 等。

主要菜单分组（中英对照，**完整表见 [`docs/示波器菜单中英对照.txt`](示波器菜单中英对照.txt)**）：

| 分组 | 关键项 |
|---|---|
| 基本 | MENU 菜单 · RUN/STOP 运行停止 · AUTOSET 自动 · CTRL 翻页 · CH1/CH2 开关 |
| 通道 | Coupling DC/AC 耦合 · Probe 1X/10X/100X 探头 · V/div 档位 · Invert 反相 |
| 触发 | Trig Mode Auto/Single/Normal · Trigger level 电平 · Rising/Falling 边沿 · Source CH1/CH2 |
| 测量 | Vmax Vmin Vavg Vrms Vpp VP Freq Cycle Tim± Duty± |
| 存储 | SAVE PIC 截图 · SAVE WAVE 存波形 · Picture/Waveform View |
| 系统 | System settings · **USB Connection** · Calibration 校准 · UPDATE 升级 |

> 🔎 **本改造版新增的能力**：上游 Atlan 固件**只有英文界面**（原作者已确认）。本项目**新增了中文界面**，并支持运行时 `#SET,LANG,CN` / `#SET,LANG,EN` 切换（开机默认中文）。所以你在原厂/纯 Atlan 上找不到的中文切换，本固件有。

---

## 4. 让 AI 接管

### 4.1 打开串口模式（在示波器上做，一次即可）

1. **MENU → System settings（系统设置）→ Other settings（其他设置）**（找不到就按 **CTRL** 翻按钮页）。
2. 列表第 6 项 **USB-Serial mode** → 拨到 **ON**。
3. **关机再开机**（必须重启才生效）。
4. 菜单 → USB Connection 打开；电脑出现串口设备：Windows 设备管理器"端口(COM 和 LPT)"里出现 COMx（示例 COM26），Linux 是 `/dev/ttyACM0`。

> 仍没有口：换数据线/换 USB 口；论坛经验有时需同时打开下一行的 **Dev mode**。

### 4.2 装上位机并自检

```bash
pip install pyserial pillow
cd fnirsi-1013d-scope-ai
python skill/verify_protocol.py        # 一键全命令自检，全通过=OK（实测 37/37）
python host/scope_ai.py status         # 设置+波形+测量 完整 JSON
python host/scope_ai.py meas-stable    # ★推荐读数：先冻结再读，与屏幕一致
```

> 脚本按 `VID 0x0483` 自动找串口，一般不用手填 COM 号。

### 4.3 选一种"接入 AI"的方式（由简到全）

**A. CLI（任何能跑命令行的 AI / Agent 都能用）**
```bash
python host/scope_ai.py meas-stable            # 屏幕测量值
python host/scope_ai.py set timebase 1ms       # 改时基
python host/scope_ai.py set vdiv CH1 500mV
python host/scope_ai.py run / stop / single / autoset
python host/scope_ai.py shot 屏幕.png          # 抓整屏
```

**B. MCP server（推荐 Claude Desktop / Cursor 等）**
把下面填进 MCP 客户端配置（路径换成你的绝对路径）：
```json
{
  "mcpServers": {
    "fnirsi-scope": {
      "command": "python",
      "args": ["/ABS/PATH/fnirsi-1013d-scope-ai/mcp/server.py", "--port", "COM26"]
    }
  }
}
```
AI 立即获得工具：`scope_info / scope_measure / scope_set / scope_capture / scope_screenshot`（详见 [`mcp/README.md`](../mcp/README.md)）。

**C. Skill 包（支持 SKILL 的 Agent）**
把 `skill/` 目录交给 AI，`skill/SKILL.md` 内含选择指引、5 条必读规则、用法与故障速查。

---

## 5. AI 能获取/控制什么

### 5.1 能读到的信息（含真实应答示例）

| 能力 | 命令 | 真实返回示例 |
|---|---|---|
| 固件版本 | `#VER` | `V1.3.0C MOD V9B4` |
| 型号 | `#INFO` | `#OK,1013D` |
| 测量值(与屏幕逐字一致) | `#MEAS` | `#MEAS,CH1,Vmax=2.18V,Vpp=3.48V,Vavg=-3.64V,Freq=1.00kHz,Duty+=49.9%` |
| 320B 系统参数 | `#READ` | `#OK,` + 320 字节 HEX |
| 一帧波形 | `#WAV2,3` | `#OK,` 参数 + CH1 3000 点(+CH2) |
| 整屏真实画面 | `#SHOT` | `#SHOT,800,480,2,768000` + 768000B RGB565 |

### 5.2 能做的控制（实测 `#SET` 生效，参数区会变）

| 控制 | 命令示例 | 实测效果 |
|---|---|---|
| 运行/停止/单次/自动 | `#SET,RUN/STOP/SINGLE/AUTOSET` | 回 `#OK,...` |
| 时基 | `#SET,TDIV,18` | 参数区 byte20 `14→16` 改变 |
| 电压档 | `#SET,CH1,VDIV,3` | 回 `#OK,VDIV` |
| 通道开关/耦合/探头 | `#SET,CH2,ON`、`#SET,CH1,AC`、`#SET,CH1,PROBE,1` | 回 `#OK,...` |
| 触发 | `#SET,TRIG,CH1/AUTO/RISE/LEVEL,120` | 触发电平 byte72 `128→117` |
| 垂直位移 | `#SET,CH1,POS,100` | byte80:82 `183→100` |
| 界面语言 | `#SET,LANG,CN` / `EN` | 中/英切换 |
| 模拟按键 | `#KEY,F` / `#KEY,46` | 回 `#OK,KEY` |
| 未知命令 | `#FOO` | 回 `#ERR,UNKNOWN` |

> 完整命令表、`#WAV2` 帧格式、SysParam 偏移、档位表：见 [`skill/PROTOCOL.md`](../skill/PROTOCOL.md)。

### 5.3 AI 必须遵守的 3 条铁律
1. **读数/抓帧前先 `#SET,STOP`**（RUN 态边采边变会跳）；用 `meas_stable()`/`capture_stable()` 已内置。
2. **测量值只信 `#MEAS`**（屏幕 Vavg/Vrms 含固件内部校准链，采样自算跨档对不上）。
3. **抓帧节流 ≥0.12s**（太密占满固件主循环 → 屏幕跳闪）。

---

## 6. 实战演示：让 AI 自动测一个信号

> 场景：探头接了一个 1 kHz 方波，你要 AI 自动把它调清楚、读数、截图、给结论。

```python
import sys; sys.path.insert(0, "/ABS/PATH/fnirsi-1013d-scope-ai/skill")
from scope_ai import ScopeAI
sc = ScopeAI()
sc.autoset()                 # 自动量程（内部先 RUN 再等最多 8s）
m = sc.meas_stable()         # 冻结读屏幕值 → {'CH1': {'Vpp':'3.48V','Freq':'1.00kHz',...}}
sc.shot("demo.png")          # 抓真实 LCD 整屏
print(m)                     # 交给你的大模型：Vpp≈3.3~3.5V, Freq=1.00kHz, Duty≈50% → 判为 3.3V 时钟
```

把 `demo.png` + 这段 JSON 一起丢给带视觉的 AI，它就能"看着屏幕"判断信号是否正常。用 MCP 接入时，同样的步骤由 AI 自己调用工具完成。

---

## 7. 常见问题速查

| 现象 | 原因 / 处理 |
|---|---|
| 电脑只看到 U 盘，没有 COM 口 | 没开 USB-Serial mode / 没重启 → 见 §4.1 |
| 打不开 COM / PermissionError | 端口被占用 → 关掉其它串口程序（一次只允许一个） |
| `#MEAS` 回 `#ERR,UNKNOWN` | 固件未含 MEAS → 刷 Releases 的 `fnirsi_1013d_control.bin` |
| 屏幕跳闪 | 抓帧太频繁 → 加到 ≥0.12s/帧，或用 `scope_bridge.py`（内置节流） |
| 读数与屏幕不符 | 在用采样自算 → 改 `meas`(#MEAS 原值) |
| 波形/频率乱 | RUN 态抓到撕裂帧 → 用 `capture_stable()` |
| `#SHOT` 图片底部缺一点 | 已知稳定少 ~2048B≈1.3 行，已按黑补齐；颜色/上下方向实测正确（见 README §7） |

---

## 8. 安全与免责

- 刷机有**变砖风险**，务必先备份，逐级确认设备名（loader 直写物理扇区，选错毁盘）。
- 刷第三方固件可能影响保修，风险自负。
- 本项目固件基于社区 **Atlan4** 开源固件改造，详见 `LICENSE`。
