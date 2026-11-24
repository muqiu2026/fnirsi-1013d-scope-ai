# 刷机方法与工具 · Flashing Guide

> ⚠️ 刷机有**变砖风险**。务必先做完整备份（SD 卡镜像 + 原固件），逐级操作，选错设备会毁数据。刷第三方固件可能影响保修，风险自负。

示波器型号：**FNIRSI / ADS 1013D**。主控：**Allwinner F1C100S**（ARM926EJ-S，408 MHz）。

## 0. 需要的工具 · Tools

| 工具 | 用途 |
|---|---|
| `FNIRSI_Firmware_Loader.exe`（loader_v1.1.3）| **Windows 下首选**，通过 USB "U 盘模式"把 .bin 写入设备隐藏扇区，比手工 dd 安全 |
| `sunxi-fel`（Linux）| 通过 **FEL** 接口写 RAM/写 SD，适合调试或 loader 不可用时 |
| 一张 FAT32 SD 卡 | 示波器系统盘（固件写在 8KB 保留扇区，不动分区表）|
| USB 数据线 | 必须是能传数据的线，不是仅充电线 |

构建产物：用 `firmware/build_firmware.ps1` 编译 + `firmware/pack_firmware.py` 打包得到 `fnirsi_1013d_control.bin`（含 `#MEAS` 与 `#SHOT`）。把该 .bin 作为下面"固件文件"。

---

## 方案 A：Windows + FNIRSI Firmware Loader（推荐，首次安装）

来源：本项目 `F盘刷机步骤.txt`（实机验证）。Loader 内置保护——不列出非 USB/被识别为硬盘/非 512B 扇区/含系统卷的设备，只对"隐藏扇区 ≥1023 且 FAT32"的卡写入。

1. SD 卡插回示波器，开机。
2. 菜单 → **USB Connection**，让电脑把它识别为 U 盘（此时电脑里那个盘就是示波器，不是读卡器）。
3. 以**管理员**运行 `FNIRSI_Firmware_Loader.exe`（会弹 UAC，点"是"）。
4. 在 "USB storage device" 下拉里选名为 **F1C100S** 的设备（**务必确认不是任何本地磁盘 C/D/E**）。
5. "Binary File" 选择你的 `fnirsi_1013d_control.bin`。
6. 点 **Write to device**，等待完成。
7. 示波器关 USB / 关机后重新开机，进入新 bootloader；首次启动在菜单里做一次基础校准。

> 固件写入位置是 MBR 与 1MB 分区之间的 8KB 保留扇区，与 FAT32 文件系统不冲突，SD 卡上的文件不丢。

---

## 方案 B：Linux + sunxi-fel（FEL）

来源：上游 `v028a_How_to_load_scope.txt`。

**检查设备是否进入 FEL**：
```bash
sudo ./sunxi-fel version
```

**临时载入到 RAM 运行（掉电即失，用于验证）**：
```bash
sudo ./sunxi-fel -p write 0x7FFFFFE0 fnirsi_1013d_scope.bin exe 0x80000000
```

**写入 SD 卡（持久安装）**：把固件写到 SD 设备偏移 8KB 处（`seek=8` 单位 KB）：
```bash
sudo umount /dev/sdc1
sudo dd if=fnirsi_1013d.bin of=/dev/sdc bs=1024 seek=8
```
> `/dev/sdc` 必须是你**确认无误**的 SD 卡设备号，写错毁盘。

---

## 开启串口模式 · Enable USB-Serial mode（刷机后必做）

来源：`开启串口模式步骤.txt`（源码 `menu.c` 的 `scope_system_settings_other_settings_CH340_mode_item()`，变量 `USB_CH340`：0=U 盘 MSC，1=串口 CDC）。

1. 主界面 **MENU** → **System settings（系统设置）**。
2. 进入 **Other settings（其他设置）**（找不到时按 **CTRL** 翻按钮页）。
3. 列表第 6 项 **USB-Serial mode** → 拨到 **ON**（绿色）。
4. **关机再开机**（必须重启设置才生效）。
5. 菜单 → **USB Connection** 打开；电脑"设备管理器 → 端口(COM 和 LPT)"出现串口设备即可。
6. 若仍无串口：换数据线/换 USB 口；论坛经验有时需同时打开 **Dev mode**（USB-Serial mode 下一行）。

---

## 参考 · References

- EEVBlog 社区帖：`https://www.eevblog.com/forum/testgear/fnirsi-1013d-100mhz-tablet-oscilloscope/`
- Atlan4 GitHub：`https://github.com/Atlan4/Fnirsi1013D`
