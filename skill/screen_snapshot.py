# -*- coding: utf-8 -*-
"""读取当前示波器屏幕显示内容：设置(#READ) + 冻结测量值(#MEAS)，读完恢复运行态。"""
from scope_ai import ScopeAI, MEAS_KEYS

TM = {0: "AUTO", 1: "NORMAL", 2: "SINGLE"}


def coupling(v):
    return "AC" if v else "DC"


def main():
    sc = ScopeAI()
    ver = sc.raw("#VER")
    info = sc.raw("#INFO")
    p = sc.params() or {}

    print("型号/版本:", info.strip(), "|", ver.strip())
    print()
    print("===== 屏幕设置 =====")
    print("运行状态 :", p.get("run"))
    print("时基     :", p.get("timebase"), "  采样率:", p.get("sample_rate"))
    print("CH1      :", p.get("ch1_vdiv"), coupling(p.get("ch1_coupling")),
          p.get("ch1_probe_text"), " 位置Y=", p.get("ch1_vert"))
    print("CH2(%s):" % ("ON" if p.get("ch2_enabled") else "OFF"),
          p.get("ch2_vdiv"), coupling(p.get("ch2_coupling")),
          p.get("ch2_probe_text"), " 位置Y=", p.get("ch2_vert"))
    print("触发     : 源CH%s  %s  %s  电平=%s/255" % (
        p.get("trigger_channel", 0) + 1,
        TM.get(p.get("trigger_mode"), "?"),
        "FALL" if p.get("trigger_edge") else "RISE",
        p.get("trigger_level")))

    print()
    print("===== 屏幕测量值 (#MEAS 原值，冻结读取，读完恢复原运行态) =====")
    m = sc.meas_stable(restore=True)
    for ch in ("CH1", "CH2"):
        kv = m.get(ch)
        if kv:
            print("[%s]" % ch)
            for k in MEAS_KEYS:
                if k in kv:
                    print("   %-6s = %s" % (k, kv[k]))
    if m.get("_error"):
        print("ERROR:", m["_error"])
    if m.get("_unstable"):
        print("(注：读数仍轻微波动)")
    sc.close()


if __name__ == "__main__":
    main()
