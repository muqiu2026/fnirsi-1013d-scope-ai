# 故障排查 · Troubleshooting

按"现象 → 最可能原因 → 怎么办"速查。

## 安装 / 环境

| 现象 | 原因 | 解决 |
|---|---|---|
| `python 不是内部或外部命令` | 安装 Python 没勾 Add to PATH | 重装并勾选；或用 `py` 代替 `python` |
| `pip 不是…命令` | 同上 / pip 未装 | 用 `python -m pip install …` |
| `No module named 'serial'` | 没装 pyserial | `pip install pyserial` |
| `No module named 'PIL'` | 没装 pillow（截图/出图用） | `pip install pillow` |

## 找不到 / 打不开串口

| 现象 | 原因 | 解决 |
|---|---|---|
| 设备管理器没有 COM 口 | 没开 USB-Serial mode，或没重启 | System settings→Other settings→**USB-Serial mode ON**，关机再开机（见 FLASHING.md）|
| 只看到 U 盘 | USB-Serial mode 关着 / 默认 MSC | 同上；打开后才会出现串口 |
| 端口被占用 / `PermissionError` | 另一个程序在用（其它串口助手/桥接） | 关掉所有占用 COM 的程序；**一次只能一个** |
| 连上没反应 | 数据线只能充电 | 换**能传数据**的线；换 USB 口 |
| 仍无串口 | 有时需同时开 Dev mode | 打开 USB-Serial mode 同时开 **Dev mode**（菜单里下一行）|
| Linux 权限 | 无串口读写权限 | `sudo usermod -aG dialout $USER` 后重登；或临时 `sudo` |

## 刷固件 / loader

| 现象 | 原因 | 解决 |
|---|---|---|
| SmartScreen "已保护你的电脑" | loader 是未签名 exe | 点"更多信息 → 仍要运行"（本项目 loader 为社区公开工具，自行判断来源）|
| loader 里看不到设备 | 没在菜单开 USB Connection / 线不行 | 菜单开 USB Connection，等约 10s 让电脑识别；换数据线 |
| 选设备怕选错毁盘 | loader 直写物理扇区 | **只选名为 `F1C100S` 的设备**，绝不选 C/D/E 本地盘；loader 会过滤掉系统盘但仍要肉眼确认 |
| 刷完不开机 | 固件写坏 | 拔掉该 SD 卡开机 = 回原厂固件；或重刷 |

## 读数 / 截图

| 现象 | 原因 | 解决 |
|---|---|---|
| `#MEAS` 返回 `#ERR,UNKNOWN` | 固件没含 MEAS | 刷 Releases 里 `fnirsi_1013d_control.bin`（含 #MEAS+#SHOT）|
| `shot` 报 `#ERR,UNKNOWN` | 固件没含 SHOT | 同上，需含 #SHOT 版 |
| 测量值跳动 | RUN 态边采边变 | 用 `meas-stable`/先 `#SET,STOP`（规则已内置）|
| 读数与屏幕不符 | 在用采样自算 | 改用 `#MEAS`（`meas`/`meas-stable`），那是屏幕原值 |
| 频率/波形形状乱 | RUN 态抓到撕裂帧 | 用 `capture_stable()`/`render_wave.py`（先冻结）|
| 屏幕跳闪 | 抓帧太频繁 | 帧间隔 ≥0.12s；或用 `scope_bridge.py`（内置节流）|
| 截图底部缺一窄条黑边 | #SHOT 稳定少 ~2048B | 已知限制，已按黑补齐，不影响判断（见 README §7）|
| 界面变英文 | 被 `#SET,LANG,EN` 切了 | `python host/scope_ai.py language cn` 或 `#SET,LANG,CN` |

## 自检
一条命令全验：
```bash
python skill/verify_protocol.py     # 期望 37/37 全通过
```
