#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键重建"示波器波形界面"：

  1. 通过串口冻结采集（#SET,STOP）后抓一帧波形（#WAV2,3）与参数区（#READ）
  2. 实机标定"触发电平(ADC) -> 屏幕 Y"的换算（在当前电压档位下）
  3. 生成自包含 HTML（示波器屏幕布局还原 + 缩放/平移/读数）

默认冻结抓帧：暂停 → 连抓 3 帧逐字节校验 → 自动恢复原来的运行状态。
加 --live 可改回"边跑边读"（数据会抖动，一般只在需要观察实时变化时用）。

用法：
  python rebuild_scope_view.py [COM口] [--live]
"""
import sys, os, time, json, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import scope_ai


def grab(port, freeze=True):
    s = scope_ai.ScopeAI(port)
    try:
        p0 = s.params()

        # —— 标定触发线：level(tvp) = lvl0 + (tvp - tvp0) * k ——
        base = p0["ch1_vert"]
        pts = {}
        for y in (base, base + 50, base + 100):
            s.set_trigger_level(y)
            time.sleep(0.35)
            pts[y] = s.params()["trigger_level"]
        k = (pts[base + 100] - pts[base]) / 100.0
        lvl0 = pts[base]
        # 恢复原来的触发电平
        y_orig = base + (p0["trigger_level"] - lvl0) / k
        s.set_trigger_level(round(y_orig))
        time.sleep(0.35)

        # —— 抓帧（默认冻结，保证整帧自洽、可复现）——
        d = s.capture_stable(frames=3) if freeze else s.capture()
        if not d:
            raise RuntimeError("没有收到波形数据")

        cal = {"cal": pts, "k": k, "base_tvp": base, "level_at_base": lvl0,
               "y_orig": y_orig, "trigger_level": p0["trigger_level"],
               "ch1_vert": p0["ch1_vert"], "ch1_scale": p0["ch1_scale"],
               "ch1_vdiv": p0["ch1_vdiv"]}
        json.dump(cal, open(os.path.join(BASE, "scope_cal.json"), "w"))
        json.dump({"params": d["params"], "ch1": list(d["ch1_raw"]),
                   "ch2": list(d["ch2_raw"])},
                  open(os.path.join(BASE, "scope_screen.json"), "w", encoding="utf-8"))
        tag = ("冻结抓帧 %d 帧校验一致" % d["frames_verified"]) if freeze else "实时抓帧"
        print("[grab] %s: Vpp=%.3fV 频率=%sHz 时基=%s CH1=%s 触发线标定 k=%.4f"
              % (tag, d["CH1"]["vpp"], d["CH1"]["freq_hz"],
                 d["params"]["timebase"], d["params"]["ch1_vdiv"], k))
        return d["params"]
    finally:
        s.close()


if __name__ == "__main__":
    argv = sys.argv[1:]
    port = next((a for a in argv if not a.startswith("--")), "COM26")
    freeze = "--live" not in argv
    grab(port, freeze=freeze)
    r = subprocess.run([sys.executable, os.path.join(BASE, "build_scope_view.py")],
                       capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
