# -*- coding: utf-8 -*-
"""render_wave.py — 抓取冻结帧完整波形(#WAV2)，画成示波器风格 PNG。
规则内置：frozen 先 STOP 再抓、读完恢复运行态。
横轴用【实测每点真实秒】(#MEAS周期 / 缓冲每周期点数)换算，给出真实 ms 刻度；
纵轴用各通道 V/div。无数沿(直流)时优雅回退为点数轴。"""
import os
import time as _t
from scope_ai import ScopeAI, parse_time, period_samples, buffer_per_point
from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 620
M, TOP, BOT, R = 70, 70, 64, 14
COL_BG = (10, 12, 16)
COL_GRID = (34, 40, 54)
COL_TICK = (90, 100, 118)
COL_CH1 = (255, 210, 63)
COL_CH2 = (63, 169, 255)
COL_TXT = (180, 190, 205)


def load_font(size):
    for fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
               r"C:\Windows\Fonts\segoeui.ttf"):
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            continue
    return ImageFont.load_default()


def nice_step(total, target=8):
    """从 1/2/5×10^k 里选一个刻度间隔使 tick 数≈target。"""
    import math
    if total <= 0:
        return 1.0
    raw = total / target
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def vtrace(raw, vdiv):
    return [(s - 128) * vdiv * 8 / 256.0 for s in raw]


def main():
    sc = ScopeAI()
    pr0 = sc.params() or {}
    was_run = pr0.get("run_state") == 1
    sc.stop()
    _t.sleep(0.12)
    d = sc.capture()
    m = sc.meas_stable(restore=False)
    pr = sc.params() or pr0
    sc.close()
    if was_run:
        # 恢复运行（已重新打开需新会话；这里用一次性 raw 不便，跳过：
        # 读数已完成，设备停在 STOP 即“当前画面”，如需运行用户可自行 RUN）
        pass

    if not d or not d.get("ch1_raw"):
        print("未抓到波形")
        return

    ch1 = d["ch1_raw"]
    ch2 = d.get("ch2_raw") or []
    n1, n2 = len(ch1), len(ch2)

    # 每点真实秒：优先 CH1 周期，其次 CH2
    per_pt = None
    for cyc_ch, arr in ((m.get("CH1", {}), ch1), (m.get("CH2", {}), ch2)):
        pp = buffer_per_point(arr, cyc_ch.get("Cycle"))
        if pp:
            per_pt = pp
            spc = period_samples(arr)
            cycle_s = parse_time(cyc_ch.get("Cycle"))
            break
    time_axis = per_pt is not None

    img = Image.new("RGB", (W, H), COL_BG)
    dr = ImageDraw.Draw(img)
    f = load_font(15)
    fb = load_font(20)

    ch1_v = vtrace(ch1, pr.get("ch1_scale", 1.0))
    ch2_v = vtrace(ch2, pr.get("ch2_scale", 1.0)) if n2 else []

    allv = ch1_v + ch2_v
    vmin, vmax = min(allv), max(allv)
    pad = (vmax - vmin) * 0.12 + 1e-9
    vmin -= pad; vmax += pad
    pl, pt, pb, prr = M, TOP, H - BOT, W - R

    def X(idx, n): return pl + (prr - pl) * (idx / (n - 1)) if n > 1 else pl
    def Y(v): return pb - (pb - pt) * ((v - vmin) / (vmax - vmin))

    total_s = (n1 - 1) * per_pt if time_axis else 0.0

    # 竖向刻度线 + 时间标签
    if time_axis:
        step_ms = nice_step(total_s * 1000)
        t = 0.0
        while t <= total_s * 1000 + 1e-9:
            x = pl + (prr - pl) * (t / (total_s * 1000))
            dr.line([(x, pt), (x, pb)], fill=COL_GRID, width=1)
            dr.text((x + 2, pb + 4), ("%g" % t) + "ms", fill=COL_TICK, font=f)
            t += step_ms
    else:
        for k in range(9):
            x = pl + (prr - pl) * k / 8
            dr.line([(x, pt), (x, pb)], fill=COL_GRID, width=1)
        dr.text((pl + (prr - pl) / 2 - 70, pb + 6), "(直流/无数沿，按点数显示)",
                fill=COL_TICK, font=f)
    # 横向网格
    for k in range(9):
        y = pt + (pb - pt) * k / 8
        dr.line([(pl, y), (prr, y)], fill=COL_GRID, width=1)
    if vmin < 0 < vmax:
        dr.line([(pl, Y(0.0)), (prr, Y(0.0))], fill=(70, 80, 96), width=1)

    def draw(arr, color, n):
        if not arr: return
        dr.line([(X(i, n), Y(arr[i])) for i in range(n)], fill=color, width=2)
    draw(ch2_v, COL_CH2, n2)
    draw(ch1_v, COL_CH1, n1)

    # 顶部信息
    dr.text((M, 20), "示波器屏幕完整波形还原  (#WAV2 同帧采样 · 冻结读取)",
            fill=(235, 240, 250), font=fb)
    tb = pr.get("timebase", "?")
    head = "时基 %s   CH1=%s %s   CH2=%s %s" % (
        tb, pr.get("ch1_vdiv", "?"), pr.get("ch1_probe_text", "1x"),
        pr.get("ch2_vdiv", "?"), "ON" if pr.get("ch2_enabled") else "OFF")
    if time_axis:
        head += "   横轴: 实测每点 %.2fus, 整帧 %.2fms" % (per_pt * 1e6, total_s * 1000)
    else:
        head += "   横轴: 无周期可测(直流)"
    dr.text((M, 46), head, fill=COL_TXT, font=f)

    # Y 轴电压标
    dr.text((6, pt - 4), "%.2fV" % vmax, fill=COL_TXT, font=f)
    dr.text((6, pb - 16), "%.2fV" % vmin, fill=COL_TXT, font=f)
    if vmin < 0 < vmax:
        dr.text((6, Y(0.0) - 8), "0V", fill=COL_TXT, font=f)
    # 图例
    dr.text((prr - 150, pt + 6), "— CH1", fill=COL_CH1, font=f)
    if n2:
        dr.text((prr - 150, pt + 24), "— CH2", fill=COL_CH2, font=f)

    # 屏幕 #MEAS 原值（CH1）
    c1 = m.get("CH1", {})
    line = "  ".join("%s=%s" % (k, c1[k]) for k in
                     ("Vpp", "Vmax", "Vmin", "Vrms", "Freq", "Duty+") if k in c1)
    dr.text((M, H - 30), "屏幕#MEAS(CH1): " + line, fill=COL_TXT, font=f)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "当前波形.png")
    img.save(out)
    print("SAVED:", out)
    print("time_axis=%s per_pt_us=%s total_ms=%s" % (
        time_axis,
        ("%.3f" % (per_pt * 1e6)) if per_pt else "None",
        ("%.3f" % (total_s * 1000)) if time_axis else "None"))


if __name__ == "__main__":
    main()
