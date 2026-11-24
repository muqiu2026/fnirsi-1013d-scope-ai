# MCP Server · 让支持 MCP 的 AI 直接操作示波器

`server.py` 是一个**自包含**的 MCP server（stdio, JSON-RPC 2.0, 协议版本 `2024-11-05`）。
它不依赖本仓库其它模块，只用 `pyserial` 直接按 `#` 协议与示波器通信；截图功能可选依赖 `pillow`。

## 暴露的工具

| 工具 | 说明 |
|---|---|
| `scope_info` | 型号 + 固件版本 |
| `scope_measure` | 屏幕测量值原值（默认先 STOP 冻结，可选读后恢复 RUN）|
| `scope_set` | `#SET`/`#KEY`/RUN 控制（`set_cmd`/`key_cmd`/`run` 三选一）|
| `scope_capture` | 一帧波形：320B 参数关键字段 + CH1/CH2 采样统计 + 屏幕测量值 |
| `scope_screenshot` | 真实 LCD 整屏存 PNG（需 `#SHOT` 固件）|

## 安装与运行

```bash
pip install pyserial pillow
python mcp/server.py                 # 自动按 VID 0x0483 找串口
python mcp/server.py --port COM26    # 手动指定
```

## 接到 AI 客户端

把 `client.example.json` 里的路径换成你的绝对路径，填进客户端配置：

- **Claude Desktop**：`claude_desktop_config.json` 的 `mcpServers` 段。
- **Cursor**：Settings → MCP，或 `.cursor/mcp.json`。
- **其它 stdio MCP 客户端**：command=`python`，args=`["<abs>/mcp/server.py","--port","COM26"]`。

接入后，AI 即可在对话中自主调用上述工具完成"读波形→调设置→再读"的闭环。

## 无硬件自测

```bash
python mcp/test_server.py     # 注入假设备，校验协议解析与 JSON-RPC，应 20/20 PASS
```

## 注意

- 读测量值/抓波形前会自动 `#SET,STOP` 冻结（见 `docs/AI-USAGE.md` 规则）。
- 串口独占：一次只允许一个程序占用 COM 口。
- `scope_screenshot` 目前尾部稳定少约 2048 字节，已按黑补齐；详见 README §7。
