# 零基础快速开始 · Absolute Beginner Guide

> 假设你**从没用过命令行**也没关系。跟着做完，你会让 AI 读出示波器屏幕上的测量值。
> 全程约 10 分钟。遇到问题看 [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)。

## 你需要准备
- 一台电脑（Windows 最省事；Linux/macOS 也行）
- FNIRSI / ADS 1013D 示波器（已刷入本项目固件，刷法见 [`docs/FLASHING.md`](FLASHING.md)）
- 一根**能传数据**的 USB 线（很多自带的线只能充电，这点最坑）
- 一个已知信号接到 CH1（例如开发板的 1kHz 方波、晶振、PWM；没有信号也能先连通自检）

---

## 第 1 步：装 Python
1. 打开 https://www.python.org/downloads/ → 下载 Python 3.11+ → 安装时**务必勾选 "Add Python to PATH"**。
2. 验证装好了：
   - **Windows**：按 `Win` 键，输入 `powershell` 回车，打开蓝色窗口。输入：
     ```powershell
     python --version
     ```
     看到 `Python 3.x.x` 就成功。
   - **macOS**：用"启动台"打开 `终端`，输入 `python3 --version`。
   - **Linux**：打开终端，输入 `python3 --version`。

> 那个蓝色/黑色的窗口就叫**终端**，后面所有命令都打在它里面、按回车执行。

## 第 2 步：下载本仓库
- 最简单：浏览器打开 https://github.com/muqiu2026/fnirsi-1013d-scope-ai → 右上 **Code → Download ZIP** → 解压到任意文件夹（例如 `D:\scope`）。
- 会 git 的话：`git clone https://github.com/muqiu2026/fnirsi-1013d-scope-ai.git`

## 第 3 步：进入项目文件夹并装依赖
在终端里 `cd` 进去（把路径换成你解压的位置）：
```powershell
cd D:\scope\fnirsi-1013d-scope-ai     # Windows
# cd ~/Downloads/fnirsi-1013d-scope-ai  # mac/Linux
pip install pyserial pillow
```
> 如果提示找不到 pip，改用 `python -m pip install pyserial pillow`（mac/Linux 用 `python3 -m pip ...`）。

## 第 4 步：插上示波器，开串口模式
1. 示波器菜单 → **System settings → Other settings → USB-Serial mode → ON** → 关机再开机（见 [`docs/FLASHING.md`](FLASHING.md)）。
2. 菜单 → **USB Connection** 打开，用数据线连电脑。
3. Windows：按 `Win` 输入"设备管理器" → 展开"端口(COM 和 LPT)" → 看到 `USB Serial Device (COMx)`，记下 **COM 号**（如 COM26）。

## 第 5 步：连通自检（一条命令）
```bash
python skill/verify_protocol.py        # 全命令自检，全通过=成功（实测 37/37）
```
或更直接读屏幕值：
```bash
python host/scope_ai.py meas-stable    # ★ 先冻结再读，结果与屏幕逐字一致
```
看到一串测量值（Vpp/Freq/Duty…）就说明 **AI 已经能"看懂"这台示波器了**。

## 第 6 步：截图当前屏幕
```bash
python host/scope_ai.py shot 屏幕.png   # 抓真实 LCD 整屏（需含 #SHOT 的固件）
```
打开 `屏幕.png` 就是你示波器此刻的画面对比 → 见 [`assets/屏幕实测.png`](../assets/屏幕实测.png)（示例）。

---

## 第 7 步：让 AI 接管（任选其一）
- **会写代码**：直接调 `host/scope_ai.py` 里的 `ScopeAI` 库（见 [`docs/AI-USAGE.md`](AI-USAGE.md)）。
- **用 Claude / Cursor 等支持 MCP 的 AI**：把 `mcp/server.py` 配进客户端，AI 就能自己读数、调档、截图。配置见 [`mcp/README.md`](../mcp/README.md)。
- **最懒**：跑 `python host/scope_bridge.py`，它把实时状态写进 `live_scope.txt`，把文件名丢给任意 AI 即可读。

> 小白只需做到第 5 步看到测量值，就已经"把示波器交给 AI"了。
