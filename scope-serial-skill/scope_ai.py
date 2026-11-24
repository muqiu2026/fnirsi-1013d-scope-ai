#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scope_ai.py - AI 可调用的示波器双向控制库

让 AI 不仅能"读"波形，还能"操作"示波器：设时基、电压档、触发、开关通道，
然后重新采集、分析，形成 设置 -> 采集 -> 分析 -> 再调整 的闭环。

依赖：改造版固件 fnirsi_1013d_control.bin（实现了 #SET 命令），pyserial。

给 AI 的核心方法：
    scope = ScopeAI()                      # 自动找串口
    scope.set_timebase("1ms")              # 设时基
    scope.set_vdiv("CH1", "500mV")         # 设电压档
    scope.set_channel("CH2", True)         # 开/关通道
    scope.set_trigger(source="CH1", mode="AUTO", edge="RISE")
    scope.run() / stop() / single() / autoset()
    scope.view("2kHz 3.3V 方波")            # 一句话让示波器调好显示
    data = scope.capture()                 # 采集 -> 结构化数据
    data = scope.capture_stable()          # 先暂停再采集，多帧校验，自动恢复（推荐）
    with scope.frozen():                   # 需要连续多次读数时用这个上下文
        a = scope.capture(); b = scope.capture()   # 两帧逐字节相同
    print(scope.report())                  # AI 可读报告

命令行（AI 用 subprocess 调用）：
    python scope_ai.py status              # 当前设置+波形数据 JSON
    python scope_ai.py meas                # 屏幕测量值(#MEAS 原值) JSON
    python scope_ai.py meas-stable         # ★推荐读数：先STOP冻结再读，不波动、与屏幕一致
    python scope_ai.py report              # 屏幕原值(权威)+采样估算(参考) 文本报告
    python scope_ai.py set timebase 1ms
    python scope_ai.py set vdiv CH1 500mV
    python scope_ai.py set trigger CH1 AUTO RISE
    python scope_ai.py view "2kHz 3.3V 方波"
    python scope_ai.py capture             # 输出完整波形 JSON
    python scope_ai.py capture-stable      # 暂停→3帧校验→恢复，输出稳定波形
    python scope_ai.py calibrate 2.18 -1.32   # 用屏幕 Umax/Umin 标定电压读数
    python scope_ai.py cal-clear          # 清除电压标定
    python scope_ai.py list                # 列出串口
