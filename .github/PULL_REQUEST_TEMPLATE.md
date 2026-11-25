## 这个 PR 改了什么
<!-- 一句话说明 + 必要背景 -->

## 类型
- [ ] 固件 (firmware/)
- [ ] 串口协议 / SysParam 偏移
- [ ] 上位机 (host/ / skill/)
- [ ] MCP server (mcp/)
- [ ] 文档 (docs/ / README)
- [ ] 其它

## 自检（必填，别提交没验证的功能）
- [ ] `python mcp/test_server.py` 全部通过
- [ ] 涉及串口命令：`python skill/verify_protocol.py` 实机通过，并贴出通过项数
- [ ] 改了 `host/scope_ai.py` 同步改了 `skill/scope_ai.py`
- [ ] 新增/修改命令已同步 `skill/PROTOCOL.md`、`docs/GETTING-STARTED.md`、`docs/AI-PROMPT.md`
- [ ] 未提交 `.bin/.img/` 编译产物（二进制走 Releases）

## 诚实与风险
- [ ] 已知限制/未验证项如实写明（不夸大）
- [ ] 示例图/数据来自真实运行（不造假）
- [ ] 涉及写物理扇区/刷机：标注变砖风险与备份要求

## 关联 Issue
<!-- 例如 Closes #123 -->
