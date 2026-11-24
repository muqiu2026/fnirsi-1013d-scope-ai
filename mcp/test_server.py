import json, sys, types, os

# 让脚本无论从何处运行都能 import 同目录 server.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 桩掉 serial，使本测试无需真硬件 / 无需装 pyserial 即可跑协议逻辑
fake_serial = types.ModuleType("serial")
class _SP:
    def comports(self): return []
fake_serial.Serial = lambda *a, **k: None
fake_serial.tools = types.ModuleType("serial.tools")
fake_serial.tools.list_ports = _SP()
sys.modules["serial"] = fake_serial
sys.modules["serial.tools"] = fake_serial.tools
sys.modules["serial.tools.list_ports"] = _SP()

import server as M


class FakeScope(M.Scope):
    def __init__(self):
        self._ser = None; self._port = "X"; self._baud = 2000000
    def _open(self): return self._ser
    def query(self, cmd, timeout=2.0):
        c = cmd.strip()
        if c == "#VER": return "V1.3.0C MOD V9B4"
        if c == "#INFO": return "#OK,1013D"
        if c == "#MEAS,CH1": return "#MEAS,CH1,Vmax=2.18V,Vpp=3.48V,Freq=1.00kHz"
        if c == "#MEAS,CH2": return "#MEAS,CH2,Vpp=1.20V,Freq=2.00kHz"
        if c.startswith("#MEAS"): return "#MEAS,CH1,Vmax=2.18V,Vpp=3.48V,Vavg=-3.64V,Freq=1.00kHz,Duty+=49.9%\n#MEAS,CH2,Vpp=1.20V"
        if c.startswith("#SET") or c.startswith("#KEY"): return "#OK,RUN"
        if c == "#WAV2,3":
            seg0 = "0A"*320; ch = "80"*4801
            return "#OK," + seg0 + "," + ch + "," + ch
        return "#ERR"


M.SCOPE = FakeScope()

ok = []
def check(name, cond):
    print(("PASS" if cond else "FAIL") + " - " + name); ok.append(bool(cond))

r = M.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}})
check("initialize.protocolVersion", r["result"]["protocolVersion"] == "2024-11-05")
check("initialize.serverInfo", r["result"]["serverInfo"]["name"] == "fnirsi-1013d-scope")

r = M.handle({"jsonrpc":"2.0","id":2,"method":"tools/list"})
tools = r["result"]["tools"]
check("tools/list count==5", len(tools) == 5)
check("tools all have inputSchema", all("inputSchema" in t for t in tools))

r = M._call_tool("scope_info", {}); t = r["content"][0]["text"]
check("scope_info has V1.3.0", "V1.3.0" in t and "1013D" in t)

r = M._call_tool("scope_measure", {"channel":"ALL"}); t = json.loads(r["content"][0]["text"])
check("measure CH1.Vpp==3.48V", t["CH1"]["Vpp"] == "3.48V")
check("measure CH1.Freq==1.00kHz", t["CH1"]["Freq"] == "1.00kHz")
check("measure key Duty+ kept", "Duty+" in t["CH1"])
check("measure CH2 present", t.get("CH2", {}).get("Vpp") == "1.20V")

r = M._call_tool("scope_measure", {"channel":"CH1"})
check("measure CH1 single returns text", "Vpp=3.48V" in r["content"][0]["text"])

r = M._call_tool("scope_capture", {}); c = json.loads(r["content"][0]["text"])
sp = c.get("sysparam", {})
check("capture timebase_index parsed", sp.get("timebase_index") == 34 - 0x0A)
check("capture ch1 points 3000", c["CH1"]["n_points"] == 3000)
check("capture ch1 adc_mean ~128", 120 <= c["CH1"]["adc_mean"] <= 136)
check("capture has screen_meas", "CH1" in c.get("screen_meas", {}))

r = M._call_tool("scope_set", {"set_cmd":"TDIV,17"}); check("set set_cmd", "#OK" in r["content"][0]["text"])
r = M._call_tool("scope_set", {"run":"STOP"});        check("set run", "#OK" in r["content"][0]["text"])
r = M._call_tool("scope_set", {});                    check("set empty->msg", "缺少参数" in r["content"][0]["text"])

r = M.handle({"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"scope_info","arguments":{}}})
check("tools/call via handle", "V1.3.0" in r["result"]["content"][0]["text"])

r = M.handle({"jsonrpc":"2.0","id":10,"method":"foo/bar"})
check("unknown method jsonrpc error", r.get("error", {}).get("code") == -32601)

check("initialized no response", M.handle({"jsonrpc":"2.0","method":"notifications/initialized"}) is None)

print("\nRESULT: %d/%d passed" % (sum(ok), len(ok)))
sys.exit(0 if all(ok) else 1)
