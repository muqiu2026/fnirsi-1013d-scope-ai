#!/usr/bin/env python3
"""
wave2ai.py - 把 FNIRSI/ADS1013D 开源固件的波形文件转换成 AI 可读的分析报告。

用法:
    python wave2ai.py <波形文件.wav> [输出文件.txt]

输出:
    1. 屏幕打印一份中文分析摘要（直接复制给 AI 即可）
    2. 生成 SVG 波形图（同名 .svg），可发给支持图片的 AI 看
    3. 生成 CSV（同名 .csv），便于精确数据分析

支持格式: 开源固件 v0.x/1.x 保存的 6800 字节波形文件
          (800 字节参数 + 3000 字节 CH1 + 3000 字节 CH2)
"""

import sys, os, struct, math

V_DIV = [5.0, 2.5, 1.0, 0.5, 0.2, 0.1, 0.05]   # 显示档位索引对应的 V/div
S_DIV = [5.0, 2.5, 1.0, 0.5, 0.2, 0.1, 0.05]   # 采样档位

def u32(buf, off):
    return struct.unpack_from('<I', buf, off)[0]

def parse(path):
    with open(path, 'rb') as f:
        buf = f.read()
    if len(buf) < 6800:
        raise ValueError(f'文件太小({len(buf)}字节)，不是开源固件波形格式(应为6800字节)')

    r = {}
    r['checksum'] = u32(buf, 0)
    r['version']  = u32(buf, 4)

    chans = []
    for c in (1, 2):
        base = 40 if c == 1 else 160
        ch = {
            'enable':      u32(buf, base + 0),
            'disp_vdiv_i': u32(buf, base + 4),
            'samp_vdiv_i': u32(buf, base + 8),
            'fft':         u32(buf, base + 12),
            'coupling':    u32(buf, base + 16),
            'probe':       u32(buf, base + 20),
            'pos':         u32(buf, base + 24),
            'vmin':        u32(buf, base + 28),
            'vmax':        u32(buf, base + 32),
            'vavg':        u32(buf, base + 36),
            'vcenter':     u32(buf, base + 40),
            'vpp':         u32(buf, base + 44),
            'freq_valid':  u32(buf, base + 48),
            'freq':        u32(buf, base + 52),
            'lowtime':     u32(buf, base + 56),
            'hightime':    u32(buf, base + 60),
            'period':      u32(buf, base + 64),
        }
        off = 800 if c == 1 else 3800
        ch['samples'] = list(buf[off:off+3000])
        chans.append(ch)
    r['ch1'], r['ch2'] = chans

    r['time_div_i']    = u32(buf, 280)
    r['sample_rate']   = u32(buf, 284)
    r['trigger_mode']  = u32(buf, 288)
    r['trigger_edge']  = u32(buf, 292)
    r['trigger_chan']  = u32(buf, 296)
    r['trigger_level'] = u32(buf, 300)
    r['trig_hpos']     = u32(buf, 304)
    r['trig_vpos']     = u32(buf, 308)
    r['have_trigger']  = u32(buf, 312)
    r['trig_index']    = u32(buf, 316)
    return r

def vdiv(idx):
    return V_DIV[idx] if 0 <= idx < len(V_DIV) else 0.5

# 时间档位索引 0..29：50s → 10ns，1-2-5 步进（共30档）
TDIV = [
    50, 20, 10, 5, 2, 1,          # 0-5   秒
    0.5, 0.2, 0.1,                # 6-8   秒
    0.05, 0.02, 0.01,             # 9-11  秒
    0.005, 0.002, 0.001,          # 12-14 秒
    500e-6, 200e-6, 100e-6,       # 15-17
    50e-6, 20e-6, 10e-6,          # 18-20
    5e-6, 2e-6, 1e-6,             # 21-23
    500e-9, 200e-9, 100e-9,       # 24-26
    50e-9, 20e-9, 10e-9,          # 27-29
]

def tdiv(idx):
    if 0 <= idx < len(TDIV):
        return TDIV[idx]
    return None

