#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FNIRSI 1013D 示波器 MCP server（stdio, JSON-RPC 2.0, 自包含）

让支持 MCP 的 AI 客户端（Claude Desktop / Cursor / 自建 Agent）直接调用一台
FNIRSI / ADS 1013D 平板示波器：读屏幕测量值、抓波形、改设置、运行控制、整屏截图。

依赖：pip install pyserial          （截图功能另需 pillow，缺失会返回友好提示）
硬件：示波器已刷含 #MEAS/#SHOT 的改造固件，菜单已开 USB-Serial mode（见 docs/FLASHING.md）。

运行：python mcp/server.py            # 自动按 VID 0x0483 找串口
      python mcp/server.py --port COM26 --baud 2000000

协议版本：MCP 2024-11-05。传输：stdin/stdout 逐行 JSON-RPC（newline-delimited）。
"""
import sys, json, time, argparse

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.stderr.write("缺少 pyserial，请先： pip install pyserial\n")
    sys.exit(1)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "fnirsi-1013d-scope"
SERVER_VERSION = "1.0.0"
VID = 0x0483


# --------------------------------------------------------------------------- #
# 串口层
# --------------------------------------------------------------------------- #
class Scope:
    """对 FNIRSI 1013D 的 #命令协议做最小封装（自包含，不依赖 scope_ai.py）。"""

    def __init__(self, port=None, baud=2000000):
        self._ser = None
        self._port = port or self._autoport()
        self._baud = baud

    @staticmethod
    def _autoport():
        for p in list_ports.comports():
            if p.vid == VID:
                return p.device
        return None

    def _open(self):
        if self._ser and self._ser.is_open:
            return self._ser
        if not self._port:
            raise RuntimeError("未找到 VID=0x%04X 的串口；请用 --port 指定 COM 号，"
                               "并确认菜单已打开 USB-Serial mode" % VID)
        self._ser = serial.Serial(self._port, self._baud, timeout=1.0)
        time.sleep(0.05)
        return self._ser

    def query(self, cmd, timeout=2.0):
        """发送一行命令，读回一行 ascii 应答。"""
        s = self._open()
        s.reset_input_buffer()
        s.write((cmd.strip() + "\n").encode("ascii"))
        s.flush()
        buf = b""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if s.in_waiting:
                buf += s.read(s.in_waiting)
                if b"\n" in buf:
                    break
            time.sleep(0.005)
        return buf.decode("ascii", "ignore").strip()

    def cmd_ok(self, cmd):
        r = self.query(cmd)
        return r.startswith("#OK") or r.startswith("V"), r

    # ---- 解析 #MEAS：返回 {'CH1':{k:v}, 'CH2':{...}} ----
    def measure(self, freeze=True, resume=None):
        if freeze:
            self.query("#SET,STOP")
            time.sleep(0.15)
        raw = self.query("#MEAS", timeout=3.0)
        out = {}
        for line in raw.split("\n"):
            line = line.strip()
            if not line.startswith("#MEAS"):
                continue
            parts = line.split(",")        # #MEAS,CH1,K=v,K=v...
            if len(parts) < 2:
                continue
            ch = parts[1]
            kv = {}
            for token in parts[2:]:
                if "=" in token:
                    k, v = token.split("=", 1)
                    kv[k] = v
            out[ch] = kv
        if freeze and resume:             # 可选恢复运行态
            self.query("#SET," + resume)
        return out, raw

    # ---- #WAV2,3 帧解析 ----
    def capture(self, n_summary=40):
        self.query("#SET,STOP")
        time.sleep(0.15)
        line = self.query("#WAV2,3", timeout=5.0)
        if not line.startswith("#OK"):
            return {"error": line[:200]}
        body = line[len("#OK,"):]
        seg = body.split(",")
        result = {}
        # 段0：320 字节 SysParam（640 hex char）
        if seg and len(seg[0]) >= 640:
            result["sysparam"] = self._parse_sysparam(seg[0][:640])
        for ch, seg_idx in (("CH1", 1), ("CH2", 2)):
            if len(seg) > seg_idx and seg[seg_idx]:
                hexstr = seg[seg_idx]
                nbytes = len(hexstr) // 2
                bytesv = bytes.fromhex(hexstr[:nbytes * 2])
                points = list(bytesv[:3000])          # 前 3000 点为真实采集
                if points:
                    result[ch] = {
                        "n_bytes": nbytes,
                        "n_points": len(points),
                        "adc_min": min(points),
                        "adc_max": max(points),
                        "adc_mean": round(sum(points) / len(points), 2),
                        "first_points": points[:n_summary],
                        "note": "ADC 0..255, 128=通道零点; 电压=(adc-128)*Vdiv*8/256",
                    }
        result["screen_meas"], _ = self.measure(freeze=False)
        return result

    @staticmethod
    def _parse_sysparam(hex640):
        b = bytes.fromhex(hex640)
        u = lambda i: b[i] if i < len(b) else None
        TDI = 34 - u(20)
        return {
            "byte0_run_hint": u(0),   # 0/1，但实测不更新，仅作提示
            "ch2_enabled": u(2),
            "timebase_index": TDI,
            "trig_source": u(64), "trig_mode": u(65), "trig_edge": u(66),
            "trig_level": u(72),
            "ch1_volt_zoom": u(74), "ch1_pos": u(80) | (u(81) << 8),
            "ch1_coupling": u(116), "ch1_probe": u(117),
            "ch2_volt_zoom": u(140), "ch2_pos": u(148) | (u(149) << 8),
            "ch2_coupling": u(180), "ch2_probe": u(181),
        }

    def screenshot(self, path="scope_screen.png"):
        head = self.query("#SHOT", timeout=3.0)   # #SHOT,800,480,2,768000
        try:
            _, w, h, bpp, total = head.split(",")
            w, h, total = int(w), int(h), int(total)
        except Exception:
            return {"error": "无 #SHOT 头，固件可能未含截图功能：" + head[:120]}
        s = self._open()
        # 容错读：收到多少算多少，缺失按黑补齐（与上位机口径一致）
        data = bytearray()
        t_last = time.time()
        while len(data) < total and time.time() - t_last < 1.5:
            if s.in_waiting:
                data += s.read(min(s.in_waiting, total - len(data)))
                t_last = time.time()
            else:
                time.sleep(0.01)
        got = len(data)
        if got < total:
            data += bytes(total - got)               # 黑补齐
        try:
            from PIL import Image
            rgb = bytearray(w * h * 3)
            px = 0
            for i in range(0, got, 2):
                v = data[i] | (data[i + 1] << 8) if i + 1 < len(data) else data[i]
                r = ((v >> 11) & 0x1F); g = ((v >> 5) & 0x3F); b = v & 0x1F
                rgb[px] = (r << 3) | (r >> 2); rgb[px + 1] = (g << 2) | (g >> 4)
                rgb[px + 2] = (b << 3) | (b >> 2)
                px += 3
            img = Image.frombytes("RGB", (w, h), bytes(rgb))
            img.save(path)
            return {"saved": path, "size": [w, h], "got_bytes": got, "expect": total,
                    "note": ("完整无缺" if got == total else
                             "尾部少 %d 字节(约%.1f行)已按黑补齐; 颜色R/B与上下方向实机验证正确"
                             % (total - got, (total - got) / (w * 2)))}
        except ImportError:
            return {"error": "缺 pillow，无法存 PNG。请：pip install pillow",
                    "raw_bytes": got, "expect": total}


