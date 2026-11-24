#!/usr/bin/env python3
"""
scope_bridge.py - ADS1013D/FNIRSI 1013D 示波器串口桥接器

功能（三合一）：
  1. 监听示波器 USB CDC 串口，解析 #OK,<hex> 波形帧
  2. 写 AI 可读文件 live_scope.txt（AI 读它就能"看懂"示波器当前界面）
  3. 启动本地网页服务，在浏览器实时显示波形（给人看）

用法:
  python scope_bridge.py COM3                    # 自动检测波特率
  python scope_bridge.py COM3 --baud 2000000     # 指定波特率
  python scope_bridge.py --list                   # 列出所有串口
  python scope_bridge.py COM3 --port 8765         # 指定网页端口

然后浏览器打开 http://localhost:8765

依赖: pip install pyserial
"""

import sys, os, re, time, json, math, threading, argparse, http.server, socketserver

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print('需要 pyserial: pip install pyserial')
    sys.exit(1)

# 复用控制库同源的档位表 / 参数解析，避免桥接器与控制库两套口径漂移
from scope_ai import (parse_prm, WEB_PROBE_TEXT, TDIV_SEC, TDIV_TEXT,
                      SAMPLE_RATE_HZ, TIME_PER_DIV_SR_IDX)

# ---- 协议参数 ----
TIMEOUT = 1.0
# 连续抓帧的最小间隔（秒）。固件在主循环同步打包 #WAV2,3 的数千点 HEX，
# 收到一帧立即回发下一帧会让主循环被打包任务占满、插队波形采集/刷屏，
# 表现为屏幕跳闪。这里留出帧间隔，让采集/刷屏优先。
MIN_FRAME_INTERVAL = 0.12

# ---- 全局状态 ----
STATE = {
    'connected': False, 'port': '', 'baud': 0, 'frames': 0,
    'last_update': 0, 'error': '', 'params': {}, 'ch1': [], 'ch2': [],
    'meas': {}, 'text': '等待串口数据...'
}

def _hex_to_samples(s, limit=3000):
    """把连续两位一字节的 HEX 字符串转成 ADC 采样值列表"""
    s = s.strip()
    return [int(s[i:i+2], 16) for i in range(0, len(s)-1, 2)][:limit]

def parse_frame(hexstr):
    """解析一帧波形数据，返回 (参数dict, ch1采样list, ch2采样list)

    v0.27r APP 固件的 #WAV2,3 返回格式为：
        #OK,<320字节参数HEX>,<CH1数据HEX>,<CH2数据HEX>
    每段 HEX 中每两个字符是一个字节，CH1/CH2 前 3000 点是真实采样，
    后面补到 4801 点的 128 是填充值，这里只取前 3000 点。
    """
    parts = [x.strip() for x in hexstr.split(',') if x.strip()]
    if not parts:
        return None

    # 新格式：第一段必须是 640 个 HEX 字符（320 字节参数）
    if len(parts[0]) >= 640:
        raw = bytes.fromhex(parts[0][:640])
        ch1 = _hex_to_samples(parts[1]) if len(parts) > 1 else []
        ch2 = _hex_to_samples(parts[2]) if len(parts) > 2 else []
    else:
        # 兼容旧格式：整帧连续 HEX，前 256 字节为参数
        raw_all = bytes.fromhex(parts[0])
        if len(raw_all) < 256:
            return None
        raw = raw_all[:256]
        rest = raw_all[256:]
        half = len(rest) // 2
        ch1 = list(rest[:half]); ch2 = list(rest[half:]) if half else []

    p = {}
    def u8(i): return raw[i] if i < len(raw) else 0
    # 参数区解析与控制库同源（parse_prm），档位/探头/采样率口径一致
    pa = parse_prm(raw)
    p['run'] = pa['run']
    p['ch2_enabled'] = pa['ch2_enabled']
    p['time_div'] = pa['timebase_sec']
    p['time_div_index'] = pa['timebase_idx']
    p['timebase'] = pa['timebase']
    p['sample_rate'] = pa['sample_rate']
    p['sample_dt'] = pa['sample_dt']
    p['trigger_ch'] = 'CH2' if pa['trigger_channel'] else 'CH1'
    p['trigger_mode'] = 'NORMAL' if pa['trigger_mode'] else 'AUTO'
    p['trigger_edge'] = 'FALLING' if pa['trigger_edge'] else 'RISING'
    p['trigger_level'] = 'AUTO' if u8(67) == 0 else 'MANUAL'
    p['ch1_vdiv'] = pa['ch1_vdiv']
    p['ch1_scale'] = pa['ch1_scale']
    p['ch1_coupling'] = 'AC' if pa['ch1_coupling'] else 'DC'
    p['ch1_probe'] = WEB_PROBE_TEXT.get(pa['ch1_probe'], '1x')
    p['ch2_vdiv'] = pa['ch2_vdiv']
    p['ch2_scale'] = pa['ch2_scale']
    p['ch2_coupling'] = 'AC' if pa['ch2_coupling'] else 'DC'
    p['ch2_probe'] = WEB_PROBE_TEXT.get(pa['ch2_probe'], '1x')
    p['xy_mode'] = u8(249) == 1
    p['siggen'] = u8(250) == 1
    p['menu'] = u8(287)
    return p, ch1, ch2