def analyze_sample(ch, tdiv_s, nsamples=3000):
    """从原始字节计算信号特征"""
    s = ch['samples']
    if not s:
        return None
    probe = ch['probe'] if ch['probe'] > 0 else 1
    scale = vdiv(ch['samp_vdiv_i']) * probe / 25.0   # 25 个像素单位每格? 用示波器报告值优先
    # 采样值 0-255，中心 128；用文件里记录的 vpp/freq 更准
    # 这里计算相对值
    vmin_raw, vmax_raw = min(s), max(s)
    mean_raw = sum(s)/len(s)
    # 估算频率：数过零点（以均值为阈值）
    threshold = mean_raw
    crossings = []
    state = s[0] >= threshold
    for i, val in enumerate(s):
        newstate = val >= threshold
        if newstate != state:
            crossings.append(i)
            state = newstate
    periods = []
    if len(crossings) >= 3:
        for i in range(2, len(crossings)):
            periods.append(crossings[i] - crossings[i-2])
    # 采样率 = 每格时间 * 3000 samples / 30 divisions / ... 
    # 采样点数 3000 对应屏幕 30 格 => 每格 100 点
    if tdiv_s:
        sample_dt = tdiv_s / 100.0
    else:
        sample_dt = 1e-6
    freq_est = None
    if periods:
        avg_period_samples = sum(periods)/len(periods)
        freq_est = 1.0/(avg_period_samples*sample_dt) if avg_period_samples>0 else None
    return {
        'vmin_raw': vmin_raw, 'vmax_raw': vmax_raw, 'mean_raw': mean_raw,
        'crossings': len(crossings), 'freq_est': freq_est,
        'sample_dt': sample_dt,
    }

def fmt_freq(hz):
    if hz is None: return 'N/A'
    if hz >= 1e6: return f'{hz/1e6:.3f} MHz'
    if hz >= 1e3: return f'{hz/1e3:.3f} kHz'
    return f'{hz:.2f} Hz'

def fmt_time(s):
    if s is None: return 'N/A'
    for unit, div in [('s',1),('ms',1e-3),('us',1e-6),('ns',1e-9)]:
        if s >= div:
            return f'{s/div:.4g} {unit}'
    return f'{s:.3g} s'

def make_report(r, path):
    lines = []
    lines.append('=' * 60)
    lines.append('示波器波形分析报告（FNIRSI/ADS1013D 开源固件）')
    lines.append('=' * 60)
    lines.append(f'文件: {os.path.basename(path)}')
    lines.append(f'文件大小: {os.path.getsize(path)} 字节')
    lines.append(f'文件格式版本: {r["version"]}')
    td = tdiv(r['time_div_i'])
    lines.append(f'水平时基: {fmt_time(td)} /格  (索引 {r["time_div_i"]})')
    lines.append(f'采样率字段: {r["sample_rate"]}')
    lines.append(f'触发: 模式={r["trigger_mode"]} 边沿={r["trigger_edge"]} 通道={r["trigger_chan"]} '
                 f'电平={r["trigger_level"]} 有触发={r["have_trigger"]} 触发点索引={r["trig_index"]}')
    lines.append('')
    for ci, ch in enumerate([r['ch1'], r['ch2']], 1):
        lines.append(f'--- CH{ci} ---')
        if not ch['enable']:
            lines.append('  状态: 未启用')
            lines.append('')
            continue
        scale = vdiv(ch['disp_vdiv_i'])
        probe = ch['probe'] if ch['probe'] > 0 else 1
        coupling = 'AC' if ch['coupling'] else 'DC'
        lines.append(f'  状态: 启用 | 耦合: {coupling} | 探头: {probe}X')
        lines.append(f'  显示档位: {scale} V/div | 采样档位: {vdiv(ch["samp_vdiv_i"])} V/div')
        lines.append(f'  示波器测量的参数:')
        lines.append(f'    Vpp    = {ch["vpp"]/100.0:.3f} V' if ch['vpp'] else '    Vpp    = N/A')
        lines.append(f'    Vmin   = {ch["vmin"]/100.0:.3f} V' if ch['vmin'] else '    Vmin   = N/A')
        lines.append(f'    Vmax   = {ch["vmax"]/100.0:.3f} V' if ch['vmax'] else '    Vmax   = N/A')
        lines.append(f'    Vavg   = {ch["vavg"]/100.0:.3f} V' if ch['vavg'] else '    Vavg   = N/A')
        lines.append(f'    中心值 = {ch["vcenter"]/100.0:.3f} V' if ch['vcenter'] else '    中心值 = N/A')
        if ch['freq_valid']:
            lines.append(f'    频率   = {fmt_freq(ch["freq"])}')
            lines.append(f'    周期   = {fmt_time(ch["period"]/1e9 if ch["period"]>1e6 else ch["period"]/1e6)} (原始值 {ch["period"]})')
            lines.append(f'    高电平时间={ch["hightime"]} 低电平时间={ch["lowtime"]} (原始单位)')
        a = analyze_sample(ch, td)
        if a:
            lines.append(f'  原始数据: 范围 {a["vmin_raw"]}~{a["vmax_raw"]} (0-255), 均值 {a["mean_raw"]:.1f}')
            lines.append(f'  零交叉次数: {a["crossings"]}')
            if a['freq_est']:
                lines.append(f'  独立估算频率: {fmt_freq(a["freq_est"])}')
        lines.append('')

    lines.append('-' * 60)
    lines.append('给 AI 的提示: 把这份报告连同波形截图一起发给 AI，')
    lines.append('让 AI 结合电路的预期行为判断信号是否正常（幅值/频率/噪声/波形形状）。')
    lines.append('=' * 60)
    return '\n'.join(lines)