# --------------------------------------------------------------------------- #
# MCP 工具定义
# --------------------------------------------------------------------------- #
SCOPE = None  # 延迟初始化

TOOLS = [
    {"name": "scope_info",
     "description": "返回示波器型号(#INFO)与固件版本(#VER)。",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "scope_measure",
     "description": "读取与屏幕逐字一致的测量值(#MEAS, 12 项)。默认先 STOP 冻结再读，避免跳动。",
     "inputSchema": {"type": "object", "properties": {
         "channel": {"type": "string", "enum": ["CH1", "CH2", "ALL"], "description": "通道，默认 ALL"},
         "resume": {"type": "string", "enum": ["RUN", "NONE"], "description": "读完是否恢复 RUN，默认 NONE 保持冻结"}},
         "required": []}},
    {"name": "scope_set",
     "description": "发送 #SET/#KEY 控制命令。set_cmd 为 #SET 后内容，如 'TDIV,17'、'CH1,VDIV,4'、'TRIG,CH1','TRIG,AUTO'。",
     "inputSchema": {"type": "object", "properties": {
         "set_cmd": {"type": "string", "description": "#SET, 之后内容；留空则用 key_cmd"},
         "key_cmd": {"type": "string", "description": "#KEY, 之后内容(模拟按键)"},
         "run": {"type": "string", "enum": ["RUN", "STOP", "SINGLE", "AUTOSET"]}},
         "required": []}},
    {"name": "scope_capture",
     "description": "抓一帧波形(#WAV2,3)：320B参数关键字段 + CH1/CH2 采样统计 + 屏幕测量值。",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "scope_screenshot",
     "description": "抓真实 LCD 整屏(#SHOT)存 PNG，需含 #SHOT 固件。",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "输出文件路径，默认 scope_screen.png"}},
         "required": []}},
]


