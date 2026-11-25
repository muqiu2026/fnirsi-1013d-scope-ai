name: ✨ Feature Request
description: 新功能 / 新串口命令 / 新 MCP 工具 / 文档改进建议
labels: ["enhancement"]
body:
  - type: markdown
    attributes:
      value: |
        想法欢迎！请说明用途与期望行为；涉及新固件命令，请考虑对 320B SysParam 与时序的影响。
  - type: textarea
    id: problem
    attributes:
      label: 想解决什么问题
      description: 这个功能帮你在"边看波形边调硬件"流程里省掉哪一步？
    validations: { required: true }
  - type: textarea
    id: proposal
    attributes:
      label: 期望的实现
      description: 命令形态 / MCP 工具名 / 上位机接口 / 屏幕交互等
      placeholder: |
        建议加 `#FOO,...`，返回 ...；对应 MCP 工具 scope_foo(...)
    validations: { required: true }
  - type: dropdown
    id: area
    attributes:
      label: 涉及部分
      options:
        - 固件 firmware/
        - 协议 PROTOCOL
        - 上位机 host/
        - Skill / MCP
        - 文档 docs/
    validations: { required: true }
  - type: textarea
    id: alt
    attributes:
      label: 备选方案 / 额外信息
