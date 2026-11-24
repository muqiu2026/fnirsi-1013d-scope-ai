#!/usr/bin/env python3
"""
示波器数据捕捉器（双击运行版）
自动找串口 → 读波形 → 写 live_scope.txt 到本程序所在目录
"""
import sys, os, time, math, serial
from serial.tools import list_ports

# 程序所在目录（打包后就是 exe 所在目录）
BASE = os.path.dirname(os.path.abspath(sys.argv[0]))
OUT = os.path.join(BASE, 'live_scope.txt')

VDIV = [0.0005,0.001,0.002,0.005,0.01,0.02,0.05,0.10,0.20,0.50,1.00,2.00,5.00,10.0,20.0,50.0,100,200,500,1000]
TDIV = [None,None,5e-9,10e-9,20e-9,50e-9,100e-9,200e-9,500e-9,1e-6,2e-6,5e-6,10e-6,20e-6,50e-6,100e-6,
        200e-6,500e-6,1e-3,2e-3,5e-3,10e-3,20e-3,50e-3,100e-3,200e-3,500e-3,1,2,5,10]

def fmt_freq(hz):
    if hz is None: return 'N/A'
    if hz>=1e6: return f'{hz/1e6:.3f} MHz'
    if hz>=1e3: return f'{hz/1e3:.3f} kHz'
    return f'{hz:.1f} Hz'

def fmt_time(s):
    if not s: return 'N/A'
    for u,d in [('s',1),('ms',1e-3),('us',1e-6),('ns',1e-9)]:
        if s>=d: return f'{s/d:.4g} {u}'
    return f'{s:.2g} s'

def hex_samples(s, limit=3000):
    s=s.strip()
    return [int(s[i:i+2],16) for i in range(0,len(s)-1,2)][:limit]

def parse(hexstr):
    # v0.27r APP 固件：#OK,<320字节参数HEX>,<CH1 HEX>,<CH2 HEX>
    parts=[x.strip() for x in hexstr.split(',') if x.strip()]
    if not parts: return None
    if len(parts[0])>=640:
        raw=bytes.fromhex(parts[0][:640])
        c1=hex_samples(parts[1]) if len(parts)>1 else []
        c2=hex_samples(parts[2]) if len(parts)>2 else []
    else:
        raw_all=bytes.fromhex(parts[0])
        if len(raw_all)<256: return None
        raw=raw_all[:256]; rest=raw_all[256:]; half=len(rest)//2
        c1=list(rest[:half]); c2=list(rest[half:]) if half else []
    u=lambda i: raw[i] if i<len(raw) else 0
    p={'run':'RUN' if u(0)==1 else 'STOP','ch2':u(2)==1}
    zi=u(20); p['tdiv']=TDIV[zi] if 0<=zi<len(TDIV) else None
    p['trig']=f"{'NORMAL' if u(65) else 'AUTO'}/{'FALL' if u(66) else 'RISE'}/CH{2 if u(64) else 1}"
    p['ch1v']=VDIV[u(74)+u(117)*3] if 0<=u(74)+u(117)*3<len(VDIV) else 0.2
    p['ch2v']=VDIV[u(140)+u(181)*3] if 0<=u(140)+u(181)*3<len(VDIV) else 0.2
    p['ch1c']='AC' if u(116) else 'DC'; p['ch2c']='AC' if u(180) else 'DC'
    p['ch1p']=[1,10,100][u(117)] if u(117)<3 else 1
    p['ch2p']=[1,10,100][u(181)] if u(181)<3 else 1
    return p,c1,c2

