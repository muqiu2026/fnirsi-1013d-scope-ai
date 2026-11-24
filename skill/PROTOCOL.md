# FNIRSI / ADS 1013D 示波器 串口控制协议 v2

> 用途：让 AI / 程序通过 USB 串口直接**操作**示波器并**读取与屏幕逐字一致**的数据，
> 边看波形边调试硬件。配合改造版固件（含 `#MEAS`）使用。

## 0. 物理层

| 项 | 值 |
|---|---|
| 接口 | USB CDC 虚拟串口（设备插入后自动枚举）|
| 端口 | Windows 形如 `COM26`；按 VID 查找：`vid==0x0483` |
| 波特率 | **2000000**（其余常用值也兼容：921600/460800/115200）|
| 数据位/停止/校验 | 8N1，流控无 |
| 行尾 | 命令与应答均以 `\n` 结束 |
| 前提 | 示波器菜单需开启 **USB-Serial mode**；串口为独占资源，同一时刻只能一个程序占用 |

## 1. 命令总表

所有命令以 `#` 开头，大小写敏感项见注。应答单行文本（`#WAV2` 为长行）。

### 1.1 读类

| 命令 | 应答 | 说明 |
|---|---|---|
| `#VER` | `V1.3.0C MOD V9B4\n` | 固件版本 |
| `#INFO` | `#OK,1013D\n` | 型号 |
| `#READ` | `#OK,<b0>,<b1>,...<b319>\n` | **320 字节 SysParam**，每字节两位大写 HEX，逗号分隔。字段偏移见 §3 |
| `#MEAS` | `#MEAS,CH1,K=v,K=v,...\n[#MEAS,CH2,...\n]` | **屏幕测量值原值**（固件用与屏幕同一函数算好后直接回传，逐字一致，含单位）。无使能通道时 `#ERR,MEAS` |
| `#MEAS,CH1` / `#MEAS,CH2` | 单通道一行 | 指定通道 |
| `#WAV2,3` | `#OK,<320B参数HEX>,<CH1数据HEX>,<CH2数据HEX>\n` | 一帧完整波形（约 12600 字节 ASCII）|
| `#SHOT` | `#SHOT,800,480,2,768000\n` + **768000 字节二进制 RGB565 帧缓冲** | **整屏截屏**：当前 LCD 真实画面（含菜单/文字/波形）。二进制非文本，需按字节数读满 |

`#MEAS` 键序固定（12 项，与固件 `meas_keys` 一致，规避固件 `measurement_names` 里 `Freg` 笔误）：

```
Vmax,Vmin,Vavg,Vrms,Vpp,Vp,Freq,Cycle,Time+,Time-,Duty+,Duty-
```

> ⚠️ **测量值口径**：要让读数 == 屏幕显示，必须用 `#MEAS`（固件原值）。
> 不要用 `#WAV2` 的采样自己算 RMS/Vavg —— 屏幕的 `Vavg/Urms` 经过固件内部
> `dcoffset / adc1compensation / dc_shift_size` 校准链（这些量不在 320B SysParam 里），
> 纯采样反算跨档位会有偏差。**本固件 `#MEAS` 已规避该问题。**

> ⚠️ **读数规则（务必遵守）**：**读 `#MEAS` 前先发 `#SET,STOP`**，否则 RUN 态下信号
> 边采边更新，读数会跳动。STOP 冻结后实测连读 3 次 `#MEAS` 逐字一致。
> 库方法 `ScopeAI.meas_stable()` 与 CLI `python scope_ai.py meas-stable` 已把这条规则内置
> （自动 STOP→读→可选恢复），应作为默认读法。

> ℹ️ **AUTOSET**：`#SET,AUTOSET` 需设备处于 RUN 态且内部做自动量程分析，
> 耗时可 >1s。`ScopeAI.autoset()` 已自动先发 `#SET,RUN` 并以 8s 超时等待 `#OK,AUTOSET`。

### 1.2 控制类（`#SET` / `#KEY`）

`#SET,<...>` 成功回 `#OK,...`，未知回 `#ERR,SET`。常用：