def _tools_list():
    return {"tools": TOOLS}


def _call_tool(name, args):
    global SCOPE
    if SCOPE is None:
        SCOPE = Scope(PORT, BAUD)
    text = ""
    if name == "scope_info":
        text = json.dumps({"ver": SCOPE.query("#VER"), "info": SCOPE.query("#INFO")}, ensure_ascii=False)
    elif name == "scope_measure":
        ch = args.get("channel", "ALL"); resume = args.get("resume")
        resume = "RUN" if resume == "RUN" else None
        if ch in ("CH1", "CH2"):
            SCOPE.query("#SET,STOP"); time.sleep(0.15)
            raw = SCOPE.query("#MEAS," + ch, timeout=3.0)
            text = raw
        else:
            m, raw = SCOPE.measure(freeze=True, resume=resume)
            text = json.dumps(m, ensure_ascii=False, indent=2)
    elif name == "scope_set":
        if args.get("run"):
            ok, r = SCOPE.cmd_ok("#SET," + args["run"]); text = r
        elif args.get("key_cmd"):
            ok, r = SCOPE.cmd_ok("#KEY," + args["key_cmd"]); text = r
        elif args.get("set_cmd"):
            ok, r = SCOPE.cmd_ok("#SET," + args["set_cmd"]); text = r
        else:
            text = "缺少参数 set_cmd / key_cmd / run"
    elif name == "scope_capture":
        text = json.dumps(SCOPE.capture(), ensure_ascii=False, indent=2)
    elif name == "scope_screenshot":
        text = json.dumps(SCOPE.screenshot(args.get("path", "scope_screen.png")), ensure_ascii=False)
    else:
        return {"isError": True, "content": [{"type": "text", "text": "未知工具: " + name}]}
    return {"content": [{"type": "text", "text": text}]}


# --------------------------------------------------------------------------- #
# JSON-RPC stdio 循环
# --------------------------------------------------------------------------- #
def handle(req):
    rid = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {}) or {}

    if method == "initialize":
        return _resp(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method in ("notifications/initialized", "initialized"):
        return None  # notification，不响应
    if method == "ping":
        return _resp(rid, {})
    if method == "tools/list":
        return _resp(rid, _tools_list())
    if method == "tools/call":
        try:
            res = _call_tool(params.get("name"), params.get("arguments", {}) or {})
        except Exception as e:
            res = {"isError": True, "content": [{"type": "text", "text": "错误: %s" % e}]}
        return _resp(rid, res)
    # 未知方法
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found: " + method}}
    return None


def _resp(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main():
    global PORT, BAUD
    ap = argparse.ArgumentParser(description="FNIRSI 1013D MCP server (stdio)")
    ap.add_argument("--port", default=None, help="串口，如 COM26 / /dev/ttyACM0；默认自动按 VID 0x0483 查找")
    ap.add_argument("--baud", type=int, default=2000000)
    args = ap.parse_args()
    PORT, BAUD = args.port, args.baud

    if sys.stdin.encoding != "utf-8":
        sys.stdin.reconfigure(encoding="utf-8")
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