def analyze(ch, p, is_ch1=True):
    """计算信号特征 —— 为 AI 调硬件提供完整指标"""
    if not ch: return {}
    n = len(ch)
    vmax, vmin = max(ch), min(ch)
    mean = sum(ch)/n
    # 用示波器的垂直档位换算电压：256级对应 8 格 * vdiv
    scale_v = p.get('ch1_scale' if is_ch1 else 'ch2_scale', 1)
    scale = (scale_v * 8) / 256
    vpp = (vmax - vmin) * scale
    vmax_v = (vmax - 128) * scale
    vmin_v = (vmin - 128) * scale
    # RMS 与 AC 耦合分量
    rms = math.sqrt(sum((x-128)**2 for x in ch)/n) * scale
    dc_off = (mean - 128) * scale
    # 阈值交叉 → 频率 / 占空比 / 上升沿
    thr = (vmax + vmin) / 2
    edges_up, edges_dn = [], []
    st = ch[0] >= thr
    for i, v in enumerate(ch):
        ns = v >= thr
        if ns != st:
            (edges_up if ns else edges_dn).append(i)
            st = ns
    freq, duty, rise_pts, min_period = None, None, None, None
    # 每采样点时间由固件采样率表定标（与控制库同源 sample_dt），
    # 不用 tdiv/100 反推：不同时基档 px/点 不同，反推会偏差。
    dt = p.get('sample_dt')
    all_edges = sorted(edges_up + edges_dn)
    if len(edges_up) >= 2:
        periods = [edges_up[i]-edges_up[i-1] for i in range(1, len(edges_up))]
        periods = [x for x in periods if x > 3]
        if periods and dt:
            min_period = min(periods)
            freq = 1.0/(sum(periods)/len(periods)*dt)
    if len(edges_up) >= 1 and len(edges_dn) >= 1 and dt:
        # 取第一个完整周期算占空比
        u = edges_up[0]
        dn_next = [d for d in edges_dn if d > u]
        if dn_next:
            high_time = dn_next[0] - u
            period = (edges_up[1]-u) if len(edges_up) > 1 else high_time*2
            duty = high_time/period if period else None
    # 上升沿点数（10%~90%）
    if edges_up:
        ei = edges_up[0]
        lo, hi = vmin + (vmax-vmin)*0.1, vmin + (vmax-vmin)*0.9
        a = ei
        while a > 0 and ch[a] > lo: a -= 1
        b = ei
        while b < n-1 and ch[b] < hi: b += 1
        rise_pts = b - a
    # 噪声估计：相邻样本的差分中位数（高频噪声指标）
    diffs = [abs(ch[i]-ch[i-1]) for i in range(1, n)]
    diffs.sort()
    noise_raw = diffs[len(diffs)//2] if diffs else 0
    noise_v = noise_raw * scale
    # 波形形状判断
    span = vmax - vmin
    shape = '未知'
    if span > 0:
        uniq = len(set(ch))
        if span <= 6:
            shape = '近似直流/噪声底'
        elif noise_raw > span*0.15:
            shape = '噪声较大'
        elif duty is not None and 0.35 <= duty <= 0.65 and uniq < span*0.6:
            shape = '方波/数字信号'
        elif duty is not None and (duty < 0.25 or duty > 0.75):
            shape = '脉冲信号(窄脉冲)'
        elif uniq > span*0.7:
            shape = '模拟波形(正弦/三角等)'
    return {
        'points': n, 'vpp': vpp, 'vmax': vmax_v, 'vmin': vmin_v,
        'vpp_raw': span, 'mean': (mean-128)*scale, 'rms': rms,
        'dc_off': dc_off, 'freq': freq, 'duty': duty,
        'rise_time': rise_pts*dt if (rise_pts and dt) else None,
        'rise_pts': rise_pts, 'noise': noise_v, 'shape': shape,
        'edges_up': len(edges_up), 'crossings': len(edges_up)+len(edges_dn),
        'raw_min': vmin, 'raw_max': vmax, 'dt': dt, 'scale': scale,
    }

def fmt_freq(hz):
    if hz is None: return 'N/A'
    if hz >= 1e6: return f'{hz/1e6:.3f} MHz'
    if hz >= 1e3: return f'{hz/1e3:.3f} kHz'
    return f'{hz:.1f} Hz'

def fmt_time(s):
    if not s: return 'N/A'
    for u, d in [('s',1),('ms',1e-3),('us',1e-6),('ns',1e-9)]:
        if s >= d: return f'{s/d:.4g} {u}'
    return f'{s:.2g} s'

def downsample(ch, target=200):
    """把波形降采样到 target 点，保留极值（AI 直接读数组就能看波形形状）"""
    n = len(ch)
    if n <= target:
        return ch[:]
    out = []
    step = n / target
    i = 0
    for k in range(target):
        lo = int(k*step); hi = max(lo+1, int((k+1)*step))
        seg = ch[lo:hi]
        out.append(min(seg) if k % 2 else max(seg))  # 交替取极值，保留毛刺
    return out

def ai_hints(a, name, p):
    """基于数据给 AI 的自动提示（异常检测）"""
    hints = []
    if a['vpp'] > 0:
        if a['dc_off'] != 0 and abs(a['dc_off']) > a['vpp']*0.5:
            hints.append(f"{name}: 直流偏置 {a['dc_off']:.2f}V 较大，检查耦合方式/偏置电路")
        if a['noise'] > a['vpp']*0.05:
            hints.append(f"{name}: 噪声 {a['noise']:.3f}V 占 Vpp 的 {a['noise']/a['vpp']*100:.0f}%，偏大，检查接地/电源纹波/走线")
        if a['rise_time'] and a['freq'] and a['rise_time'] < 0.01/a['freq']:
            hints.append(f"{name}: 上升沿 {fmt_time(a['rise_time'])} 很快，注意信号完整性/过冲")
    if a['crossings'] == 0 and a['vpp'] > a['noise']*3:
        hints.append(f"{name}: 有电压变化但无跳变沿，可能是纯直流或极低频信号")
    return hints

def write_ai_file(state, include_samples=True):
    """写 AI 可读文件 —— 完整数值指标 + 采样数组，AI 直接做数值分析无需图像识别"""
    p = state['params']
    if not p: return
    L = [
        '=' * 70,
        '示波器实时数据（结构化数值格式 · 供 AI 直接分析）',
        '=' * 70,
        f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')} | 设备: {state['port']} @{state['baud']} | 帧: {state['frames']}",
        '',
        '[仪器设置]',
        f"  运行={p.get('run','?')}  时基={fmt_time(p.get('time_div'))}/格  屏幕8格",
        f"  触发: {p.get('trigger_mode','?')}/{p.get('trigger_edge','?')}沿/源{p.get('trigger_ch','?')}/电平{p.get('trigger_level','?')}",
        f"  CH1: {p.get('ch1_vdiv','?')} {p.get('ch1_coupling','?')} {p.get('ch1_probe','?')}",
        f"  CH2: {'OFF' if not p.get('ch2_enabled') else str(p.get('ch2_vdiv'))+' '+p.get('ch2_coupling','?')+' '+str(p.get('ch2_probe'))}",
        f"  XY={int(p.get('xy_mode',False))} 信号发生器={int(p.get('siggen',False))}",
        '',
    ]
    # ---- 屏幕测量值（固件 #MEAS 原值 · 权威，与示波器逐字一致）----
    meas = state.get('meas') or {}
    if meas and not meas.get('_error'):
        L += ['[屏幕测量值 · 固件 #MEAS 原值（权威读数，等同示波器面板）]']
        for cn in ('CH1', 'CH2'):
            kv = meas.get(cn)
            if kv:
                L.append(f'  {cn}: ' + '  '.join(f'{k}={kv[k]}' for k in [
                    'Vmax','Vmin','Vavg','Vrms','Vpp','Vp',
                    'Freq','Cycle','Time+','Time-','Duty+','Duty-'] if k in kv))
        L.append('')
    elif meas.get('_error'):
        L += ['[屏幕测量值] 旧固件无 #MEAS，下面的电压为本地估算，仅供参考（刷 MEAS 版固件后此段变为权威）', '']
    all_hints = []
    for name, ch, is1 in [('CH1', state['ch1'], True), ('CH2', state['ch2'], False)]:
        if not ch:
            L += [f'[{name}] OFF / 无数据', '']
            continue
        a = analyze(ch, p, is1)
        duty_s = (f"{a['duty']*100:.1f}%" if a['duty'] is not None else 'N/A')
        ac_rms = math.sqrt(max(a['rms']**2 - a['dc_off']**2, 0))
        L += [
            f'[{name}] 波形数据',
            f"  形状={a['shape']}  采样点={a['points']}  每点时间={fmt_time(a['dt'])}",
            f"  Vpp={a['vpp']:.4f}V  Vmax={a['vmax']:.4f}V  Vmin={a['vmin']:.4f}V",
            f"  均值(直流)={a['mean']:.4f}V  RMS={a['rms']:.4f}V  交流有效值≈{ac_rms:.4f}V",
            f"  频率≈{fmt_freq(a['freq'])}  占空比={duty_s}  上升时间≈{fmt_time(a['rise_time'])}",
            f"  高频噪声(相邻差分中位数)={a['noise']:.4f}V  跳变沿数={a['crossings']}",
            f"  原始ADC范围={a['raw_min']}~{a['raw_max']} (0-255, 中心128)",
        ]
        if include_samples:
            ds = downsample(ch, 200)
            L += [f"  采样数组(200点降采样,保留极值,原始ADC值,中值128=0V):",
                  '  ' + ','.join(str(x) for x in ds)]
        L.append('')
        all_hints += ai_hints(a, name, p)
    if all_hints:
        L += ['[自动异常提示]'] + [f'  ! {h}' for h in all_hints] + ['']
    L += [
        '[给 AI 的分析指引]',
        '  以上是纯数值数据，AI 可直接做推理，无需图像识别。',
        '  请结合用户描述的电路类型/预期信号，判断：',
        '  1) 电压幅值是否合理（逻辑电平/电源电压/传感器输出）',
        '  2) 频率、占空比是否符合设计',
        '  3) 噪声/纹波是否过大，直流偏置是否异常',
        '  4) 波形形状是否正常，有无削顶/振荡/毛刺',
        '  如需看视觉波形，浏览器打开 http://localhost:8765（给人看用）。',
        '=' * 70,
    ]
    with open('live_scope.txt', 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))

