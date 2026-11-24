---
name: scope-serial-control
description: 通过 USB 串口(COM26 @2000000)操作并读取 FNIRSI/ADS 1013D 改造版示波器。可设时基/电压档/触发/开关通道、运行停止，并读取【与示波器屏幕逐字一致】的测量值(#MEAS)和波形采样(#WAV2)。用于边看波形边调试硬件、自动化测量校验、产线自检。当用户提到"操作示波器""读示波器波形/测量值""1013D 串口"时使用。
---

# 示波器串口控制 Skill

让 AI 直接驱动 FNIRSI / ADS 1013D 示波器：改设置 → 采集 → 读数 → 再调整，
并保证读到的测量值就是屏幕上显示的值。

## 前置条件（重要）

1. 示波器已刷入**含 `#MEAS` 与 `#SHOT` 的固件**（`固件_新增SHOT可截屏.bin`，
   是 `#MEAS` 版的超集）。若仍是旧 `#MEAS` 版，`#SHOT` 返回 `#ERR,UNKNOWN`，
   其余命令照常可用。未刷 `#MEAS` 则该功能同样回退。
2. 菜单开启 **USB-Serial mode**。
3. 串口独占：先关闭任何占用 COM26 的程序（其它串口监视/桥接器）。
4. 安装 pyserial：`pip install pyserial`。

## 怎么选

- **要"读数 == 屏幕显示"** → 用 `meas-stable`（先 STOP 冻结再读 #MEAS，不波动，**推荐**）。
- **要波形形状 / 毛刺 / 做 FFT 等** → 用 `capture` / `capture-stable`（采样数组）。
- **要"一句话把屏幕调到目标信号"** → 用 `view`。
- **给人看 + 同时给 AI 读文件** → 跑 `scope_bridge.py`，浏览器看 http://localhost:8765，AI 读同目录 `live_scope.txt`。
- **要一张"当前屏幕完整波形"的图片** → `python render_wave.py`，生成 `当前波形.png`（冻结读 #WAV2，按 V/div 纵向、按【实测每点时间】给真实 ms 横轴）。
- **要液晶屏真实画面（含菜单/文字/UI，不只是波形）** → `python scope_ai.py shot`（需含 `#SHOT` 固件）。`render_wave` 是用 `#WAV2` 采样点 + `#MEAS` 重绘的还原图，而 `shot` 直接抓 `maindisplaybuffer` 屏幕像素，二者不同。

## ⭐ 读数铁律：读之前先 STOP（冻结帧不波动）

RUN 态下信号边采边更新，#MEAS/波形每帧都在变，读到的会跳。
**正确姿势：先 `#SET,STOP`，等当前采集周期收尾，再读 `#MEAS`** —— 这时读数稳定，
且等于屏幕冻结那一瞬显示的值。实测 STOP 后连读 3 次 #MEAS **逐字一致**。
这条规则已**做进代码**：直接调 `meas_stable()` / `capture_stable()` / `frozen()` 即自动遵守。

## 一键自检（26 项，含全控制指令）

```bash
python verify_protocol.py      # 退出码0=全通过；打印 #MEAS 表供对照屏幕
```

## 用 CLI（最简单，subprocess 即可）

在本目录运行（脚本与本协议文档同级）：

```bash
python scope_ai.py meas-stable                  # ★推荐读数：先冻结再读(规则内置)
python scope_ai.py meas                         # 屏幕测量值(固件原值) JSON(不冻结)
python scope_ai.py meas CH1                   # 指定通道
python scope_ai.py status                     # 设置+波形+测量 完整 JSON
python scope_ai.py capture-stable             # 暂停→多帧校验→稳定采样(推荐)
python scope_ai.py set timebase 1ms           # 设时基
python scope_ai.py set vdiv CH1 500mV         # 设电压档
python scope_ai.py set channel CH2 on         # 开/关通道
python scope_ai.py set trigger CH1 AUTO RISE  # 设触发
python scope_ai.py run / stop / single / autoset
python scope_ai.py view "2kHz 3.3V 方波"      # 自动调到合适显示
python scope_ai.py language cn                # 界面切中文
python scope_ai.py report                      # AI 可读文本报告(屏幕值+采样估算)
python scope_ai.py shot                        # 截当前 LCD 整屏→屏幕截图.png(需#SHOT固件)
#   实测：真实 UI/颜色(R/B)/上下方向均正确；固件偶发丢最底部~2KB(≈1行),shot 已自动按黑补齐,不影响判断
python scope_ai.py shot 画面.png               # 指定输出文件名
```

## 库调用

```python
from scope_ai import ScopeAI
with_scope = ScopeAI()                       # 自动按 VID 0x483 找串口
m = with_scope.meas()                         # {'CH1':{'Vpp':'3.50V',...}, 'CH2':{...}}
d = with_scope.capture_stable()              # 含 'screen_meas' 与 'ch1_raw'/'ch2_raw'
with_scope.set_vdiv("CH1", "1V")
print(with_scope.report())                   # 屏幕原值为权威段 + 本地估算参考段
```

## 关键规则（务必遵守，否则会读到不一致/撕裂数据）

1. **读数先 STOP**：用 `meas_stable()`（而非裸 `meas()`）读测量值；RUN 态读会跳变。
2. **测量值只认 `#MEAS`**：屏幕的 Vavg/Urms 走了固件内部直流偏移与 ADC 补偿链，
   不在采样里。用采样自算会跨档位对不上（#MEAS 返回多少就显示多少，**与屏幕逐字一致**，
   即便屏幕上某个值看着"怪"也是忠实回显，不是上位机算错）。
3. **稳定帧先 STOP 再抓**：RUN 态连续抓 `#WAV2` 会撕裂（边采边写），频率误报。
   `capture_stable()`/`frozen()` 已自动处理。
4. **抓帧要节流(≥0.12s/帧)**：请求过密会占满固件主循环 → 屏幕跳闪。
   `scope_bridge.py` 已内置节流。
5. **RUN/STOP 不在 SysParam 里**：别从 `#READ` 的字节猜运行态；记自己发的命令或看帧号增长。
6. **采样换算**：ADC 0..255，128=通道零点，电压=(adc-128)×(vdiv×8/256)；每段 4801 字节取前 3000 点。
7. **AUTOSET 需 RUN 且耗时**：`autoset()` 会先 `#SET,RUN` 再发 `#SET,AUTOSET` 并等最多 8s，
   否则容易误判成"无应答"。

完整协议（命令表、#WAV2 帧格式、SysParam 偏移、档位表）见 `PROTOCOL.md`。

## 故障速查

| 现象 | 原因 / 处理 |
|---|---|
| `#MEAS` 返回 `#ERR,UNKNOWN` | 固件未含 MEAS → 刷 `固件_新增MEAS可直读屏幕值.bin` |
| 打不开 COM26 / PermissionError | 端口被占用 → 关闭其它串口程序 |
| 屏幕跳闪 | 抓帧太频繁 → 增大帧间隔 / 用 bridge 的节流 |
| 读数与屏幕不符 | 在用采样自算 → 改用 `meas`(#MEAS 原值) |
| 频率/形状乱 | RUN 态抓的撕裂帧 → 用 `capture_stable()` |
