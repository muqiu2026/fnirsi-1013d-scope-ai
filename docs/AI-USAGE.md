# 给 AI 怎么调用 · How an AI / Agent calls this scope

目标：让任意 AI（Claude / GPT / Cursor / 自建 Agent）能"看一眼波形 → 调一个设置 → 再读一次"。提供**由简到全**四条路径。

## 物理前提

- 示波器已刷含 `#MEAS`/`#SHOT` 的改造固件，并已开启 **USB-Serial mode**（见 `docs/FLASHING.md`）。
- 电脑装好 `pyserial`（截图还需 `pillow`）。
- 端口自动识别（按 `VID 0x0483`）；手动指定时形如 `COM26` / `/dev/ttyACM0`，波特率 `2000000`。
- **串口独占**：同一时刻只能一个程序占用，调用前先关掉其它串口监视/桥接。

---

## 路径 1：MCP server（推荐给支持 MCP 的客户端）

`mcp/server.py` 把协议封装成标准 MCP 工具。Claude Desktop / Cursor 等配置（绝对路径换成你的）：

```json
{
  "mcpServers": {
    "fnirsi-scope": {
      "command": "python",
      "args": ["/ABS/PATH/mcp/server.py", "--port", "COM26"]
    }
  }
}
```

AI 将直接获得以下工具：

| 工具 tool | 作用 |
|---|---|
| `scope_info` | 型号 + 固件版本 |
| `scope_status` | 完整状态（档位/触发/测量/采样估算）JSON |
| `scope_measure` | 屏幕测量值（默认先 STOP 冻结再读）|
| `scope_set` | 改时基/电压档/耦合/探头/触发/通道/语言 |
| `scope_run` / `scope_stop` / `scope_single` / `scope_autoset` | 运行控制 |
| `scope_capture` | 抓一帧波形（3000 点 + 屏幕测量值）|
| `scope_screenshot` | 抓整屏 LCD → PNG |

---

## 路径 2：Skill 包（给 Claude Code / 支持 SKILL 的 Agent）

把 `skill/` 目录交给 AI。`skill/SKILL.md` 的 frontmatter 即触发说明，内含：选择指引、5 条必读规则、CLI/库用法、故障速查；`skill/PROTOCOL.md` 是完整协议。AI 只要会跑 `python scope_ai.py <子命令>` 即可。

---

## 路径 3：CLI（subprocess 即用，最低门槛）

```bash
python host/scope_ai.py meas-stable        # ★读数（先冻结，推荐）
python host/scope_ai.py status             # 完整 JSON
python host/scope_ai.py set timebase 1ms   # 设时基
python host/scope_ai.py set vdiv CH1 500mV
python host/scope_ai.py set trigger CH1 AUTO RISE
python host/scope_ai.py run / stop / single / autoset
python host/scope_ai.py view "2kHz 3.3V 方波"   # 一句话调到目标信号
python host/scope_ai.py report             # 人/AI 可读文本报告
python host/scope_ai.py shot 屏幕.png      # 整屏截图（需 #SHOT）
python host/render_wave.py                 # 还原波形图
```

---

## 路径 4：Python 库（编程集成）

```python
import sys; sys.path.insert(0, "/ABS/PATH/skill")
from scope_ai import ScopeAI
sc = ScopeAI()                       # 自动按 VID 找串口
m = sc.meas()                        # {'CH1': {'Vpp':'3.48V', ...}, 'CH2': {...}}
m = sc.meas_stable()                 # 先冻结再读，等于屏幕
d = sc.capture_stable()              # 含 screen_meas + ch1_raw/ch2_raw
sc.set_vdiv("CH1", "1V")
sc.set_trigger("CH1", "AUTO", "RISE")
print(sc.report())                   # 屏幕原值为权威段 + 本地估算为参考段
```

实时给 AI 读：跑 `python host/scope_bridge.py`，浏览器看 `http://localhost:8765`，AI 读同目录 `live_scope.txt`（顶部即"屏幕测量值·固件 #MEAS 原值(权威)"段，已内置节流防屏幕跳闪）。

---

## 喂给 AI 的规则（务必转达，否则会读错）

1. **读数/抓帧前先 STOP**：用 `meas_stable`/`capture_stable`（规则已内置），RUN 态读会跳。
2. **测量只信 `#MEAS`**：屏幕 Vavg/Vrms 走了固件内部直流偏置与 ADC 补偿链，采样自算跨档对不上。
3. **抓帧节流 ≥0.12s**：太密会占满固件主循环 → 屏幕跳闪。
4. **别用 SysParam 猜 RUN/STOP**：实测运行态不更新那 320B，看自己发的命令或帧号增长。
5. **AUTOSET 需 RUN 态**且可能 >1s，用 `scope_autoset`（已自动 RUN + 8s 超时）。
