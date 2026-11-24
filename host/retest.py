#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补测：探头映射、KEY,B 边界、冻结帧一致性、触发电平恢复。"""
import time, serial

PORT = "COM26"; BAUD = 2000000
B_C1PR, B_TLEV, B_C1VZ, B_RUN, B_TDIV = 117, 72, 74, 0, 20

s = serial.Serial(PORT, BAUD, timeout=0.3)
s.dtr = True; s.rts = True
time.sleep(0.3); s.reset_input_buffer()

def send(cmd, wait=1.2):
    s.reset_input_buffer(); s.write((cmd+"\n").encode())
    buf=b""; t0=time.time()
    while time.time()-t0<wait:
        n=s.in_waiting
        if n:
            buf+=s.read(n)
            if b"\n" in buf: break
        else: time.sleep(0.02)
    return buf.decode("ascii",errors="ignore").strip()

def read_prm(wait=2.0):
    s.reset_input_buffer(); s.write(b"#READ\n")
    buf=""; t0=time.time()
    while time.time()-t0<wait:
        n=s.in_waiting
        if n:
            buf+=s.read(n).decode("ascii",errors="ignore")
            if "\n" in buf: break
        else: time.sleep(0.02)
    buf=buf.strip()
    if not buf.startswith("#OK,"): return None
    try: return bytes(int(h,16) for h in buf[4:].split(",") if len(h)==2)
    except ValueError: return None

def wave(wait=5.0):
    s.reset_input_buffer(); s.write(b"#WAV2,3\n")
    buf=""; t0=time.time()
    while time.time()-t0<wait:
        n=s.in_waiting
        if n:
            buf+=s.read(65536).decode("ascii",errors="ignore")
            if "#OK," in buf and buf.rstrip().endswith("\n"): break
        else: time.sleep(0.02)
    parts=[x.strip() for x in buf.split(",") if x.strip()]
    if len(parts)<3 or len(parts[1])<640: return None
    ch1=[int(parts[2][i:i+2],16) for i in range(0,min(len(parts[2]),6000),2)]
    return bytes(ch1)

base = read_prm()
print("快照: run",base[B_RUN],"tdiv_z",base[B_TDIV],"ch1vz",base[B_C1VZ],"probe",base[B_C1PR],"lev",base[B_TLEV])

# --- A. 探头各档：发固件倍率索引 0..6，回读网页映射字节 ---
print("\n== A. 探头倍率映射（发 magnification 索引 -> 回读 SysParam.ch1_probe）==")
# 先把电压档放到中间档，避免边界干扰
send("#SET,CH1,VDIV,3"); time.sleep(0.2)
for mag in range(7):
    send(f"#SET,CH1,PROBE,{mag}"); time.sleep(0.2); p=read_prm()
    print(f"  mag={mag} -> ch1_probe={p[B_C1PR]}  (固件倍率: {['0.5X','1X','10X','20X','50X','100X','1000X'][mag]})")

# --- B. KEY,B 电压档边界：先置低档 idx=1，按 B 两次看是否递增 ---
print("\n== B. #KEY,B 电压档递增（先置 idx=1）==")
send("#SET,CH1,VDIV,1"); time.sleep(0.2); p=read_prm(); print("  起始 volt_zoom=",p[B_C1VZ],"(idx",p[B_C1VZ]-4,")")
for k in range(3):
    r=send("#KEY,B"); time.sleep(0.2); p=read_prm()
    print(f"  KEY,B #{k+1}: resp={r} volt_zoom={p[B_C1VZ]} (idx {p[B_C1VZ]-4})")

# --- C. 冻结帧一致性：STOP 下连抓 4 帧比较 CH1 ---
print("\n== C. STOP 状态冻结帧一致性 ==")
send("#SET,STOP"); time.sleep(0.3)
fr=[wave() for _ in range(4)]
fr=[f for f in fr if f]
print(f"  抓到 {len(fr)} 帧")
for i in range(1,len(fr)):
    diff=sum(1 for a,b in zip(fr[0],fr[i]) if a!=b)
    print(f"  帧{i+1} vs 帧1: 差异点 {diff}/{len(fr[0])}")

# --- D. 触发电平命令语义：连发两个不同 Y，看 ADC 是否单调跟随 ---
print("\n== D. 触发电平跟随（Y 90 -> 200）==")
for y in [90,200]:
    send(f"#SET,TRIG,LEVEL,{y}"); time.sleep(0.2); p=read_prm()
    print(f"  LEVEL,{y} -> level_adc={p[B_TLEV]}")

# --- 恢复 ---
print("\n== 恢复原始快照 ==")
send("#SET,RUN" if base[B_RUN]==1 else "#SET,STOP"); time.sleep(0.2)
send(f"#SET,TDIV,{34-base[B_TDIV]}"); time.sleep(0.2)
send(f"#SET,CH1,VDIV,{base[B_C1VZ]-4}"); time.sleep(0.2)
send(f"#SET,CH1,PROBE,{base[B_C1PR]}"); time.sleep(0.2)
send(f"#SET,TRIG,LEVEL,{base[B_TLEV]}"); time.sleep(0.2)
rest=read_prm()
print("  恢复后: run",rest[B_RUN],"tdiv_z",rest[B_TDIV],"ch1vz",rest[B_C1VZ],"probe",rest[B_C1PR],"lev",rest[B_TLEV])
s.close()
