name: 🐛 Bug Report
description: 报告固件 / 串口协议 / 上位机 / MCP 的问题
labels: ["bug"]
body:
  - type: markdown
    attributes:
      value: |
        感谢反馈！请尽量填全，便于复现。**注意已知限制**：`#SHOT` 稳定少末尾约 2048 字节（≈1.3 行），属已记录现象，非新 bug。
  - type: input
    id: model
    attributes:
      label: 示波器型号
      description: "`#INFO` 的返回值"
      placeholder: "#OK,1013D"
    validations: { required: true }
  - type: input
    id: fwver
    attributes:
      label: 固件版本串
      description: "`#VER` 的返回值"
      placeholder: "V1.3.0C MOD V9B4"
    validations: { required: true }
  - type: textarea
    id: what
    attributes:
      label: 现象描述
      description: 你做了什么、期望什么、实际得到什么
    validations: { required: true }
  - type: textarea
    id: repro
    attributes:
      label: 复现步骤 / 完整命令
      description: 例如 `python scope_ai.py meas-stable` 的完整输出
      render: shell
    validations: { required: true }
  - type: textarea
    id: env
    attributes:
      label: 运行环境
      description: OS、Python 版本、pyserial/pillow 版本、COM 口、是否已开 USB-Serial mode
      placeholder: |
        Windows 11 / Python 3.11 / pyserial 3.5 / COM26 / 已开 USB-Serial mode
  - type: textarea
    id: log
    attributes:
      label: 完整报错 / 自检结果
      description: "`python skill/verify_protocol.py` 输出（通过了多少项）"
      render: shell