| 命令 | 含义 |
|---|---|
| `#SET,RUN` / `#SET,STOP` / `#SET,SINGLE` / `#SET,AUTOSET` | 运行 / 停止 / 单次 / 自动设置 |
| `#SET,TDIV,<idx>` | 时基档（0..34，见 §4 表）|
| `#SET,CH1,VDIV,<idx>` / `#SET,CH2,VDIV,<idx>` | 电压档（0..6，见 §4 表）|
| `#SET,CH1,ON` / `OFF` | 通道开关 |
| `#SET,CH1,AC` / `DC` | 耦合 |
| `#SET,CH1,PROBE,<row>` | 探头倍率行（0:.5x 1:1x 2:10x 3:20x 4:50x 5:100x 6:1000x）|
| `#SET,CH1,POS,<y>` | 通道垂直位置（屏幕 Y，约 6..396）|
| `#SET,TRIG,CH1` / `CH2` | 触发源 |
| `#SET,TRIG,AUTO` / `NORMAL` / `SINGLE` | 触发模式 |
| `#SET,TRIG,RISE` / `FALL` | 触发边沿 |
| `#SET,TRIG,LEVEL,<y>` | 触发电平（屏幕 Y）|
| `#SET,LANG,CN` / `EN` | 界面中英文切换 |
| `#KEY,<K>` | 模拟按键 |

> ℹ️ RUN/STOP 的切换命令有效（回 `#OK,RUN`/`#OK,STOP`），但实测**该状态不反映在 320B SysParam 的字节里**。判断运行态请记"自己最后发的命令"，或用波形帧数是否在增长间接判断；**不要**从 SysParam 臆测运行位。

## 1.5 `#SHOT` 截屏解码

固件 `usb_CDC_send_data` 把字节写进 32KB 环形缓冲、靠 `usb_CDC_in_ep_callback()` 排空，
故对 768000 字节按 **16KB 分块**发送、每块后 flush 一次（见 `usb_interface.c` 的 `#SHOT` 分支）。
主机侧：先读一行 ascii 头 `#SHOT,800,480,2,768000`，再**按 768000 字节读满原始二进制**
（不能用按行文本读法）。帧缓冲 `maindisplaybuffer` 是 **RGB565 / 小端 / 标准 R5G6B5**
（由 `display_lib.c` 的 `fg_color=(color&0xF80000)>>8 | (color&0xFC00)>>5 | (color&0xF8)>>3` 确认）。
解码：`v = lo|(hi<<8); R=(v>>11)&0x1F; G=(v>>5)&0x3F; B=v&0x1F`，转 8bit 时按 `<<3|>>2` 等扩展。

库已封装 `ScopeAI.shot(out_path)` → 直接存 PNG（`scope_ai.py shot [输出.png]`）。
⚠️ **实机首次使用请肉眼核对颜色 R/B 通道、画面上下方向**；若反了改位序或
`Image.transpose(FLIP_TOP_BOTTOM)`，属一次性校准，校准后固化。

## 2. `#WAV2,3` 帧解码

格式：`#OK,` + 三段逗号分隔的连续 HEX 字符（每 2 个字符 = 1 字节）。

- 第 1 段：320 字节 SysParam（640 个 HEX 字符）→ 见 §3。
- 第 2 段：CH1 采样，**4801 字节**，前 **3000** 点是真实采集缓冲，后 1801 字节是 `0x80`（中间电平）填充，**必须丢弃**。
- 第 3 段：CH2 同结构。
- 采样为 FPGA 原始 ADC 码 0..255，`128 = 该通道零点`。换算电压用 §3 的 `ch_scale`：`V = (adc-128) * scale*8/256`（scale = 每格 V/div）。

稳定抓帧要点：
- **RUN 态连续抓帧会撕裂**（边采边写），频率等会误报。要稳定数据请 **先 `#SET,STOP` 再抓**，STOP 后连抓多帧逐字节相同。
- 连续抓帧要**节流**（建议 ≥0.12s/帧）。固件主循环同步打包数千点 HEX，请求过密会占满主循环、拖慢采集与刷屏，**表现为屏幕跳闪**。