def make_svg(r, path):
    """生成 SVG 波形图"""
    W, H = 1000, 600
    margin_l, margin_t = 60, 40
    plot_w, plot_h = W - margin_l - 20, H - margin_t - 60
    colors = ['#ffd429', '#29b2ff']
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
             f'<rect width="{W}" height="{H}" fill="#0b1021"/>']
    parts.append(f'<text x="20" y="24" fill="#bbb" font-family="sans-serif" font-size="14">{os.path.basename(path)}</text>')
    for gi in range(1, 10):
        x = margin_l + plot_w*gi/10
        parts.append(f'<line x1="{x:.0f}" y1="{margin_t}" x2="{x:.0f}" y2="{margin_t+plot_h}" stroke="#223" stroke-width="1"/>')
    for gi in range(1, 8):
        y = margin_t + plot_h*gi/8
        parts.append(f'<line x1="{margin_l}" y1="{y:.0f}" x2="{margin_l+plot_w}" y2="{y:.0f}" stroke="#223" stroke-width="1"/>')
    for ci, ch in enumerate([r['ch1'], r['ch2']]):
        if not ch['enable']: continue
        s = ch['samples']
        if not s: continue
        pts = []
        for i, val in enumerate(s):
            x = margin_l + plot_w * i / (len(s)-1)
            y = margin_t + plot_h/2 - (val-128)/128 * (plot_h/2) * 0.9
            pts.append(f'{x:.1f},{y:.1f}')
        parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{colors[ci]}" stroke-width="1.5"/>')
        parts.append(f'<text x="{margin_l+10}" y="{margin_t+20+ci*20}" fill="{colors[ci]}" font-family="sans-serif" font-size="13">CH{ci+1}</text>')
    parts.append(f'<text x="{margin_l}" y="{H-15}" fill="#888" font-family="sans-serif" font-size="12">水平时基 {fmt_time(tdiv(r["time_div_i"]))}/格 · 开源固件波形</text>')
    parts.append('</svg>')
    return '\n'.join(parts)

def make_csv(r, path):
    out = []
    out.append('sample,ch1_raw,ch2_raw')
    s1, s2 = r['ch1']['samples'], r['ch2']['samples']
    n = max(len(s1), len(s2))
    for i in range(n):
        out.append(f'{i},{s1[i] if i<len(s1) else ""},{s2[i] if i<len(s2) else ""}')
    return '\n'.join(out)

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    try:
        r = parse(path)
    except Exception as e:
        print(f'解析失败: {e}', file=sys.stderr)
        sys.exit(2)
    report = make_report(r, path)
    print(report)

    base = os.path.splitext(path)[0]
    out_txt = sys.argv[2] if len(sys.argv) > 2 else base + '_AI报告.txt'
    with open(out_txt, 'w', encoding='utf-8') as f:
        f.write(report)
    with open(base + '_波形.svg', 'w', encoding='utf-8') as f:
        f.write(make_svg(r, path))
    with open(base + '.csv', 'w', encoding='utf-8') as f:
        f.write(make_csv(r, path))
    print(f'\n[已生成]\n  {out_txt}\n  {base}_波形.svg\n  {base}.csv')

if __name__ == '__main__':
    main()