def serial_worker(port_name, baud):
    """串口监听线程：交替显式请求 #WAV2,3 与 #MEAS。
    显式"发一条-读到换行-再发下一条"，避免 #MEAS 与波形流交错导致错位。"""
    try:
        ser = serial.Serial(port_name, baudrate=(baud or 2000000), timeout=0.3)
        STATE['connected'] = True
        STATE['port'] = port_name
        STATE['baud'] = ser.baudrate
        STATE['error'] = ''
    except Exception as e:
        STATE['error'] = f'无法打开串口: {e}'
        return

    def read_until(prompt_prefix, timeout=2.5):
        buf = b""
        t0 = time.time()
        while time.time() - t0 < timeout:
            n = ser.in_waiting
            if n:
                buf += ser.read(n)
                if b"\n" in buf:
                    break
            else:
                time.sleep(0.005)
        return buf.decode('ascii', 'ignore')

    def send(cmd):
        ser.reset_input_buffer()
        ser.write((cmd + "\n").encode())
        ser.flush()

    # 握手
    send('#VER'); read_until('#')
    last_req = 0.0
    try:
        while True:
            # 1) 波形帧（含 320B 参数 + CH1/CH2 采样）
            send('#WAV2,3')
            raw = read_until('#OK,', timeout=3.0)
            s = raw.find('#OK,')
            if s >= 0:
                e = raw.find('\n', s)
                hexdata = raw[s + 4:e].strip() if e > 0 else raw[s + 4:].strip()
                if len(hexdata) > 640:
                    try:
                        parsed = parse_frame(hexdata)
                        if parsed:
                            p, ch1, ch2 = parsed
                            STATE['params'] = p
                            STATE['ch1'] = ch1
                            STATE['ch2'] = ch2
                            STATE['frames'] += 1
                            STATE['last_update'] = time.time()
                    except Exception:
                        pass
            # 2) 屏幕测量原值
            send('#MEAS')
            mt = read_until('#', timeout=1.0)
            meas = {}
            for line in mt.splitlines():
                line = line.strip()
                if line.startswith('#MEAS,'):
                    parts = line.split(',')
                    if len(parts) >= 3:
                        kv = {}
                        for tok in parts[2:]:
                            if '=' in tok:
                                k, v = tok.split('=', 1)
                                kv[k.strip()] = v.strip()
                        meas[parts[1]] = kv
                elif line.startswith('#ERR'):
                    meas['_error'] = line
            if meas:
                STATE['meas'] = meas
            write_ai_file(STATE)
            # 节流：给固件主循环留采集/刷屏时间，避免跳闪
            now = time.time()
            if now - last_req < MIN_FRAME_INTERVAL:
                time.sleep(MIN_FRAME_INTERVAL - (now - last_req))
            last_req = time.time()
    except Exception as e:
        STATE['error'] = str(e)
    finally:
        ser.close()

