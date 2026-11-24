#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断脚本：精细检查 #READ / #WAV2,3 / #KEY / #SET,AUTOSET 的真实行为"""
import serial, time, sys

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM26"
ser = serial.Serial(PORT, 115200, timeout=0.2)
ser.dtr = True
ser.rts = True
time.sleep(0.3)


def drain():
    ser.reset_input_buffer()


def read_line(wait=2.0):
    buf = b""
    t0 = time.time()
    while time.time() - t0 < wait:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
            if b"\n" in buf:
                break
        else:
            time.sleep(0.02)
    return buf.decode("ascii", errors="ignore").strip()


print("=" * 70)
print("[1] #READ 原始结构分析")
drain()
ser.write(b"#READ\n")
time.sleep(1.2)
raw = ser.read(8000).decode("ascii", errors="ignore")
print("    原始长度:", len(raw), "以\\n结尾:", raw.endswith("\n"))
body = raw.strip()
print("    前缀:", body[:10])
hexes = body[4:].split(",")
print("    逗号分隔元素数:", len(hexes))
bad = [(i, h) for i, h in enumerate(hexes) if len(h) != 2]
print("    长度!=2 的元素:", bad[:10], "共", len(bad))
print("    首5:", hexes[:5], " 末5:", hexes[-5:])
prm = bytes(int(h, 16) for h in hexes if len(h) == 2)
print("    有效字节数:", len(prm))
print("    byte[0]run_state=%d byte[2]ch2=%d byte[20]time_zoom=%d byte[64]trigch=%d"
      % (prm[0], prm[2], prm[20], prm[64]))
print("    byte[72]trigger_level=%d  byte[80:82]ch1_vert=%s  byte[148:150]ch2_vert=%s"
      % (prm[72], list(prm[80:82]), list(prm[148:150])))

print("=" * 70)
print("[2] #WAV2,3 波形帧完整性")
drain()
ser.write(b"#WAV2,3\n")
t0 = time.time()
buf = b""
while time.time() - t0 < 8:
    n = ser.in_waiting
    if n:
        buf += ser.read(n)
        if buf.endswith(b"\n") and len(buf) > 100:
            break
    else:
        time.sleep(0.02)
frame = buf.decode("ascii", errors="ignore").strip()
parts = frame.split(",")
print("    总长:", len(frame), " 段数:", len(parts))
print("    段0:", parts[0], " 段1长度:", len(parts[1]) if len(parts) > 1 else "-")
if len(parts) >= 3:
    ch1 = bytes.fromhex(parts[2]) if len(parts[2]) % 2 == 0 and len(parts[2]) >= 2 else b""
    ch2 = bytes.fromhex(parts[3]) if len(parts) > 3 else b""
    print("    CH1 字节数:", len(ch1), " CH2 字节数:", len(ch2))
    if ch1:
        print("    CH1 前16:", list(ch1[:16]))
        print("    CH1 统计: min=%d max=%d 不同值个数=%d" % (min(ch1), max(ch1), len(set(ch1))))
        nz = sum(1 for b in ch1 if b != 128)
        print("    CH1 非128点占比: %.1f%%" % (100.0 * nz / len(ch1)))
    if ch2:
        print("    CH2 统计: min=%d max=%d 不同值=%d" % (min(ch2), max(ch2), len(set(ch2))))

print("=" * 70)
print("[3] #SET,AUTOSET 耗时测试（等 20s）")
drain()
ser.write(b"#SET,AUTOSET\n")
t0 = time.time()
resp = ""
while time.time() - t0 < 20:
    n = ser.in_waiting
    if n:
        resp += ser.read(n).decode("ascii", errors="ignore")
        if "\n" in resp:
            break
    else:
        time.sleep(0.05)
print("    耗时 %.2fs 回复: %r" % (time.time() - t0, resp.strip()))

print("=" * 70)
print("[4] #KEY 语义测试（单字符 vs 双位hex）")
for cmd in ["#KEY,F", "#KEY,A", "#KEY,3", "#KEY,46", "#KEY,41", "#KEY,33"]:
    drain()
    ser.write((cmd + "\n").encode())
    time.sleep(0.6)
    r = read_line(1.5)
    print("    %-10s -> %r" % (cmd, r))

ser.close()