def analyze(ch,p,is1):
    if not ch: return None
    n=len(ch); vmax=max(ch); vmin=min(ch); mean=sum(ch)/n
    vd=p['ch1v'] if is1 else p['ch2v']; scale=vd*8/256
    vpp=(vmax-vmin)*scale; dc=(mean-128)*scale
    rms=math.sqrt(sum((x-128)**2 for x in ch)/n)*scale
    ac=math.sqrt(max(rms**2-dc**2,0))
    thr=(vmax+vmin)/2; up=[]; dn=[]; st=ch[0]>=thr
    for i,v in enumerate(ch):
        ns=v>=thr
        if ns!=st: (up if ns else dn).append(i); st=ns
    freq=None; duty=None; rise=None
    # 50px/格 ÷ 0.5px/采样点 = 每格 100 点（不能用 tdiv/n*8）
    dt=p['tdiv']/100.0 if p['tdiv'] else None
    if len(up)>=2 and dt:
        per=[up[i]-up[i-1] for i in range(1,len(up)) if up[i]-up[i-1]>3]
        if per: freq=1/(sum(per)/len(per)*dt)
    if up and dn and dt:
        nx=[d for d in dn if d>up[0]]
        if nx:
            hi=nx[0]-up[0]; per=(up[1]-up[0]) if len(up)>1 else hi*2
            duty=hi/per if per else None
    if up:
        lo=vmin+(vmax-vmin)*0.1; h=vmin+(vmax-vmin)*0.9
        a=up[0]
        while a>0 and ch[a]>lo: a-=1
        b=up[0]
        while b<n-1 and ch[b]<h: b+=1
        rise=(b-a)*dt if dt else None
    dif=sorted(abs(ch[i]-ch[i-1]) for i in range(1,n))
    noise=dif[len(dif)//2]*scale if dif else 0
    span=vmax-vmin
    if span<=6: shape='直流/噪声底'
    elif noise>span*0.15: shape='噪声大'
    elif duty and 0.35<=duty<=0.65: shape='方波/数字信号'
    elif duty and (duty<0.25 or duty>0.75): shape='脉冲信号'
    else: shape='模拟波形'
    return {'shape':shape,'vpp':vpp,'vmax':(vmax-128)*scale,'vmin':(vmin-128)*scale,
            'dc':dc,'rms':rms,'ac':ac,'freq':freq,'duty':duty,'rise':rise,
            'noise':noise,'edges':len(up)+len(dn),'pts':n}

def downsample(ch,target=200):
    n=len(ch)
    if n<=target: return ch
    out=[]; step=n/target
    for k in range(target):
        seg=ch[int(k*step):max(int(k*step)+1,int((k+1)*step))]
        out.append(min(seg) if k%2 else max(seg))
    return out

def write_report(p,a1,a2,frames,port,baud,_ch1,_ch2):
    L=['='*70,'示波器实时数据（供 AI 直接分析，无需截图）','='*70,
       f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')} | {port} @{baud} | 帧 {frames}",'',
       '[仪器设置]',
       f"  运行={p['run']} 时基={fmt_time(p['tdiv'])}/格 (8格) 触发={p['trig']}",
       f"  CH1: {p['ch1v']}V/div {p['ch1c']} {p['ch1p']}X",
       f"  CH2: {'OFF' if not p['ch2'] else str(p['ch2v'])+'V/div '+p['ch2c']+' '+str(p['ch2p'])+'X'}",'']
    hints=[]
    for name,ch,a in [('CH1',_ch1,a1),('CH2',_ch2,a2)]:
        if a is None: L += [f'[{name}] OFF/无数据','']; continue
        duty_s = f"{a['duty']*100:.1f}%" if a['duty'] is not None else 'N/A'
        L += [f'[{name}] 波形数据',
              f"  形状={a['shape']} 点数={a['pts']}",
              f"  Vpp={a['vpp']:.4f}V Vmax={a['vmax']:.4f}V Vmin={a['vmin']:.4f}V",
              f"  直流={a['dc']:.4f}V RMS={a['rms']:.4f}V 交流有效值={a['ac']:.4f}V",
              f"  频率≈{fmt_freq(a['freq'])} 占空比={duty_s} 上升时间≈{fmt_time(a['rise'])}",
              f"  高频噪声={a['noise']:.4f}V 跳变沿={a['edges']}",
              '  采样数组(200点降采样,ADC原值,128=0V):',
              '  '+','.join(str(x) for x in downsample(ch,200)),'']
        if a['noise']>a['vpp']*0.05 and a['vpp']>0:
            hints.append(f"{name}: 噪声占Vpp的{a['noise']/a['vpp']*100:.0f}%，偏大，查接地/电源纹波")
        if abs(a['dc'])>a['vpp']*0.5 and a['vpp']>0:
            hints.append(f"{name}: 直流偏置{a['dc']:.2f}V较大，查耦合/偏置电路")
    if hints: L += ['[自动异常提示]']+[f'  ! {h}' for h in hints]+['']
    L += ['[给AI] 纯数值数据，请结合电路类型/预期信号判断幅值、频率、噪声、波形形状是否正常。','='*70]
    with open(OUT,'w',encoding='utf-8') as f: f.write('\n'.join(L))

def find_port():
    ports=[p for p in list_ports.comports() if p.vid==0x483]
    if ports: return ports[0].device
    ports=[p for p in list_ports.comports() if 'USB' in p.description.upper() or 'CH340' in p.description.upper()]
    return ports[0].device if ports else None

def main():
    print('='*60)
    print('示波器数据捕捉器 - 自动读取波形并写 live_scope.txt')
    print('='*60)
    port=find_port()
    if not port:
        print('\n[错误] 没找到示波器串口！')
        print('请确认: 1) USB线接好 2) 示波器菜单已开 USB-Serial mode')
        print('3) 关掉可能占用串口的程序(浏览器web app等)')
        input('\n按回车退出...'); return
    print(f'\n找到串口: {port}')
    bauds=[2000000,921600,115200,460800]
    ser=None
    for b in bauds:
        try:
            s=serial.Serial(port,baudrate=b,timeout=0.2)
            s.write(b'#VER\n'); time.sleep(0.4)
            data=s.read(100)
            s.close()
            print(f'  尝试 {b} baud ... 收到 {len(data)} 字节')
            if len(data)>0:
                baud=b; break
        except Exception:
            continue
    else:
        baud=bauds[0]; print(f'  未收到响应，默认用 {baud}')
    try:
        ser=serial.Serial(port,baudrate=baud,timeout=0.2)
    except Exception as e:
        print(f'\n[错误] 打不开串口: {e}')
        print('可能串口被占用，关掉浏览器web app后重试')
        input('\n按回车退出...'); return
    print(f'\n开始读取 ({baud} baud)... 把 live_scope.txt 给 AI 读即可')
    print('按 Ctrl+C 或关闭窗口停止\n')
    buf=''; frames=0
    ser.write(b'#WAV2,3\n')
    try:
        while True:
            data=ser.read(4096)
            if data:
                buf+=data.decode('ascii',errors='ignore')
                while '#OK,' in buf:
                    s=buf.index('#OK,')+4; e=buf.find('\n',s)
                    if e<0: break
                    hx=buf[s:e].strip(); buf=buf[e+1:]
                    if len(hx)<640: continue
                    try:
                        r=parse(hx)
                        if not r: continue
                        p,c1,c2=r
                        a1=analyze(c1,p,True); a2=analyze(c2,p,False)
                        frames+=1
                        write_report(p,a1,a2,frames,port,baud,c1,c2)
                        print(f'\r  已写入 {frames} 帧 -> live_scope.txt',end='',flush=True)
                        ser.write(b'#WAV2,3\n')
                    except Exception:
                        pass
                if len(buf)>100000: buf=buf[-10000:]
    except KeyboardInterrupt:
        print('\n\n已停止')
    finally:
        ser.close()

if __name__=='__main__':
    main()