# ---- Web 界面（给人看）----
HTML = r'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>示波器实时画面</title>
<style>
body{margin:0;background:#0b1021;color:#ddd;font-family:Consolas,monospace}
h1{font-size:16px;padding:10px 16px;margin:0;background:#141c38;border-bottom:1px solid #2a3}
#wrap{display:flex;gap:16px;padding:16px}
canvas{background:#000;border:1px solid #334;max-width:100%}
#side{min-width:280px;font-size:13px;line-height:1.7}
#side h2{font-size:14px;color:#7cf}
.k{color:#ffd429}.v{color:#9cf}
#status{padding:6px 16px;background:#0d2418;color:#6c6;font-size:12px}
pre{font-size:11px;color:#8a8;max-height:300px;overflow:auto;white-space:pre-wrap}
</style></head><body>
<h1>ADS1013D 实时波形（桥接器）</h1>
<div id="status">连接中...</div>
<div id="wrap">
<div><canvas id="cv" width="900" height="520"></canvas></div>
<div id="side">
<h2>示波器状态</h2><div id="info">--</div>
<h2>屏幕测量值</h2><div id="measinfo" style="font-size:12px;line-height:1.6">--</div>
<h2>AI 可读文件</h2>
<p>同目录下 <b>live_scope.txt</b> 实时更新，直接给 AI 读即可。</p>
<pre id="aipreview">等待数据...</pre>
</div></div>
<script>
const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
async function tick(){
  try{
    const s=await (await fetch('/api/state')).json();
    document.getElementById('status').textContent=
      s.connected?`已连接 ${s.port} @ ${s.baud} · 帧数 ${s.frames}`:`未连接 ${s.error}`;
    if(s.params&&s.params.run!==undefined){
      const p=s.params;
      const TM={AUTO:'自动',NORMAL:'常规',SINGLE:'单次'};
      const TE={RISING:'上升沿',FALLING:'下降沿'};
      const RN={RUN:'运行',STOP:'停止'};
      const ch2 = p.ch2_enabled
        ? `${p.ch2_vdiv} ${p.ch2_coupling} ${p.ch2_probe}` : '关闭';
      document.getElementById('info').innerHTML=
        `<span class="k">状态</span> <span class="v">${RN[p.run]||p.run}</span><br>`+
        `<span class="k">时基</span> <span class="v">${p.timebase||'?'}</span><br>`+
        `<span class="k">CH1</span> <span class="v">${p.ch1_vdiv} ${p.ch1_coupling} ${p.ch1_probe}</span><br>`+
        `<span class="k">CH2</span> <span class="v">${ch2}</span><br>`+
        `<span class="k">触发</span> <span class="v">${TM[p.trigger_mode]||p.trigger_mode} ${TE[p.trigger_edge]||p.trigger_edge} ${p.trigger_ch}</span><br>`+
        `<span class="k">帧数</span> <span class="v">${s.frames}</span>`;
      // 屏幕测量值（固件 #MEAS 原值，权威）
      let mh='';
      if(s.meas && !s.meas._error){
        for(const c of ['CH1','CH2']){
          const kv=s.meas[c];
          if(kv){
            mh+=`<b style="color:${c==='CH1'?'#ffd429':'#29b2ff'}">${c}</b> `;
            mh+=['Vpp','Vmax','Vmin','Vavg','Vrms','Freq','Duty+'].filter(k=>k in kv)
                 .map(k=>`${k}=${kv[k]}`).join(' · ');
            mh+='<br>';
          }
        }
        if(mh) document.getElementById('measinfo').innerHTML=
          `<span style="color:#6c6">✔ 固件屏幕原值（与示波器一致）</span><br>`+mh;
        else document.getElementById('measinfo').innerHTML='<span style="color:#888">通道均关闭</span>';
      }else{
        document.getElementById('measinfo').innerHTML=
          `<span style="color:#f88">旧固件无 #MEAS${s.meas&&s.meas._error?'（'+s.meas._error+'）':''}，请刷 MEAS 版固件以显示与屏幕逐字一致的读数</span>`;
      }
      draw(s.ch1,s.ch2);
      document.getElementById('aipreview').textContent=s.text||'';
    }
  }catch(e){}
  setTimeout(tick,400);
}
function draw(c1,c2){
  ctx.fillStyle='#000';ctx.fillRect(0,0,cv.width,cv.height);
  ctx.strokeStyle='#223';ctx.lineWidth=1;
  for(let i=1;i<10;i++){let x=cv.width*i/10;ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,cv.height);ctx.stroke()}
  for(let i=1;i<8;i++){let y=cv.height*i/8;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(cv.width,y);ctx.stroke()}
  const drawCh=(ch,color)=>{
    if(!ch||!ch.length)return;
    ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.beginPath();
    for(let i=0;i<ch.length;i++){
      const x=cv.width*i/(ch.length-1);
      const y=cv.height/2-(ch[i]-128)/128*(cv.height/2)*0.9;
      i?ctx.lineTo(x,y):ctx.moveTo(x,y);
    }
    ctx.stroke();
  };
  drawCh(c2,'#29b2ff');drawCh(c1,'#ffd429');
  ctx.fillStyle='#ffd429';ctx.font='14px sans-serif';ctx.fillText('CH1',10,22);
  ctx.fillStyle='#29b2ff';ctx.fillText('CH2',10,42);
}
tick();
</script></body></html>'''

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/api/state'):
            data = STATE.copy()
            try:
                with open('live_scope.txt', encoding='utf-8') as f:
                    data['text'] = f.read()
            except Exception:
                data['text'] = ''
            # 只传必要字段给前端
            out = {k: data[k] for k in ('connected','port','baud','frames','error','params','ch1','ch2','meas','text')}
            body = json.dumps(out).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Access-Control-Allow-Origin','*')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = HTML.encode()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    def log_message(self, *a): pass

def main():
    ap = argparse.ArgumentParser(description='示波器串口桥接器')
    ap.add_argument('port', nargs='?', help='串口名如 COM3 或 /dev/ttyUSB0')
    ap.add_argument('--baud', type=int, default=0, help='波特率(0=自动试)')
    ap.add_argument('--list', action='store_true', help='列出串口')
    ap.add_argument('--webport', type=int, default=8765, help='网页端口')
    args = ap.parse_args()

    if args.list:
        print('可用串口:')
        for p in list_ports.comports():
            print(f'  {p.device}  {p.description}')
        return
    if not args.port:
        print('请指定串口。先运行: python scope_bridge.py --list')
        print(__doc__); return

    print(f'启动桥接器: {args.port} baud={args.baud or "auto"}')
    print(f'网页地址: http://localhost:{args.webport}')
    print(f'AI 文件: {os.path.abspath("live_scope.txt")}')
    t = threading.Thread(target=serial_worker, args=(args.port, args.baud), daemon=True)
    t.start()
    with socketserver.TCPServer(('127.0.0.1', args.webport), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\n退出')

if __name__ == '__main__':
    main()
