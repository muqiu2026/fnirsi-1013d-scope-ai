# run.ps1 — Windows 一键跑通：装依赖 → 自检 → 读屏幕值 → 截图
# 用法：右键“使用 PowerShell 运行”，或在本目录终端执行  powershell -ExecutionPolicy Bypass -File .\run.ps1
$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "=== [1/4] 检查 Python ===" -ForegroundColor Cyan
try { python --version } catch { Write-Host "未找到 python，请先装 Python 并勾选 Add to PATH（见 docs/BEGINNER.md）" -ForegroundColor Red; exit 1 }

Write-Host "`n=== [2/4] 安装依赖 pyserial/pillow ===" -ForegroundColor Cyan
python -m pip install --quiet pyserial pillow

Write-Host "`n=== [3/4] 串口全命令自检 (需已开 USB-Serial mode 并接好设备) ===" -ForegroundColor Cyan
python skill/verify_protocol.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n自检未全通过：多半是没开 USB-Serial mode / 数据线只能充电 / 端口被占用。" -ForegroundColor Yellow
    Write-Host "请对照 docs/TROUBLESHOOTING.md。仍想继续可手动跑下面命令。" -ForegroundColor Yellow
}

Write-Host "`n=== [4/4] 读屏幕测量值 + 整屏截图 ===" -ForegroundColor Cyan
python host/scope_ai.py meas-stable
python host/scope_ai.py shot 屏幕截图.png
Write-Host "`n完成。截图见 ./屏幕截图.png；接入 AI 见 docs/AI-USAGE.md。" -ForegroundColor Green