> ⚠️ **`#WAV2` 每点时间 ≠ SysParam `sample_dt`（实测重要发现）**：3000 点缓冲相对屏幕窗口是**另一套抽取**。实测某 1ms/div、`Freq≈1.0kHz` 帧里一个周期 = **500 点** → 每点 ≈ **1.8us**；而 `sample_dt`(100KSa/s)=10us，差约 **5.5 倍**。所以**不能用 `sample_dt` 给缓冲标时间/算频率**。
> 正确口径：**每点秒 = `#MEAS Cycle` / 实测每周期点数(上升沿中位)**。库已提供 `buffer_per_point()` / `period_samples()`；`capture()` 本地估算与 `render_wave.py` 横轴均改用此口径（已验证本地 freq 与 #MEAS 一致）。
> 澄清：SysParam 的 `timebase` 文本与 `timebase_sec`(0.001↔1ms/div) 本身一致，**不是**下标 bug；错的只是把 `sample_dt` 误当 `#WAV2` 每点时间。

## 3. SysParam(320B) 关键字节偏移

（与固件 `PC_interface.h` 对齐；`u(i)` = 第 i 字节）

| 偏移 | 含义 |
|---|---|
| 0 | 1=RUN, 0=STOP（**注意：实测 RUN/STOP 不一定更新此字节，勿作唯一依据**）|
| 2 | CH2 使能（==1 开）|
| 20 | `time_zoom = 34 - timeperdiv`，反查时基索引 `tdi=34-u(20)` |
| 64 | 触发源 0=CH1 1=CH2 |
| 65 | 触发模式 0=AUTO 1=NORMAL |
| 66 | 触发边沿 0=RISE 1=FALL |
| 72 | 触发电平 0..255（128=中心）|
| 74 | CH1 `volt_zoom = 4 + 电压档(0..6)` |
| 80,81 | CH1 垂直位置（小端 16bit）|
| 116 | CH1 耦合 0=DC 1=AC |
| 117 | CH1 探头 0/1/2（1x/10x/100x 约定）|
| 140 | CH2 `volt_zoom` |
| 148,149 | CH2 垂直位置 |
| 180 | CH2 耦合 |
| 181 | CH2 探头 |

电压档换算：探头字段 0/1/2 → VDIV 表行 1/2/5；列 = `volt_zoom-4`。
采样率由时基索引经固件表 `TIME_PER_DIV_SR_IDX → SAMPLE_RATE_HZ` 得到，**不可用时基×格数÷点数反推**。

## 4. 档位表

时基 `TDIV_TEXT[0..34]`：
```
0-10(长): 50s 20s 10s 5s 2s 1s 500ms 200ms 100ms 50ms 20ms
11-34(短):200ms 100ms 50ms 20ms 10ms 5ms 2ms 1ms 500us 200us 100us 50us 20us 10us
          5us 2us 1us 500ns 200ns 100ns 50ns 20ns 10ns 5ns
```
电压 `VDIV_TEXT[探头行][0..6]`（行 1 = 1x 探头）：
```
1x: 5V 2.5V 1V 500mV 200mV 100mV 50mV
10x:50V 25V 10V 5V 2V 1V 500mV
```

## 5. 直接调用（无需库）

```python
import serial,time
s=serial.Serial("COM26",2000000,timeout=1.0)
def q(c):
    s.reset_input_buffer(); s.write((c+"\n").encode()); s.flush()
    time.sleep(0.1); b=b""; t=time.time()
    while time.time()-t<1.0:
        if s.in_waiting:
            b+=s.read(s.in_waiting)
            if b"\n" in b: break
        time.sleep(0.005)
    return b.decode("ascii","ignore").strip()
print(q("#MEAS"))       # 与屏幕一致的测量值
print(q("#SET,TDIV,17"))# 设 1ms/div
```

## 6. 推荐通过封装库调用（见 SKILL.md）

- `scope_ai.py`（双向控制库 + CLI，含 `meas()`）
- `scope_bridge.py`（串口↔网页桥 + 实时 `live_scope.txt` 给 AI 读）
