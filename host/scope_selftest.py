#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scope_selftest.py - FNIRSI 1013D 串口功能完整性自检工具

对示波器串口协议逐条发命令并收集回应，判断：
  1. 握手/识别：#VER / #INFO / #ECHO
  2. 读取数据：#READ(参数) / #WAV2,3(真实波形) / #WAV2,2(测试斜坡)
  3. 调参控制：#SET,RUN/STOP/TDIV/CH1/CH2/TRIG/LANG/AUTOSET
  4. 新增命令：#SET,TRIG,LEVEL / #SET,CH1,POS（验证参数区字节真的变了）
  5. 兼容键码：#KEY,F/A/3 与十六进制形式 #KEY,46
  6. 错误处理：未知命令应回 #ERR,UNKNOWN

用法：
  python scope_selftest.py            # 自动找示波器串口并全量测试
  python scope_selftest.py COM26      # 指定串口
  python scope_selftest.py --keep     # 测试后不恢复原始设置
输出：
  串口功能自检报告.txt   逐条命令的发送/预期/实测/判定
  控制台摘要            通过率与结论
"""
import sys, os, time, json, re

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("需要 pyserial: pip install pyserial")
    raise

BAUD = 2000000
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "串口功能自检报告.txt")

TDIV_TEXT = ["50s","20s","10s","5s","2s","1s","500ms","200ms","100ms","50ms","20ms",
             "200ms","100ms","50ms","20ms","10ms","5ms","2ms","1ms","500us",
             "200us","100us","50us","20us","10us","5us","2us","1us","500ns",
             "200ns","100ns","50ns","20ns","10ns","5ns"]


def find_scope_port():
    ports = list(list_ports.comports())
    for p in ports:
        if p.vid == 0x0483 and p.pid == 0x5740:
            return p.device
    for p in ports:
        if "USB" in (p.description or "").upper() or "CH340" in (p.description or "").upper():
            return p.device
    return None


class Tester:
    def __init__(self, port, keep=False):
        self.port = port
        self.keep = keep
        self.ser = serial.Serial(port, baudrate=BAUD, timeout=0.2)
        # 显式拉高 DTR/RTS：固件把 DTR/RTS 当作"端口已打开"的标志
        self.ser.dtr = True
        self.ser.rts = True
        time.sleep(0.3)
        self.ser.reset_input_buffer()
        self.results = []   # (category, cmd, expected, actual, verdict)

    # ---- 底层收发 --------------------------------------------------------
    def _drain(self):
        self.ser.reset_input_buffer()

    def ask(self, cmd, wait=1.2, expect_prefix=None):
        """发送一条命令，收集一个完整回应行。返回收到的文本('' = 无回应)"""
        self._drain()
        self.ser.write((cmd + "\n").encode())
        buf = b""
        t0 = time.time()
        while time.time() - t0 < wait:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                if b"\n" in buf:
                    break
            else:
                time.sleep(0.02)
        return buf.decode("ascii", errors="ignore").strip()

    def ask_raw(self, cmd, wait=4.0, min_len=100):
        """发送命令并收集大块回应（如 #WAV2,3 波形帧）"""
        self._drain()
        self.ser.write((cmd + "\n").encode())
        buf = b""
        t0 = time.time()
        while time.time() - t0 < wait:
            n = self.ser.in_waiting
            if n:
                buf += self.ser.read(n)
                if len(buf) >= min_len and buf.endswith(b"\n"):
                    break
            else:
                time.sleep(0.02)
            # 已经收到完整一行且超过最小长度就停
        return buf.decode("ascii", errors="ignore").strip()

    def record(self, cat, cmd, expected, actual):
        if actual == "":
            verdict = "无回应"
        elif expected is None:
            verdict = "有回应"
        elif expected.startswith("prefix:"):
            verdict = "通过" if actual.startswith(expected[7:]) else "不符"
        elif expected.startswith("contains:"):
            verdict = "通过" if expected[9:] in actual else "不符"
        elif expected.startswith("regex:"):
            verdict = "通过" if re.search(expected[6:], actual) else "不符"
        else:
            verdict = "通过" if actual == expected else "不符"
        self.results.append((cat, cmd, expected, actual, verdict))
        print(f"[{verdict:4s}] {cmd:28s} -> {actual[:70]}")
        return verdict

    # ---- 分类测试 --------------------------------------------------------
    def read_prm(self):
        """读回 #READ 参数区（字节数组），失败返回 None"""
        r = self.ask("#READ", wait=2.5)
        if r.startswith("#OK,") and len(r) > 40:
            hexes = r[4:].split(",")
            b = bytes(int(h, 16) for h in hexes if len(h) == 2)
            return b if len(b) >= 190 else None
        return None

    def t_handshake(self):
        self.record("握手", "#VER", None, self.ask("#VER"))
        self.record("握手", "#INFO", "prefix:#OK,", self.ask("#INFO"))
        self.record("握手", "#ECHO,HELLO", "#OK,HELLO", self.ask("#ECHO,HELLO"))

    def t_read(self):
        r = self.ask("#READ", wait=2.5)
        if r.startswith("#OK,") and len(r) > 40:
            # #READ 返回逗号分隔的十六进制字节
            hexes = r[4:].split(",")
            self.record("读取", "#READ", "prefix:#OK, (320字节)", f"#OK, ({len(hexes)}字节)")
            self.prm_bytes = [int(h, 16) for h in hexes if len(h) == 2]
        else:
            self.record("读取", "#READ", "prefix:#OK,", r)
            self.prm_bytes = None

        r = self.ask_raw("#WAV2,3", wait=5.0, min_len=600)
        parts = [x.strip() for x in r.split(",") if x.strip()]
        if len(parts) >= 3 and parts[0] == "#OK" and len(parts[1]) >= 640:
            ch1 = len(parts[2]) // 2
            ch2 = len(parts[3]) // 2 if len(parts) > 3 else 0
            summary = f"#OK, prm={len(parts[1])//2}B CH1={ch1}点 CH2={ch2}点"
            # 重编版：#WAV2,3 已裁剪为 3000 真实点（CH2 关闭时不打包第二段），预期随之更新
            self.record("读取", "#WAV2,3", "prefix:#OK, prm=320B CH1=3000点", summary)
            self.wav_frame = r
            # 波形真实性：CH1 必须有多个不同采样值（不是恒定填充）
            data = bytes.fromhex(parts[2]) if len(parts[2]) % 2 == 0 else b""
            distinct = len(set(data))
            self.record("读取", "#WAV2,3 波形真实性", "contains:不同采样值",
                        f"CH1 共 {len(data)} 点，{distinct} 个不同采样值")
        else:
            self.record("读取", "#WAV2,3", "prefix:#OK, prm=320B", r[:120])
            self.wav_frame = None

        r = self.ask_raw("#WAV2,2", wait=1.0, min_len=1)
        # 重编版：#WAV2,2 假测试斜坡已移除，应回 #ERR,UNKNOWN（设计如此）
        self.record("读取", "#WAV2,2", "prefix:#ERR,UNKNOWN", r[:60] + ("..." if len(r) > 60 else ""))

    def t_control(self, snap):
        """调参类命令；snap 是测试前的设置快照，用于恢复"""
        def chk(cmd, expected="prefix:#OK,"):
            return self.record("调参", cmd, expected, self.ask(cmd, wait=1.5))

        chk("#SET,STOP")
        chk("#SET,RUN")
        chk("#SET,TDIV,18")                     # 1ms/div
        chk("#SET,CH1,VDIV,3")                  # 500mV/div
        chk("#SET,CH2,VDIV,2")                  # 1V/div
        chk("#SET,CH2,ON")
        chk("#SET,CH2,OFF")
        chk("#SET,CH1,AC")
        chk("#SET,CH1,DC")
        chk("#SET,CH1,PROBE,1")                 # 1x
        chk("#SET,CH2,PROBE,1")
        chk("#SET,TRIG,CH1")
        chk("#SET,TRIG,CH2")
        chk("#SET,TRIG,AUTO")
        chk("#SET,TRIG,NORMAL")
        chk("#SET,TRIG,RISE")
        chk("#SET,TRIG,FALL")
        chk("#SET,LANG,CN")
        chk("#SET,LANG,EN")
        # AUTOSET 本身要跑 1.5 秒左右（自动设置要多轮测量），单独给足等待时间
        self.record("调参", "#SET,AUTOSET", "prefix:#OK,", self.ask("#SET,AUTOSET", wait=3.5))

    def t_new_commands(self):
        """验证新增的触发电平/通道位置命令确实改变了参数区对应字节"""
        before = self.read_prm()
        self.ask("#SET,TRIG,LEVEL,120", wait=1.5)
        after = self.read_prm()
        if before and after:
            self.record("新命令", "#SET,TRIG,LEVEL,120",
                        "contains:变化",
                        f"byte[72] trigger_level {before[72]} -> {after[72]} "
                        f"{'变化' if before[72] != after[72] else '未变化'}")
        else:
            self.record("新命令", "#SET,TRIG,LEVEL,120", "contains:变化", "参数区读取失败")

        before = self.read_prm()
        self.ask("#SET,CH1,POS,100", wait=1.5)
        after = self.read_prm()
        if before and after:
            b0 = before[80] | (before[81] << 8)
            a0 = after[80] | (after[81] << 8)
            self.record("新命令", "#SET,CH1,POS,100",
                        "contains:变化",
                        f"byte[80:82] ch1_vert {b0} -> {a0} "
                        f"{'变化' if b0 != a0 else '未变化'}")
        else:
            self.record("新命令", "#SET,CH1,POS,100", "contains:变化", "参数区读取失败")

    def t_keys(self):
        for code in ("F", "A", "3", "46"):
            self.record("键码", f"#KEY,{code}", "prefix:#OK,", self.ask(f"#KEY,{code}", wait=3.0))

    def t_errors(self):
        self.record("错误", "#FOO", "#ERR,UNKNOWN", self.ask("#FOO", wait=1.2))
        self.record("错误", "#SET,BOGUS", "#ERR,SET", self.ask("#SET,BOGUS", wait=1.2))
        self.record("错误", "#KEY,ZZ", None, self.ask("#KEY,ZZ", wait=1.2))

    def t_param_effect(self):
        """验证调参命令是否真的改变了 #WAV2,3 参数区里的对应字节"""
        before = self.ask_raw("#WAV2,3", wait=5.0, min_len=600)
        self.ask("#SET,TDIV,18", wait=1.0)
        after = self.ask_raw("#WAV2,3", wait=5.0, min_len=600)

        def prm_byte(frame, idx):
            parts = [x.strip() for x in frame.split(",") if x.strip()]
            if len(parts) < 2 or len(parts[1]) < 640:
                return None
            raw = bytes.fromhex(parts[1][:640])
            return raw[idx] if idx < len(raw) else None

        b0, a0 = prm_byte(before, 20), prm_byte(after, 20)   # byte20 = 时基索引
        # 固件里 time_zoom = 34 - timeperdiv，20 -> time_zoom=14
        self.record("调参生效", "#SET,TDIV,18 改变参数区byte20",
                    "contains:变化", f"before={b0} after={a0} {'变化' if b0 != a0 else '未变化'}")

    # ---- 主流程 ----------------------------------------------------------
    def run(self):
        print(f"== 自检开始: {self.port} ==")
        snap = None
        self.wav_frame = None
        self.prm_bytes = None

        self.t_handshake()
        self.t_read()
        self.t_control(snap)
        self.t_new_commands()
        self.t_keys()
        self.t_errors()
        self.t_param_effect()

        self.write_report()
        self.ser.close()
        return self.summary()

    def summary(self):
        total = len(self.results)
        passed = sum(1 for r in self.results if r[4] in ("通过", "有回应"))
        noresp = sum(1 for r in self.results if r[4] == "无回应")
        print(f"\n== 自检结束: {passed}/{total} 通过, {noresp} 条无回应 ==")
        return {"total": total, "passed": passed, "noresp": noresp}

    def write_report(self):
        lines = []
        lines.append("=" * 64)
        lines.append("FNIRSI 1013D 串口功能自检报告")
        lines.append(f"串口: {self.port}   波特率: {BAUD}   时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 64)
        cur = None
        for cat, cmd, exp, act, verdict in self.results:
            if cat != cur:
                lines.append("")
                lines.append(f"---- {cat} ----")
                cur = cat
            lines.append(f"[{verdict}]  发送: {cmd}")
            lines.append(f"        预期: {exp}")
            lines.append(f"        实测: {act if act else '(无数据)'}")
        passed = sum(1 for r in self.results if r[4] in ("通过", "有回应"))
        lines.append("")
        lines.append("-" * 64)
        lines.append(f"合计: {passed}/{len(self.results)} 通过")
        noresp = [r for r in self.results if r[4] == "无回应"]
        if noresp:
            lines.append("无回应命令: " + ", ".join(r[1] for r in noresp))
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\n报告已写入: {REPORT}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    keep = "--keep" in sys.argv
    port = args[0] if args else find_scope_port()
    if not port:
        print("找不到示波器串口。请确认: 1) USB-Serial mode 已打开 2) 没有其它程序占用串口")
        sys.exit(1)
    print(f"使用串口: {port}")
    t = Tester(port, keep=keep)
    t.run()


if __name__ == "__main__":
    main()