"""
import sys, os, re, time, json, math
from contextlib import contextmanager

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("需要 pyserial: pip install pyserial")
    raise

# ---- 与固件一致的档位表 ----------------------------------------------------
# time_div_texts[35]: 索引 0..10 长时基, 11..34 短时基
TDIV_TEXT = [
    "50s","20s","10s","5s","2s","1s","500ms","200ms","100ms","50ms","20ms",
    "200ms","100ms","50ms","20ms","10ms","5ms","2ms","1ms","500us",
    "200us","100us","50us","20us","10us","5us","2us","1us","500ns",
    "200ns","100ns","50ns","20ns","10ns","5ns",
]
TDIV_SEC = []
for _t in TDIV_TEXT:
    if _t.endswith("ms"):   TDIV_SEC.append(float(_t[:-2]) * 1e-3)
    elif _t.endswith("us"): TDIV_SEC.append(float(_t[:-2]) * 1e-6)
    elif _t.endswith("ns"): TDIV_SEC.append(float(_t[:-2]) * 1e-9)
    elif _t.endswith("s"):  TDIV_SEC.append(float(_t[:-1]))

# volt_div_texts[7][7]: 行=探头倍率(0:.5x,1:1x,2:10x,3:20x,4:50x,5:100x,6:1000x)
VDIV_TEXT = [
    ["2.5V","1.25V","500mV","250mV","100mV","50mV","25mV"],
    ["5V","2.5V","1V","500mV","200mV","100mV","50mV"],
    ["50V","25V","10V","5V","2V","1V","500mV"],
    ["100V","50V","20V","10V","4V","2V","1V"],
    ["250V","125V","50V","25V","10V","5V","2.5V"],
    ["500V","250V","100V","50V","20V","10V","5V"],
    ["1kV","500V","200V","100V","50V","25V","10V"],
]
def _vparse(s):
    s = s.strip().upper()
    if s.endswith("MV"): return float(s[:-2]) * 1e-3   # mV
    if s.endswith("UV"): return float(s[:-2]) * 1e-6   # uV
    if s.endswith("KV"): return float(s[:-2]) * 1e3    # kV
    if s.endswith("V"):  return float(s[:-1])
    return float(s)
VDIV_SEC = [[_vparse(x) for x in row] for row in VDIV_TEXT]
PROBE_ROW = {0:0, 1:1, 10:2, 20:3, 50:4, 100:5, 1000:6}
# 固件 SysParam 里探头字段是网页约定的 0/1/2（1x / 10x / >=50x），
# 映射回 VDIV_TEXT 的倍率行（行号 = 固件 magnification 索引）
WEB_PROBE_ROW  = {0: 1, 1: 2, 2: 5}
WEB_PROBE_TEXT = {0: "1x", 1: "10x", 2: "100x"}

# ---- 固件真实采样率 / 屏幕映射（variables.c + scope_functions.c）------------
# sample_rate[29]：每个采样率档对应的真实 Sa/s
SAMPLE_RATE_HZ = [
    200e6, 100e6, 50e6, 20e6, 10e6, 5e6, 2e6, 1e6, 500e3, 200e3,
    100e3, 50e3, 20e3, 10e3, 5e3, 2e3, 1e3, 500,
    10000, 4000, 2000, 1000, 400, 200, 100, 40, 20, 10, 4,
]
# frequency_per_div[35]：1/时基（Hz），index 与 TDIV_TEXT 完全对齐
FREQ_PER_DIV = [
    1, 2, 4, 8, 20, 40, 80, 200, 400, 800, 2000,
    5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000,
    50000, 100000, 200000, 500000, 1000000, 2000000, 5000000, 10000000,
    20000000, 50000000, 100000000, 200000000,
]
# time_per_div_sample_rate[35]：时基档 -> 采样率档索引
TIME_PER_DIV_SR_IDX = [
    28, 27, 26, 25, 24, 23, 22, 21, 20, 19, 18,
    17, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0,
    0, 0, 0, 0, 0, 0,
]
# scope_functions.c: disp_xpos_per_sample = 50 * frequency_per_div / sample_rate
# 波形区宽 726px（menu.c scope_draw_grid: display_draw_rect(2,48,726,403)）
GRID_PX = 50
SCREEN_W_PX = 726
# #WAV2,3 的 CH1/CH2 段是 4801 字节：前 3000 点是采集缓冲，后 1801 点是 0x80 填充
BUFFER_SAMPLES = 3000

# 真机电压标定（可选）：#WAV2,3 返回 FPGA 原始 ADC，而屏幕测量面板走的是
# 固件内部的 dcoffset / adc1compensation / dc_shift_size 校准链，这些量不在
# SysParam 的 320 字节里。用一次真机读数(Umax/Umin)反推 v_per_adc / adc_zero
# 就能让 AI 的电压读数与屏幕一致。换电压档或探头后需重新标定。
VOLT_CAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "scope_cal_volt.json")


def load_volt_cal():
    try:
        with open(VOLT_CAL_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_volt_cal(cal):
    with open(VOLT_CAL_FILE, "w", encoding="utf-8") as f:
        json.dump(cal, f, ensure_ascii=False, indent=2)


def _rate_text(hz):
    if hz >= 1e6:
        return ("%g" % (hz / 1e6)) + "MSa/s"
    if hz >= 1e3:
        return ("%g" % (hz / 1e3)) + "KSa/s"
    return ("%g" % hz) + "Sa/s"


def parse_time(s):
    """'1ms' '500us' '2s' -> 秒"""
    if isinstance(s, (int, float)): return float(s)
    s = str(s).strip().lower().replace("/div", "").replace("s/div", "")
    m = re.match(r"([0-9.]+)\s*(ms|us|ns|s)?", s)
    if not m: raise ValueError(f"无法解析时基: {s}")
    v = float(m.group(1)); u = m.group(2) or "s"
    return v * {"s":1, "ms":1e-3, "us":1e-6, "ns":1e-9}[u]


def nearest_time_index(sec):
    return min(range(len(TDIV_SEC)), key=lambda i: abs(TDIV_SEC[i] - sec))


def nearest_vdiv_index(volts, probe_row=1):
    row = VDIV_SEC[probe_row]
    return min(range(len(row)), key=lambda i: abs(row[i] - volts))


def period_samples(ch, thr_mid=True):
    """实测：缓冲里一个信号周期平均占多少采样点（用上升沿间隔中位数）。
    返回 0 表示检测不到（直流/无周期）。"""
    if not ch:
        return 0
    n = len(ch)
    thr = (max(ch) + min(ch)) / 2.0
    ups = [i for i in range(1, n) if ch[i - 1] < thr <= ch[i]]
    if len(ups) < 2:
        return 0
    gaps = sorted(ups[i + 1] - ups[i] for i in range(len(ups) - 1))
    return gaps[len(gaps) // 2] if gaps else 0


def buffer_per_point(ch, cycle_str):
    """反推 #WAV2 缓冲【每个采样点的真实秒数】。
    关键：不能直接用 SysParam 的 sample_dt——实测缓冲是相对屏幕窗口的另一套抽取，
    每点时间与 sample_dt 差约数倍。正确口径 = 固件周期(#MEAS Cycle) / 实测每周期点数。
    不可用时返回 None。"""
    if not cycle_str:
        return None
    spc = period_samples(ch)
    if spc <= 0:
        return None
    try:
        cyc = parse_time(cycle_str)
    except Exception:
        return None
    if cyc <= 0:
        return None
    return cyc / spc


# ---- #MEAS 屏幕测量键序（与固件 scope_control.c 的 meas_keys 一一对应）----
MEAS_KEYS = ["Vmax", "Vmin", "Vavg", "Vrms", "Vpp", "Vp",
             "Freq", "Cycle", "Time+", "Time-", "Duty+", "Duty-"]


def parse_prm(prm):
    """把 320 字节 SysParam 解析成可读字典（params() 与 capture() 共用）"""
    def u(i): return prm[i] if i < len(prm) else 0
    p = {"prm_bytes": len(prm)}
    p["run"] = "RUN" if u(0) == 1 else "STOP"
    p["run_state"] = u(0)
    p["ch2_enabled"] = u(2) == 1
    zi = u(20)
    p["time_zoom"] = zi
    # 固件约定 SysParam.time_zoom = 34 - timeperdiv，要反查回固件的时基档位
    tdi = 34 - zi
    p["timebase_idx"] = tdi
    p["timebase"] = TDIV_TEXT[tdi] + "/div" if 0 <= tdi < len(TDIV_TEXT) else "N/A"
    p["timebase_sec"] = TDIV_SEC[tdi] if 0 <= tdi < len(TDIV_SEC) else None
    # 采样率由时基档查固件表得到（不能用"时基×10格/点数"反推，那会差 3 倍）
    ti = tdi if 0 <= tdi < 35 else 20
    sri = TIME_PER_DIV_SR_IDX[ti]
    rate = SAMPLE_RATE_HZ[sri] if 0 <= sri < len(SAMPLE_RATE_HZ) else 500000.0
    p["sample_rate_hz"] = rate
    p["sample_rate"] = _rate_text(rate)
    p["sample_dt"] = 1.0 / rate                       # 每个采样点的真实时间
    p["px_per_sample"] = GRID_PX * FREQ_PER_DIV[ti] / rate   # 200us/div -> 0.5 px/点
    p["screen_points"] = int(SCREEN_W_PX / p["px_per_sample"]) if p["px_per_sample"] > 0 else 1500
    # 通道零点在屏幕上的 Y：scope_draw_pointers() -> 441 - traceposition
    p["ch1_zero_y"] = 441 - (u(80) | (u(81) << 8))
    p["ch2_zero_y"] = 441 - (u(148) | (u(149) << 8))
    def vd(idx, probe):
        # SysParam 里 ch*_volt_zoom = 4 + 电压档位(0..6)；ch*_probe 是网页约定的 0/1/2
        row = WEB_PROBE_ROW.get(probe, 1)
        i = idx - 4
        return VDIV_TEXT[row][i] + "/div" if 0 <= i < 7 else "N/A"
    def vs(idx, probe):
        row = WEB_PROBE_ROW.get(probe, 1)
        i = idx - 4
        return VDIV_SEC[row][i] if 0 <= i < 7 else 1.0
    p["ch1_volt_zoom"] = u(74); p["ch2_volt_zoom"] = u(140)
    p["ch1_vdiv"] = vd(u(74), u(117)); p["ch1_scale"] = vs(u(74), u(117))
    p["ch2_vdiv"] = vd(u(140), u(181)); p["ch2_scale"] = vs(u(140), u(181))
    p["ch1_probe"] = u(117); p["ch2_probe"] = u(181)
    p["ch1_probe_text"] = WEB_PROBE_TEXT.get(u(117), "1x")
    p["ch2_probe_text"] = WEB_PROBE_TEXT.get(u(181), "1x")
    p["ch1_coupling"] = u(116); p["ch2_coupling"] = u(180)
    p["trigger_channel"] = u(64); p["trigger_mode"] = u(65); p["trigger_edge"] = u(66)
    p["trigger"] = f"{'CH2' if u(64) else 'CH1'}/{'NORMAL' if u(65) else 'AUTO'}/{'FALL' if u(66) else 'RISE'}"
    p["trigger_level"] = u(72)           # FPGA 触发电平 0..255, 128=波形中心
    p["ch1_vert"] = u(80) | (u(81) << 8)
    p["ch2_vert"] = u(148) | (u(149) << 8)
    p["ch1_vert_position"] = p["ch1_vert"]
    p["ch2_vert_position"] = p["ch2_vert"]
    return p


def to_samples(hexstr):
    """解析 #OK,<prm>,<ch1>,<ch2> 帧"""
    parts = [p.strip() for p in hexstr.split(",") if p.strip()]
    if not parts or len(parts[0]) < 640:
        return None
    prm = bytes.fromhex(parts[0][:640])
    def conv(s, limit=BUFFER_SAMPLES):
        # 帧里每段 4801 字节，末尾 1801 字节是 0x80(=中间电平)填充，必须丢掉
        return [int(s[i:i+2], 16) for i in range(0, len(s)-1, 2)][:limit]
    ch1 = conv(parts[1]) if len(parts) > 1 else []
    ch2 = conv(parts[2]) if len(parts) > 2 else []
    p = parse_prm(prm)
    return p, ch1, ch2


def _platform(sub, thr, vmax, vmin):
    hi = sorted(v for v in sub if v >= thr)
    lo = sorted(v for v in sub if v < thr)
    return (hi[len(hi) // 2] if hi else vmax, lo[len(lo) // 2] if lo else vmin)


def analyze(ch, scale, dt=None, vcal=None):
    """把 ADC 采样转成 AI 可读的信号指标。

    dt   = 每个采样点的真实秒数（固件采样率表算出，例如 500KSa/s -> 2us）。
    vcal = 可选的真机电压标定 dict{v_per_adc, adc_zero}；不传时按
           scale*8/256 V/ADC、零点 128 的理论值计算（与屏幕读数会有偏差）。
    """
    if not ch: return None
    n = len(ch)
    vmax, vmin = max(ch), min(ch)
    step = vcal["v_per_adc"] if vcal else scale * 8 / 256.0
    zero = vcal["adc_zero"] if vcal else 128.0
    vpp_raw = (vmax - vmin) * step
    mean = sum(ch) / n
    dc = (mean - zero) * step
    rms = math.sqrt(sum((x-zero)**2 for x in ch) / n) * step
    ac = math.sqrt(max(rms*rms - dc*dc, 0))
    thr = (vmax + vmin) / 2
    # 缓冲是两路交织 ADC（162/148、61/43 交替）。把两个 bank 混在一起取
    # 中位数会把平台差压小（实测 89 ADC 而非 101/105），必须分开再平均。
    # 同时平台法本身可以免疫偶发野点（如 6 / 252）。
    h0, l0 = _platform(ch[::2], thr, vmax, vmin)
    h1, l1 = _platform(ch[1::2], thr, vmax, vmin)
    hi_med = (h0 + h1) / 2.0
    lo_med = (l0 + l1) / 2.0
    vpp = (hi_med - lo_med) * step
    peak = (hi_med - zero) * step
    bottom = (lo_med - zero) * step
    up, dn = [], []
    st = ch[0] >= thr
    for i, v in enumerate(ch):
        ns = v >= thr
        if ns != st: (up if ns else dn).append(i); st = ns
    # 上升沿间隔的中位数 = 一个周期对应的采样点数（比"整帧边沿数"稳，
    # 窗口边界把周期截断时不会像直接计数那样差一倍）
    gaps = sorted(up[i + 1] - up[i] for i in range(len(up) - 1))
    period = gaps[len(gaps) // 2] if gaps else 0
    freq = 1.0 / (period * dt) if (period and dt) else None

    # 高/低电平游程的中位数 -> Time+ / Time- / 占空比
    hi_runs, lo_runs = [], []
    cur, run = ch[0] >= thr, 0
    for v in ch:
        ns = v >= thr
        if ns == cur:
            run += 1
        else:
            (hi_runs if cur else lo_runs).append(run)
            cur, run = ns, 1
    (hi_runs if cur else lo_runs).append(run)
    med = lambda a: sorted(a)[len(a) // 2] if a else 0
    t_hi = med(hi_runs) * dt if dt else None
    t_lo = med(lo_runs) * dt if dt else None
    duty = (t_hi / (t_hi + t_lo)) if (t_hi and t_lo) else None
    return {
        "points": n, "vpp": round(vpp, 4), "vpp_raw": round(vpp_raw, 4),
        "vmax": round(peak, 4), "vmin": round(bottom, 4),
        "dc": round(dc, 4),
        "rms": round(rms, 4), "ac_rms": round(ac, 4),
        "edges_up": len(up), "edges_down": len(dn),
        "period_samples": period, "raw_sample": ch[:20],
        "freq_hz": round(freq, 3) if freq else None,
        "cycle_sec": round(1.0/freq, 9) if freq else None,
        "time_pos": round(t_hi, 9) if t_hi else None,
        "time_neg": round(t_lo, 9) if t_lo else None,
        "duty_pos": round(duty, 4) if duty else None,
        "duty_neg": round(1-duty, 4) if duty else None,
        "peak_v": round(peak, 4),
        "vavg": round(dc, 4),
        "hi_median": round(hi_med, 2), "lo_median": round(lo_med, 2),
        "v_per_adc": round(step, 8), "adc_zero": round(zero, 2),
        "voltage_calibrated": bool(vcal),
    }


class ScopeAI:
    def __init__(self, port=None, baud=2000000, timeout=0.5):
        self.port = port or self.find_port()
        if not self.port:
            raise RuntimeError("找不到示波器串口。请确认已开启 USB-Serial mode，且没有其它程序占用。")
        self.ser = serial.Serial(self.port, baudrate=baud, timeout=timeout)
        time.sleep(0.1)
        # 清空缓冲
        self.ser.reset_input_buffer()

    @staticmethod
    def find_port():
        ports = [p for p in list_ports.comports() if p.vid == 0x483]
        if ports: return ports[0].device
        ports = [p for p in list_ports.comports()
                 if "USB" in p.description.upper() or "CH340" in p.description.upper()]
        return ports[0].device if ports else None

    @staticmethod
    def list_ports():
        return [{"device": p.device, "desc": p.description,
                 "vid": hex(p.vid) if p.vid else None} for p in list_ports.comports()]

    def _send(self, cmd, timeout=1.0):
        """发送命令并读一行响应"""
        self.ser.write((cmd + "\n").encode())
        time.sleep(0.08)
        buf = b""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.ser.in_waiting:
                buf += self.ser.read(self.ser.in_waiting)
                if b"\n" in buf: break
            time.sleep(0.01)
        return buf.decode("ascii", errors="ignore").strip()

    # ---- 语义化控制命令 ----------------------------------------------------
    def set_timebase(self, value):
        """设时基: set_timebase('1ms') / set_timebase('500us') / set_timebase(0.001)"""
        sec = parse_time(value)
        idx = nearest_time_index(sec)
        r = self._send(f"#SET,TDIV,{idx}")
        return {"cmd": f"TDIV,{idx}", "expect": TDIV_TEXT[idx] + "/div", "resp": r}

    def set_vdiv(self, channel, value):
        """设电压档: set_vdiv('CH1','500mV')"""
        ch = "CH1" if str(channel).upper().startswith("CH1") else "CH2"
        volts = _vparse(value) if isinstance(value, str) else float(value)
        idx = nearest_vdiv_index(volts, probe_row=1)
        r = self._send(f"#SET,{ch},VDIV,{idx}")
        return {"cmd": f"{ch},VDIV,{idx}", "expect": VDIV_TEXT[1][idx] + "/div", "resp": r}

    def set_channel(self, channel, on):
        ch = "CH1" if str(channel).upper().startswith("CH1") else "CH2"
        return self._send(f"#SET,{ch},{'ON' if on else 'OFF'}")

    def set_coupling(self, channel, ac):
        ch = "CH1" if str(channel).upper().startswith("CH1") else "CH2"
        return self._send(f"#SET,{ch},{'AC' if ac else 'DC'}")

    def set_probe(self, channel, ratio):
        ch = "CH1" if str(channel).upper().startswith("CH1") else "CH2"
        row = PROBE_ROW.get(int(ratio), 1)
        return self._send(f"#SET,{ch},PROBE,{row}")

    def set_trigger(self, source=None, mode=None, edge=None):
        out = {}
        if source:
            src = "CH2" if str(source).upper().startswith("CH2") else "CH1"
            out["source"] = self._send(f"#SET,TRIG,{src}")
        if mode:
            m = str(mode).upper()
            m = "SINGLE" if m.startswith("SIN") else ("NORMAL" if m.startswith("NOR") else "AUTO")
            out["mode"] = self._send(f"#SET,TRIG,{m}")
        if edge:
            e = "FALL" if str(edge).upper().startswith("FAL") else "RISE"
            out["edge"] = self._send(f"#SET,TRIG,{e}")
        return out

    def set_trigger_level(self, y):
        """设置触发电平（屏幕 Y 坐标 6..396，越大越靠下）

        固件内部换算成 FPGA 电平 (0..255, 128=波形中心) 后写入触发电路。
        """
        return self._send(f"#SET,TRIG,LEVEL,{int(y)}")

    def set_channel_position(self, channel, y):
        """设置通道波形在屏幕上的垂直位置（Y 坐标 6..396）

        等价于用手指拖动波形；触发在该通道上时触发标记会跟着一起移动。
        """
        ch = "CH1" if str(channel).upper().startswith("CH1") else "CH2"
        return self._send(f"#SET,{ch},POS,{int(y)}")

    def run(self):    return self._send("#SET,RUN")
    def stop(self):   return self._send("#SET,STOP")
    def single(self): return self._send("#SET,SINGLE")

    def autoset(self):
        # AUTOSET 内部要做一次自动量程分析（含触发等待），耗时可能 >1s，
        # 且需要 RUN 态才有效；先确保运行，再给 8s 超时等它回 #OK,AUTOSET。
        self._send("#SET,RUN")
        return self._send("#SET,AUTOSET", timeout=8.0)
    def language(self, cn=True):
        """切换界面语言: language(True)=中文, language(False)=英文"""
        return self._send("#SET,LANG,CN" if cn else "#SET,LANG,EN")

    def raw(self, cmd):
        """直接发任意原始命令, 如 '#KEY,F' / '#SET,TDIV,20'"""
        return self._send(cmd if cmd.startswith("#") else "#" + cmd)

    # ---- 冻结抓帧 ----------------------------------------------------------
    @contextmanager
    def frozen(self, settle=0.15):
        """临时暂停采集，退出时自动恢复原来的运行状态。

        真机实测：RUN 状态下连续抓 4 帧，每帧与首帧相差 500+ 字节
        （采集缓冲边写边读，读到的可能是撕裂帧，频率甚至会误报）；
        #SET,STOP 之后连抓 4 帧 md5 完全相同、0 字节差异，隔 2.5s 再抓仍一致。

        可安全嵌套；若进入时本来就是 STOP，退出时仍保持 STOP（不擅自开机）。
        用法：
            with scope.frozen():
                d = scope.capture()      # 稳定帧
                d2 = scope.capture()     # 与 d 逐字节相同
            # 这里已自动恢复 RUN
        """
        p0 = self.params()
        need_resume = (p0.get("run_state") == 1)
        if need_resume:
            self.stop()
            time.sleep(settle)           # 等当前采集周期收尾、缓冲冻结
        try:
            yield self
        finally:
            if need_resume:
                self.run()

    def capture_stable(self, frames=3, settle=0.15):
        """暂停采集 -> 连抓 N 帧逐字节校验 -> 返回稳定帧 -> 自动恢复运行。

        返回结构在 capture() 基础上多出：
            frozen=True, frames_verified=N, run_original='RUN'/'STOP'
        params 里的 run / run_state 回填为「暂停前」的状态（重建的是暂停前
        那一瞬的屏幕），并加 capture_frozen=True 标记。
        校验不通过会抛 RuntimeError，而不是悄悄返回一帧脏数据。
        """
        p0 = self.params()
        with self.frozen(settle=settle):
            got = []
            for _ in range(max(2, frames)):
                d = self.capture()
                if d:
                    got.append(d)
            if len(got) < 2:
                raise RuntimeError("冻结后未能抓到足够的波形帧")
            base = bytes(got[0]["ch1_raw"])
            bad = [i + 2 for i, d in enumerate(got[1:])
                   if bytes(d["ch1_raw"]) != base]
            if bad:
                raise RuntimeError(
                    "冻结后第 %s 帧与首帧不一致，采集可能没有真正暂停" % bad)
            out = got[0]
            out["frozen"] = True
            out["frames_verified"] = len(got)
            out["run_original"] = "RUN" if p0.get("run_state") == 1 else "STOP"
            out["params"]["run"] = out["run_original"]
            out["params"]["run_state"] = p0.get("run_state")
            out["params"]["capture_frozen"] = True
            return out

    # ---- 电压标定（让 AI 读数与屏幕测量面板一致）----------------------------
    @staticmethod
    def _volt_cal_for(p):
        cal = load_volt_cal()
        if not cal:
            return None
        # 电压档 / 探头 / 耦合任一变化都会改变换算，标定随即失效
        if (cal.get("ch1_volt_zoom") == p.get("ch1_volt_zoom")
                and cal.get("ch1_probe") == p.get("ch1_probe")
                and cal.get("ch1_coupling") == p.get("ch1_coupling")):
            return cal
        return None

    def calibrate_voltage(self, umax, umin, channel="CH1"):
        """用屏幕上的 Umax / Umin 读数标定 ADC→电压换算（同一档位只需一次）。

        示例：屏幕显示 Umax=2.18V、Umin=-1.32V
              s.calibrate_voltage(2.18, -1.32)
        会冻结抓一帧，按交织 bank 的平台电平反推 v_per_adc 与 adc_zero，
        存到 scope_cal_volt.json。之后所有 capture() 的电压读数与真机一致。
        换电压档 / 探头 / 耦合后自动失效，需重新标定。
        """
        d = self.capture_stable()
        p = d["params"]
        is1 = str(channel).upper().startswith("CH1")
        ch = d["ch1_raw"] if is1 else d["ch2_raw"]
        scale = p["ch1_scale"] if is1 else p["ch2_scale"]
        a = analyze(ch, scale, p["sample_dt"], None)
        hi, lo = a["hi_median"], a["lo_median"]
        if hi <= lo:
            raise RuntimeError("波形没有明显的高低电平平台，无法用 Umax/Umin 标定")
        umax, umin = float(umax), float(umin)
        v_per_adc = (umax - umin) / (hi - lo)
        adc_zero = hi - umax / v_per_adc
        cal = {
            "v_per_adc": v_per_adc, "adc_zero": adc_zero,
            "umax_ref": umax, "umin_ref": umin,
            "hi_adc": hi, "lo_adc": lo,
            "ch1_volt_zoom": p["ch1_volt_zoom"], "ch1_probe": p["ch1_probe"],
            "ch1_coupling": p["ch1_coupling"], "ch1_vdiv": p["ch1_vdiv"],
            "channel": "CH1" if is1 else "CH2",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_volt_cal(cal)
        return cal

    @staticmethod
    def clear_voltage_cal():
        try:
            os.remove(VOLT_CAL_FILE)
            return "已清除电压标定"
        except OSError:
            return "没有已保存的电压标定"

    # ---- 采集 --------------------------------------------------------------
    def params(self, timeout=2.0):
        """读取示波器参数区（#READ，320 字节 SysParam），返回解析后的字典

        字段偏移与固件 PC_interface.h 的 sysparam 结构一致。
        """
        self.ser.reset_input_buffer()
        self.ser.write(b"#READ\n")
        buf = ""
        t0 = time.time()
        while time.time() - t0 < timeout:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n).decode("ascii", errors="ignore")
                if "\n" in buf:
                    break
            else:
                time.sleep(0.02)
        buf = buf.strip()
        if not buf.startswith("#OK,"):
            return None
        try:
            b = bytes(int(h, 16) for h in buf[4:].split(",") if len(h) == 2)
        except ValueError:
            return None
        if len(b) < 320:
            return None
        p = parse_prm(b)
        p["bytes"] = len(b)
        return p

    # ---- 屏幕测量值（固件原值，逐字一致）------------------------------------
    def meas(self, channel=None, timeout=1.2):
        """读 #MEAS：固件用它显示屏幕的同一组函数算好后原样回传。
        这是【与示波器屏幕逐字一致】的测量值（含单位），优先于本地 analyze() 估算。
        返回 {'CH1': {key:str,...}, 'CH2': {...}}；
        旧固件不支持时返回 {'_error': '#ERR,UNKNOWN'}。
        """
        cmd = "#MEAS" if not channel else "#MEAS,%s" % channel
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\n").encode())
        buf = ""
        t0 = time.time()
        while time.time() - t0 < timeout:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n).decode("ascii", errors="ignore")
                if "\n" in buf:
                    break
            else:
                time.sleep(0.01)
        out = {}
        err = ""
        for line in buf.splitlines():
            line = line.strip()
            if line.startswith("#ERR"):
                err = line
                continue
            if not line.startswith("#MEAS"):
                continue
            parts = line.split(",")
            if len(parts) < 3:
                continue
            ch = parts[1]
            kv = {}
            for tok in parts[2:]:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    kv[k.strip()] = v.strip()
            out[ch] = kv
        if not out and err:
            return {"_error": err}
        return out

    def meas_stable(self, verify=True, restore=False, channel=None):
        """【推荐读法】先 STOP 冻结再读 #MEAS，避免边采样波动导致读数跳变。
        实测：STOP 后连读 #MEAS 三次逐字一致；RUN 态则每帧都在变。
        verify=True 时额外读一次确认稳定（两次不一致会标记 _unstable）。
        restore=True 时读完后恢复到进入前的 RUN/STOP 状态；默认保持 STOP，
        这样读数就是屏幕上那一瞬间冻结的帧。
        """
        was_run = None
        if restore:
            pr = self.params()
            was_run = (pr or {}).get("run_state") == 1
        self.stop()
        time.sleep(0.12)                 # 等当前采集周期收尾、缓冲冻结
        m = self.meas(channel)
        if verify and not m.get("_error"):
            m2 = self.meas(channel)
            if m2 != m:
                m["_unstable"] = True
        if restore and was_run:
            self.run()
        return m

    def capture(self, timeout=3.0, request=True):
        """请求一帧波形并解析成结构化数据"""
        if request:
            self.ser.reset_input_buffer()
            self.ser.write(b"#WAV2,3\n")
        buf = ""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.ser.in_waiting:
                buf += self.ser.read(65536).decode("ascii", errors="ignore")
                while "#OK," in buf:
                    s = buf.find("#OK,") + 4
                    e = buf.find("\n", s)
                    if e < 0: break
                    hexdata = buf[s:e].strip()
                    buf = buf[e+1:]
                    if len(hexdata) < 640: continue
                    parsed = to_samples(hexdata)
                    if not parsed: continue
                    p, ch1, ch2 = parsed
                    cal = self._volt_cal_for(p)
                    try:
                        screen = self.meas_stable(verify=False)  # 先冻结再读
                    except Exception:
                        screen = {}
                    # 本地估算时间基准：用实测每点秒(周期/每周期点数)，
                    # 而非 SysParam sample_dt —— 后者与 #WAV2 缓冲抽取不符，
                    # 会使本地频率偏差数倍。测不到周期时回退 sample_dt。
                    dt1 = buffer_per_point(ch1, screen.get("CH1", {}).get("Cycle")) or p["sample_dt"]
                    dt2 = (buffer_per_point(ch2, screen.get("CH2", {}).get("Cycle"))
                           or p["sample_dt"])
                    return {
                        "params": p,
                        "screen_meas": screen,
                        "CH1": analyze(ch1, p["ch1_scale"], dt1, cal),
                        "CH2": analyze(ch2, p["ch2_scale"], dt2, cal)
                               if p["ch2_enabled"] else None,
                        "ch1_raw": ch1, "ch2_raw": ch2,
                        "volt_cal": cal,
                    }
            time.sleep(0.02)
        return None

    # ---- 闭环：AI 描述信号 -> 自动调整显示 ---------------------------------
    def view(self, description):
        """一句话让示波器调好来显示目标信号。

        支持形如: '2kHz 3.3V 方波' / '100MHz 正弦' / '500mV 1kHz'
        自动设置 时基(显示2-3个周期) + 电压档(信号占约4格) + 触发。
        """
        freq = None; volt = None
        fm = re.search(r"([0-9.]+)\s*(k|m|g)?\s*(hz|赫)", description, re.I)
        if fm:
            mult = {"":1, "k":1e3, "m":1e6, "g":1e9}.get((fm.group(2) or "").lower(), 1)
            freq = float(fm.group(1)) * mult
        vm = re.search(r"([0-9.]+)\s*(m?v)", description, re.I)
        if vm:
            volt = _vparse(vm.group(1) + (vm.group(2) or "V"))
        actions = {}
        if freq:
            period = 1.0 / freq
            tdiv = period / 4.0              # 让一个周期占约4格
            actions["timebase"] = self.set_timebase(tdiv)
        if volt:
            vdiv = volt / 4.0                # 让信号占约4格
            actions["vdiv"] = self.set_vdiv("CH1", vdiv)
        actions["trigger"] = self.set_trigger(source="CH1", mode="AUTO", edge="RISE")
        actions["coupling"] = self.set_coupling("CH1", False)
        time.sleep(0.3)
        data = self.capture()
        return {"description": description, "actions": actions, "result": data}

    def report(self, data=None):
        """生成 AI 可读文本报告

        测量值口径：
          【屏幕原值】= 固件 #MEAS，与示波器屏幕显示逐字一致（权威）。
          【本地估算】= 由采样数组重算，仅供波形形状/毛刺分析参考，
                       跨档位/直流偏置时与屏幕会有偏差，勿当读数依据。
        """
        d = data or self.capture()
        if not d: return "采集失败：没有收到波形数据。"
        p = d["params"]
        cal = d.get("volt_cal")
        tag = ("已按真机读数标定" if cal else "未标定，电压为理论换算值")
        lines = [
            "=== 示波器状态 ===",
            f"运行={p['run']} 时基={p['timebase']} 采样率={p.get('sample_rate','?')} "
            f"触发={p['trigger']} 触发电平={p.get('trigger_level', '?')}/255",
            f"CH1={p['ch1_vdiv']} @{p.get('ch1_probe_text', '1x')} (位置 {p.get('ch1_vert', '?')})  "
            f"CH2={p['ch2_vdiv']} @{p.get('ch2_probe_text', '1x')} "
            f"({'ON' if p['ch2_enabled'] else 'OFF'})  电压换算: {tag}",
        ]
        # --- 屏幕原值（#MEAS，权威，与屏幕逐字一致）---
        try:
            m = self.meas_stable(verify=False)   # 先冻结再读，避免波动
        except Exception:
            m = {}
        if m and not m.get("_error"):
            lines.append("")
            lines.append("=== 屏幕测量值(固件 #MEAS 原值 · 与示波器逐字一致) ===")
            for ch in ("CH1", "CH2"):
                kv = m.get(ch)
                if not kv:
                    continue
                cell = "  ".join("%s=%s" % (k, kv[k]) for k in MEAS_KEYS if k in kv)
                lines.append(f"  [{ch}] {cell}")
        elif isinstance(m, dict) and m.get("_error"):
            lines.append("")
            lines.append("（屏幕原值不可用：%s —— 旧固件无 #MEAS，下面电压仅为本地估算）"
                         % m["_error"])
        # --- 本地估算（仅参考）---
        for name in ("CH1", "CH2"):
            a = d.get(name)
            if not a: continue
            freq = (f"  频率≈{a['freq_hz']:.0f}Hz  周期≈{a['cycle_sec']*1e3:.3f}ms"
                    f"  占空比≈{a['duty_pos']*100:.1f}%" if a.get("freq_hz") else "")
            lines += [
                f"--- {name} 本地估算(仅参考) ---",
                f"  Vpp={a['vpp']}V  Vmax={a['vmax']}V  Vmin={a['vmin']}V",
                f"  直流={a['dc']}V  RMS={a['rms']}V  交流RMS={a['ac_rms']}V",
                f"  采样点={a['points']}  上升沿={a['edges_up']} 下降沿={a['edges_down']}{freq}",
            ]
        return "\n".join(lines)

    # ---- LCD 整屏截图（#SHOT，RGB565 帧缓冲）------------------------------
    def _read_binary(self, n, timeout=15.0):
        """阻塞读取恰好 n 个原始字节（非文本）。

        用带超时的 ser.read() 持续消费，而非 in_waiting 轮询 + sleep——后者在
        大块(768KB)接收时 OS 接收缓冲易溢出丢包。阻塞 read 让内核及时搬运交付。
        """
        data = bytearray()
        end = time.time() + timeout
        old_to = self.ser.timeout
        try:
            while len(data) < n:
                rem = end - time.time()
                if rem <= 0:
                    break
                self.ser.timeout = min(2.0, rem)
                want = min(16384, n - len(data))
                chunk = self.ser.read(want)   # 阻塞至多 timeout，及时消费
                if chunk:
                    data += chunk
                elif time.time() > end:
                    break
        finally:
            self.ser.timeout = old_to
        return bytes(data)

    def _read_binary_idlean(self, n, idle=1.5, timeout=20.0):
        """读二进制：读满 n，或连续 idle 秒无新数据即停（设备尾部丢包时不空等）。"""
        data = bytearray()
        end = time.time() + timeout
        last = time.time()
        old_to = self.ser.timeout
        try:
            while len(data) < n:
                if time.time() > end:
                    break
                self.ser.timeout = 0.5
                want = min(16384, n - len(data))
                chunk = self.ser.read(want)
                if chunk:
                    data += chunk
                    last = time.time()
                elif time.time() - last > idle:
                    break           # idle: 设备已无更多数据
        finally:
            self.ser.timeout = old_to
        return bytes(data)

    def shot(self, out_path="屏幕截图.png", timeout=20.0):
        """抓取当前 LCD 屏幕画面，保存为 PNG 并返回路径。

        固件 #SHOT 先发一行 ascii 头 `#SHOT,800,480,2,768000`，再发 768000
        字节的 RGB565 帧缓冲（小端、标准 R5G6B5）。这里读头→读满二进制→
        解码 RGB565 为 RGB888→用 Pillow 存 PNG。
        注意：这是真实屏幕像素（含菜单、文字、波形），与 #WAV2 采样重算图不同。
        """
        self.ser.reset_input_buffer()
        self.ser.write(b"#SHOT\n")
        # 读 ascii 头行
        head = b""
        t0 = time.time()
        while b"\n" not in head and time.time() - t0 < timeout:
            n = self.ser.in_waiting
            if n:
                head += self.ser.read(n)
            else:
                time.sleep(0.005)
        if b"\n" not in head:
            return {"error": "未收到 #SHOT 头（固件可能不支持 #SHOT）"}
        line, rest = head.split(b"\n", 1)
        if not line.startswith(b"#SHOT"):
            return {"error": "响应异常: " + line.decode("ascii", "ignore")}
        _, w_s, h_s, bpp_s, total_s = line.split(b",")
        w, h, total = int(w_s), int(h_s), int(total_s)
        # 头之后可能已附带部分像素，先吃进缓冲区再读剩余。
        # 容错：设备偶发尾包缺失时，连续 1.5s 无新数据即停，缺失按黑补齐出图
        # （实测缺失稳定在最底部，约 2048B≈1.3 屏行，对屏幕内容影响极小）。
        buf = bytearray(rest)
        buf += self._read_binary_idlean(total - len(buf), idle=1.5, timeout=timeout)
        got = len(buf)
        padded = total - got if got < total else 0
        if padded:
            buf += bytes(padded)              # 底部缺失按黑(0x0000)补齐
        png = self._rgb565_to_png(bytes(buf), w, h, out_path)
        res = {"saved": png, "width": w, "height": h, "bytes": total,
               "got": got, "format": "RGB565"}
        if padded:
            res["padded_bottom_bytes"] = padded
            res["note"] = "尾部%d字节未收(约%.1f行)，底部按黑补齐；其余为真实屏幕像素" % (padded, padded / (w * 2))
        return res

    @staticmethod
    def _rgb565_to_png(raw, w, h, out_path):
        try:
            from PIL import Image
        except ImportError:
            # 退而求其次：存原始 .rgb565 二进制供后续转换
            with open(out_path + ".raw565", "wb") as f:
                f.write(raw)
            return out_path + ".raw565"
        # RGB565(小端) -> RGB888。每像素 2 字节: hi=RRRRRGG G, lo=GGGBBBBB
        out = bytearray(w * h * 3)
        j = 0
        k = 0
        npx = w * h
        while j < npx:
            lo = raw[k]; hi = raw[k + 1]; k += 2
            v = lo | (hi << 8)
            r5 = (v >> 11) & 0x1F
            g6 = (v >> 5) & 0x3F
            b5 = v & 0x1F
            out[j * 3 + 0] = (r5 << 3) | (r5 >> 2)
            out[j * 3 + 1] = (g6 << 2) | (g6 >> 4)
            out[j * 3 + 2] = (b5 << 3) | (b5 >> 2)
            j += 1
        img = Image.frombytes("RGB", (w, h), bytes(out))
        img.save(out_path)
        return out_path

    def close(self):
        self.ser.close()


def cli():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "help"):
        print(__doc__); return
    cmd = args[0]
    if cmd == "list":
        print(json.dumps(ScopeAI.list_ports(), ensure_ascii=False, indent=2)); return
    s = ScopeAI()
    try:
        if cmd == "status":
            print(json.dumps(s.capture(), ensure_ascii=False, indent=2))
        elif cmd == "set" and len(args) >= 3:
            what = args[1].lower()
            if what == "timebase":   r = s.set_timebase(args[2])
            elif what == "vdiv":     r = s.set_vdiv(args[2], args[3])
            elif what == "channel":  r = s.set_channel(args[2], args[3].lower() in ("on","1","true"))
            elif what == "trigger":  r = s.set_trigger(source=args[2], mode=args[3], edge=args[4] if len(args)>4 else None)
            elif what == "raw":      r = s.raw(" ".join(args[2:]))
            else: r = {"error": f"未知设置项 {what}"}
            print(json.dumps(r, ensure_ascii=False, indent=2))
        elif cmd in ("run","stop","single","autoset"):
            print(getattr(s, cmd)())
        elif cmd == "language":
            print(s.language(args[1].lower() not in ("en","english","0")))
        elif cmd == "view":
            print(json.dumps(s.view(" ".join(args[1:])), ensure_ascii=False, indent=2))
        elif cmd in ("capture", "capture-stable", "capture_stable"):
            d = s.capture() if cmd == "capture" else s.capture_stable()
            print(json.dumps(d, ensure_ascii=False, indent=2))
        elif cmd == "report":
            print(s.report())
        elif cmd == "meas":
            ch = args[1] if len(args) > 1 and args[1].upper().startswith("CH") else None
            print(json.dumps(s.meas(ch), ensure_ascii=False, indent=2))
        elif cmd in ("meas-stable", "meas_stable"):
            # 推荐：先 STOP 冻结再读，读数不波动，与屏幕那一瞬一致
            ch = args[1] if len(args) > 1 and args[1].upper().startswith("CH") else None
            print(json.dumps(s.meas_stable(channel=ch), ensure_ascii=False, indent=2))
        elif cmd == "calibrate" and len(args) >= 3:
            print(json.dumps(s.calibrate_voltage(args[1], args[2]),
                             ensure_ascii=False, indent=2))
        elif cmd in ("cal-clear", "cal_clear"):
            print(s.clear_voltage_cal())
        elif cmd == "shot":
            out = args[1] if len(args) > 1 else os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "屏幕截图.png")
            print(json.dumps(s.shot(out), ensure_ascii=False, indent=2))
        else:
            print(f"未知命令: {cmd}")
    finally:
        s.close()


if __name__ == "__main__":
    cli()
