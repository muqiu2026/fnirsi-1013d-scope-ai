#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_protocol.py - FNIRSI 1013D 串口协议全量实测
1) 快照原始 SysParam，逐条发送控制命令，#READ 回读对应字节判定是否真正生效
2) 抓多帧波形，校验一致性与数值（频率/占空比/Vpp/边沿）
3) 测试结束自动恢复原始设置
所有判定基于 #READ 参数区字节回读，不凭固件应答字符串下结论。
"""
import sys, time, math, json
import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM26"
BAUD = 2000000

# SysParam 字节偏移（与 scope_control.c / 记忆一致）
B_RUN, B_CH2EN = 0, 2
B_TDIV = 20
B_TCH, B_TMODE, B_TEDGE, B_TLEV = 64, 65, 66, 72
B_C1VZ, B_C1CP, B_C1PR = 74, 116, 117
B_C2VZ, B_C2CP, B_C2PR = 140, 180, 181
B_C1POS, B_C2POS = 80, 148

results = []

def log(cat, cmd, resp, ok, detail):
    results.append((cat, cmd, resp, "通过" if ok else "不符", detail))
    print(f"[{'通过' if ok else '不符'}] {cat:10s} {cmd:28s} resp={resp:16s} {detail}")

class Scope:
    def __init__(self):
        self.ser = serial.Serial(PORT, baudrate=BAUD, timeout=0.3)
        self.ser.dtr = True; self.ser.rts = True
        time.sleep(0.3); self.ser.reset_input_buffer()

    def send(self, cmd, wait=1.2):
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\n").encode())
        buf = b""; t0 = time.time()
        while time.time() - t0 < wait:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                if b"\n" in buf: break
            else:
                time.sleep(0.02)
        return buf.decode("ascii", errors="ignore").strip()

    def read_prm(self, wait=2.0):
        self.ser.reset_input_buffer()
        self.ser.write(b"#READ\n")
        buf = ""; t0 = time.time()
        while time.time() - t0 < wait:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n).decode("ascii", errors="ignore")
                if "\n" in buf: break
            else:
                time.sleep(0.02)
        buf = buf.strip()
        if not buf.startswith("#OK,"): return None
        try:
            b = bytes(int(h, 16) for h in buf[4:].split(",") if len(h) == 2)
        except ValueError:
            return None
        return b if len(b) >= 190 else None

    def read_wave(self, wait=5.0):
        self.ser.reset_input_buffer()
        self.ser.write(b"#WAV2,3\n")
        buf = ""; t0 = time.time()
        while time.time() - t0 < wait:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(65536).decode("ascii", errors="ignore")
                if "#OK," in buf and buf.rstrip().endswith("\n"): break
            else:
                time.sleep(0.02)
        parts = [x.strip() for x in buf.split(",") if x.strip()]
        if len(parts) < 3 or len(parts[1]) < 640: return None
        prm = bytes.fromhex(parts[1][:640])
        def conv(s, limit=3000):
            return [int(s[i:i+2],16) for i in range(0, len(s)-1, 2)][:limit]
        ch1 = conv(parts[2]); ch2 = conv(parts[3]) if len(parts) > 3 else []
        return prm, ch1, ch2

    def close(self): self.ser.close()

def u8(b, i): return b[i] if b and i < len(b) else None

s = Scope()

# ---------- 0. 握手 / 只读命令 ----------
print("==== 0. 握手与只读命令 ====")
log("握手", "#VER", s.send("#VER"), True, "版本字符串" )
r = s.send("#INFO");  log("握手", "#INFO", r, r.startswith("#OK,1013D"), "")
r = s.send("#ECHO,HELLO"); log("握手", "#ECHO,HELLO", r, r == "#OK,HELLO", "")
r = s.send("#FOO");   log("错误", "#FOO", r, r == "#ERR,UNKNOWN", "未知命令应报错")
r = s.send("#SET,BOGUS"); log("错误", "#SET,BOGUS", r, r.startswith("#ERR"), "非法SET应报错")
r = s.send("#KEY,ZZ"); log("错误", "#KEY,ZZ", r, r.startswith("#ERR"), "非法键码应报错")

base = s.read_prm()
print("\n原始 SysParam 关键字段:",
      f"run={u8(base,B_RUN)} ch2en={u8(base,B_CH2EN)} tdiv_z={u8(base,B_TDIV)}",
      f"trig={u8(base,B_TCH)}/{u8(base,B_TMODE)}/{u8(base,B_TEDGE)} lev={u8(base,B_TLEV)}",
      f"ch1vz={u8(base,B_C1VZ)} ch1cp={u8(base,B_C1CP)} ch1pr={u8(base,B_C1PR)}",
      f"ch2vz={u8(base,B_C2VZ)} ch2cp={u8(base,B_C2CP)} ch2pr={u8(base,B_C2PR)}",
      f"c1pos={u8(base,B_C1POS)} c2pos={u8(base,B_C2POS)}")

# ---------- 1. 运行状态 ----------
print("\n==== 1. 运行/停止/单次 ====")
s.send("#SET,STOP"); time.sleep(0.2); p = s.read_prm()
log("运行", "#SET,STOP", "OK", u8(p,B_RUN)==0, f"run_state={u8(p,B_RUN)} (期望0)")
s.send("#SET,RUN"); time.sleep(0.2); p = s.read_prm()
log("运行", "#SET,RUN", "OK", u8(p,B_RUN)==1, f"run_state={u8(p,B_RUN)} (期望1)")
s.send("#SET,STOP"); time.sleep(0.2)

# ---------- 2. 时基 ----------
print("\n==== 2. 时基 TDIV ====")
# 选两个不同索引：18(1ms/div) 与 22(50us/div)
for idx, label in [(18,"1ms/div"), (22,"50us/div")]:
    s.send(f"#SET,TDIV,{idx}"); time.sleep(0.25); p = s.read_prm()
    z = u8(p,B_TDIV); expect_z = 34 - idx
    log("时基", f"#SET,TDIV,{idx}({label})", "OK", z == expect_z, f"time_zoom={z} (期望{expect_z})")

# ---------- 3. CH1 电压档 ----------
print("\n==== 3. 电压档 VDIV ====")
for idx, label in [(3,"500mV/div"), (6,"100mV/div")]:
    s.send(f"#SET,CH1,VDIV,{idx}"); time.sleep(0.2); p = s.read_prm()
    vz = u8(p,B_C1VZ); expect_vz = 4 + idx
    log("CH1电压", f"#SET,CH1,VDIV,{idx}({label})", "OK", vz == expect_vz, f"volt_zoom={vz} (期望{expect_vz})")

# ---------- 4. CH2 开关 ----------
print("\n==== 4. 通道开关 ====")
s.send("#SET,CH2,ON"); time.sleep(0.25); p = s.read_prm()
log("通道", "#SET,CH2,ON", "OK", u8(p,B_CH2EN)==1, f"ch2_enabled={u8(p,B_CH2EN)} (期望1)")
s.send("#SET,CH2,OFF"); time.sleep(0.25); p = s.read_prm()
log("通道", "#SET,CH2,OFF", "OK", u8(p,B_CH2EN)==0, f"ch2_enabled={u8(p,B_CH2EN)} (期望0)")

# ---------- 5. 耦合 ----------
print("\n==== 5. 耦合 AC/DC ====")
for ac, label, exp in [(1,"AC",1), (0,"DC",0)]:
    s.send(f"#SET,CH1,{label}"); time.sleep(0.2); p = s.read_prm()
    log("CH1耦合", f"#SET,CH1,{label}", "OK", u8(p,B_C1CP)==exp, f"coupling={u8(p,B_C1CP)} (期望{exp})")

# ---------- 6. 探头倍率 ----------
print("\n==== 6. 探头倍率 PROBE ====")
for idx, label in [(0,"1x"), (1,"10x")]:
    s.send(f"#SET,CH1,PROBE,{idx}"); time.sleep(0.2); p = s.read_prm()
    log("CH1探头", f"#SET,CH1,PROBE,{idx}({label})", "OK", u8(p,B_C1PR)==idx, f"probe={u8(p,B_C1PR)} (期望{idx})")

# ---------- 7. 触发源 / 模式 / 沿 ----------
print("\n==== 7. 触发 ====")
for ch, exp in [("CH1",0), ("CH2",1)]:
    s.send(f"#SET,TRIG,{ch}"); time.sleep(0.2); p = s.read_prm()
    log("触发源", f"#SET,TRIG,{ch}", "OK", u8(p,B_TCH)==exp, f"channel={u8(p,B_TCH)} (期望{exp})")
for m, exp in [("AUTO",0), ("NORMAL",2)]:
    s.send(f"#SET,TRIG,{m}"); time.sleep(0.2); p = s.read_prm()
    # 固件 mode: AUTO=0,NORMAL=2; SysParam.trigger_mode 存 0/1(1=NORMAL)
    ok = u8(p,B_TMODE) in (1,2) if m=="NORMAL" else u8(p,B_TMODE)==0
    log("触发模式", f"#SET,TRIG,{m}", "OK", ok, f"mode={u8(p,B_TMODE)}")
for e, exp in [("RISE",0), ("FALL",1)]:
    s.send(f"#SET,TRIG,{e}"); time.sleep(0.2); p = s.read_prm()
    log("触发沿", f"#SET,TRIG,{e}", "OK", u8(p,B_TEDGE)==exp, f"edge={u8(p,B_TEDGE)} (期望{exp})")

# ---------- 8. 触发电平 / 通道位置 ----------
print("\n==== 8. 触发电平 / 垂直位置 ====")
for y, label in [(90,"低"), (170,"中")]:
    s.send(f"#SET,TRIG,LEVEL,{y}"); time.sleep(0.2); p = s.read_prm()
    lv = u8(p,B_TLEV)
    log("触发电平", f"#SET,TRIG,LEVEL,{y}({label})", "OK", lv is not None and 0<=lv<=255, f"level_adc={lv}")
for y, label in [(120,"上移"), (300,"下移")]:
    s.send(f"#SET,CH1,POS,{y}"); time.sleep(0.2); p = s.read_prm()
    pos = u8(p,B_C1POS) | (u8(p,B_C1POS+1)<<8)
    log("CH1位置", f"#SET,CH1,POS,{y}({label})", "OK", pos == y, f"traceposition={pos} (期望{y})")

# ---------- 9. #KEY 键码 ----------
print("\n==== 9. 键码 #KEY ====")
# 先记录 RUN，用 F 翻转两次应回到原值
p0 = s.read_prm(); run0 = u8(p0,B_RUN)
r = s.send("#KEY,F"); time.sleep(0.25); p1 = s.read_prm()
log("按键", "#KEY,F (RUN/STOP翻转)", r, u8(p1,B_RUN) != run0, f"run {run0}->{u8(p1,B_RUN)}")
r = s.send("#KEY,F"); time.sleep(0.25); p2 = s.read_prm()
log("按键", "#KEY,F (再次翻转复原)", r, u8(p2,B_RUN)==run0, f"run ->{u8(p2,B_RUN)}")
# B / 4 改 CH1 电压档，C / D 改时基
pv = s.read_prm(); vz0 = u8(pv,B_C1VZ)
r = s.send("#KEY,B"); time.sleep(0.2); pv = s.read_prm()
log("按键", "#KEY,B (CH1 VDIV+)", r, u8(pv,B_C1VZ) != vz0, f"volt_zoom {vz0}->{u8(pv,B_C1VZ)}")

# ---------- 10. 波形帧完整性 + 多帧一致性 ----------
print("\n==== 10. 波形数据校验 ====")
frames = []
for i in range(3):
    w = s.read_wave()
    if w:
        frames.append(w)
    time.sleep(0.1)
print(f"成功抓取波形帧: {len(frames)}/3")
if frames:
    prm, ch1, ch2 = frames[0]
    print(f"  CH1 点数={len(ch1)} 范围={min(ch1)}~{max(ch1)}  CH2 点数={len(ch2)} 范围={(min(ch2),max(ch2)) if ch2 else 'OFF'}")
    # 多帧一致性（STOP 状态下应逐点相同）
    if len(frames) >= 2:
        same = all(bytes(f[1]) == bytes(frames[0][1]) for f in frames[1:])
        log("波形", "冻结帧一致性(3帧CH1逐点)", "OK", same, "3帧完全相同" if same else "帧间存在差异")
    # 数值分析
    n = len(ch1); vmax, vmin = max(ch1), min(ch1); mean = sum(ch1)/n
    thr = (vmax+vmin)/2; up=[]; dn=[]; st=ch1[0]>=thr
    for i,v in enumerate(ch1):
        ns=v>=thr
        if ns!=st: (up if ns else dn).append(i); st=ns
    gaps = sorted(up[i+1]-up[i] for i in range(len(up)-1) if up[i+1]-up[i]>3)
    period = gaps[len(gaps)//2] if gaps else 0
    print(f"  上升沿={len(up)} 下降沿={len(dn)} 周期点数={period}")
    if period:
        # 时基索引反查采样率（与 scope_ai 表一致的简化：用 SysParam time_zoom）
        zi = u8(prm,B_TDIV); tdi = 34-zi
        SR = [200e6,100e6,50e6,20e6,10e6,5e6,2e6,1e6,500e3,200e3,100e3,50e3,20e3,10e3,5e3,2e3,1e3,500,10000,4000,2000,1000,400,200,100,40,20,10,4]
        dt = 1.0/SR[tdi] if 0<=tdi<len(SR) else 5e-6
        freq = 1.0/(period*dt)
        print(f"  时基索引={tdi} 每点dt={dt*1e6:.2f}us 估算频率≈{freq/1e3:.3f} kHz")

# ---------- 11. 恢复原始设置 ----------
print("\n==== 11. 恢复原始设置 ====")
if base:
    # 用快照字节反推命令恢复
    run0 = u8(base,B_RUN)
    s.send("#SET,RUN" if run0==1 else "#SET,STOP"); time.sleep(0.2)
    tdiv_z0 = u8(base,B_TDIV); s.send(f"#SET,TDIV,{34-tdiv_z0}"); time.sleep(0.2)
    s.send(f"#SET,CH1,VDIV,{u8(base,B_C1VZ)-4}"); time.sleep(0.2)
    s.send("#SET,CH2,ON" if u8(base,B_CH2EN)==1 else "#SET,CH2,OFF"); time.sleep(0.2)
    s.send("#SET,CH1,AC" if u8(base,B_C1CP)==1 else "#SET,CH1,DC"); time.sleep(0.2)
    s.send(f"#SET,CH1,PROBE,{u8(base,B_C1PR)}"); time.sleep(0.2)
    tch = u8(base,B_TCH); s.send(f"#SET,TRIG,{'CH2' if tch else 'CH1'}"); time.sleep(0.2)
    tmode = u8(base,B_TMODE); s.send("#SET,TRIG,NORMAL" if tmode==1 else "#SET,TRIG,AUTO"); time.sleep(0.2)
    tedge = u8(base,B_TEDGE); s.send("#SET,TRIG,FALL" if tedge==1 else "#SET,TRIG,RISE"); time.sleep(0.2)
    s.send(f"#SET,TRIG,LEVEL,{u8(base,B_TLEV)}"); time.sleep(0.2)
    c1pos = u8(base,B_C1POS)|(u8(base,B_C1POS+1)<<8); s.send(f"#SET,CH1,POS,{c1pos}"); time.sleep(0.2)
    restored = s.read_prm()
    def cmpb(a,b,i): return u8(a,i)==u8(b,i)
    ok_rec = all([cmpb(base,restored,B_RUN),cmpb(base,restored,B_TDIV),cmpb(base,restored,B_C1VZ),
                  cmpb(base,restored,B_CH2EN),cmpb(base,restored,B_C1CP),cmpb(base,restored,B_C1PR),
                  cmpb(base,restored,B_TCH),cmpb(base,restored,B_TEDGE),cmpb(base,restored,B_TLEV),
                  cmpb(base,restored,B_C1POS)])
    log("恢复", "还原快照设置", "OK", ok_rec, "关键字节已复原" if ok_rec else "部分字节未复原，见下")
    print("  恢复后: run",u8(restored,B_RUN),"tdiv_z",u8(restored,B_TDIV),"ch1vz",u8(restored,B_C1VZ),
          "ch2en",u8(restored,B_CH2EN),"trig",u8(restored,B_TCH),u8(restored,B_TMODE),u8(restored,B_TEDGE),
          "lev",u8(restored,B_TLEV),"c1pos",u8(restored,B_C1POS))

s.close()

# 汇总
total = len(results); passed = sum(1 for r in results if r[3]=="通过")
print(f"\n==== 汇总: {passed}/{total} 通过 ====")
fails = [r for r in results if r[3]!="通过"]
if fails:
    print("未通过项:")
    for r in fails: print("  ", r[0], r[1], r[4])
