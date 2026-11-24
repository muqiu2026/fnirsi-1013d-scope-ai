#!/usr/bin/env bash
# run.sh — Linux/macOS 一键跑通：装依赖 → 自检 → 读屏幕值 → 截图
# 用法： bash run.sh
cd "$(dirname "$0")" || exit 1
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python

echo "=== [1/4] 检查 Python ==="; $PY --version || { echo "未找到 python3"; exit 1; }
echo "=== [2/4] 安装依赖 ==="; $PY -m pip install --quiet pyserial pillow
# Linux 可能需要串口权限： sudo usermod -aG dialout $USER  # 重新登录生效
echo "=== [3/4] 串口全命令自检 ==="; $PY skill/verify_protocol.py \
  || echo "自检未全通过：检查 USB-Serial mode、数据线、端口占用，见 docs/TROUBLESHOOTING.md"
echo "=== [4/4] 读屏幕值 + 截图 ==="; $PY host/scope_ai.py meas-stable; $PY host/scope_ai.py shot 屏幕截图.png
echo "完成。截图 ./屏幕截图.png；AI 接入见 docs/AI-USAGE.md。"
