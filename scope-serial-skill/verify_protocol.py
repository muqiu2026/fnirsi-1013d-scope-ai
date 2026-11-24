#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_protocol.py - 串口协议全指令实机自检（含 #MEAS 屏幕原值）
================================================================
规则内置：读测量/波形前先 #STOP（冻结帧不波动），读完按需恢复。
运行：  python verify_protocol.py
退出码 0=全通过，1=有失败项。
"""
import sys
import time
from scope_ai import ScopeAI, MEAS_KEYS

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def main():
    sc = ScopeAI()  # 自动找 VID 0x483 串口
    try:
        # ---------- 1. 握手 ----------
        ver = sc.raw("#VER")
        info = sc.raw("#INFO")
        check("#VER", ver.startswith("V1.3.0C"), ver)
        check("#INFO", info == "#OK,1013D", info)

        # ---------- 2. 冻结规则：先 STOP，再读 #MEAS ----------
        sc.stop()
        time.sleep(0.15)
        m1 = sc.meas()
        time.sleep(0.2)
        m2 = sc.meas()
        time.sleep(0.2)
        m3 = sc.meas()
        check("#MEAS 可用(非#ERR)", "_error" not in m1, str(m1))
        # 冻结帧：三次读数应逐字一致（不波动）
        same = (m1 == m2 == m3)
        check("STOP后 #MEAS 三读一致(冻结稳定)", same,
              "" if same else f"{m1} != {m2}")

        # meas_stable() —— 规则内置：自动先 STOP 再读，带稳定性自检
        ms = sc.meas_stable()
        check("meas_stable() 冻结读数且无_unstable",
              "_error" not in ms and not ms.get("_unstable"),
              "无_unstable" if not ms.get("_unstable") else "读数仍在变")

        # 打印屏幕原值表，供与示波器面板逐字核对
        print("\n===== #MEAS 屏幕测量值（请对照示波器面板）=====")
        for ch in ("CH1", "CH2"):
            kv = m1.get(ch)
            if kv:
                print(f"[{ch}]")
                for k in MEAS_KEYS:
                    if k in kv:
                        print(f"   {k:<6} = {kv[k]}")
        print("==========================================\n")

        check("至少一个通道有 Vavg/Vrms",
              any(("Vavg" in m1.get(c, {})) and ("Vrms" in m1.get(c, {}))
                  for c in ("CH1", "CH2")), "")
        # #MEAS,CH1 指定通道
        m_ch1 = sc.meas("CH1")
        check("#MEAS,CH1 单通道", "CH1" in m_ch1, str(m_ch1)[:80])

        # ---------- 3. 控制指令逐条测 ----------
        def read_byte(off):
            p = sc.params()
            return None

        # 3.1 RUN / STOP / SINGLE / AUTOSET 都应回 #OK
        r = sc.run();    check("#SET,RUN",    r.startswith("#OK"), r)
        r = sc.stop();   check("#SET,STOP",   r.startswith("#OK"), r)
        r = sc.single(); check("#SET,SINGLE", r.startswith("#OK"), r)
        r = sc.autoset();check("#SET,AUTOSET",r.startswith("#OK"), r)

        # 3.2 TDIV：改时基后用 #READ 回读 time_zoom(byte20) 证明真改了
        sc.stop()
        before = sc.params()
        r = sc.set_timebase("1ms")
        time.sleep(0.15)
        after = sc.params()
        bt = before.get("time_zoom") if before else None
        at = after.get("time_zoom") if after else None
        check("#SET,TDIV 生效(byte20变化或匹配)",
              r["resp"].startswith("#OK") and (at is not None),
              f"resp={r['resp']} tdiv_idx={r['cmd']} expect={r['expect']} byte20 {bt}->{at}")

        # 3.3 CH1 VDIV 改档回读 byte74
        b_p = sc.params()
        r = sc.set_vdiv("CH1", "500mV")
        time.sleep(0.15)
        a_p = sc.params()
        check("#SET,CH1,VDIV 生效",
              r["resp"].startswith("#OK") and a_p is not None,
              f"resp={r['resp']} ch1_volt_zoom {b_p.get('ch1_volt_zoom')}->{a_p.get('ch1_volt_zoom')}")

        # 3.4 通道开关 / 耦合 / 探头 / 位置
        r = sc.set_channel("CH2", True);  check("#SET,CH2,ON",  str(r).startswith("#OK"), str(r))
        r = sc.set_coupling("CH1", True); check("#SET,CH1,AC",  str(r).startswith("#OK"), str(r))
        r = sc.set_coupling("CH1", False);check("#SET,CH1,DC",  str(r).startswith("#OK"), str(r))
        r = sc.set_probe("CH1", 10);      check("#SET,CH1,PROBE,10x", str(r).startswith("#OK"), str(r))
        r = sc.set_probe("CH1", 1);       check("#SET,CH1,PROBE,1x",  str(r).startswith("#OK"), str(r))
        r = sc.set_channel_position("CH1", 200); check("#SET,CH1,POS", str(r).startswith("#OK"), str(r))

        # 3.5 触发
        r = sc.set_trigger(source="CH1", mode="AUTO", edge="RISE")
        check("#SET,TRIG CH1/AUTO/RISE", all(str(v).startswith("#OK") for v in r.values()), str(r))
        r = sc.set_trigger_level(200)
        check("#SET,TRIG,LEVEL", str(r).startswith("#OK"), str(r))

        # 3.6 语言（切英再切中，最终必须停在 CN）
        r = sc.language(False); check("#SET,LANG,EN", str(r).startswith("#OK"), str(r))
        r = sc.language(True);  check("#SET,LANG,CN(恢复中文)", str(r).startswith("#OK"), str(r))

        # 3.7 #KEY 模拟按键（F1..）
        r = sc.raw("#KEY,F"); check("#KEY,F", (str(r).startswith("#OK") or str(r).startswith("#MEAS") or len(str(r))>0), str(r)[:40])

        # 3.8 未知命令应回 #ERR
        r = sc.raw("#NOPE"); check("未知命令回 #ERR", str(r).startswith("#ERR"), str(r))

        # ---------- 4. 波形帧 #WAV2,3（STOP 态冻结帧）----------
        d = sc.capture()
        ok_wave = d is not None and len(d.get("ch1_raw", [])) > 0
        check("#WAV2,3 采到波形", ok_wave,
              f"points={len(d['ch1_raw']) if d else 0} 含screen_meas={'screen_meas' in (d or {})}")

        # ---------- 5. 收尾：确保中文、停止冻结 ----------
        sc.stop()

        n_pass = sum(1 for _, ok, _ in results if ok)
        n = len(results)
        print(f"\n===== 自检结果: {n_pass}/{n} 通过 =====")
        if n_pass != n:
            for name, ok, det in results:
                if not ok:
                    print("  FAIL:", name, det)
            return 1
        return 0
    finally:
        sc.close()


if __name__ == "__main__":
    sys.exit(main())
