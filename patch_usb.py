#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按花括号配平重写 usb_interface.c 的 #WAV2,3 / #WAV2,2 分支。
- WAV2,3: 保留帧头(#OK, + SysParam 320B)，波形裁剪到 3000 真实点，CH2 关闭不打包第二段
- WAV2,2: 废弃假数据锯齿分支，返回 #ERR,UNKNOWN
"""

p = r"firmware\repos\Atlan4\Scope v0.28a\usb_interface.c"
lines = open(p, encoding="utf-8").read().splitlines(keepends=True)

def find_idx(pred, start=0):
    for i in range(start, len(lines)):
        if pred(lines[i]):
            return i
    return -1

def close_brace(open_idx):
    depth = 0; started = False
    for i in range(open_idx, len(lines)):
        for ch in lines[i]:
            if ch == '{':
                depth += 1; started = True
            elif ch == '}':
                depth -= 1
        if started and depth == 0:
            return i
    return -1

# ---- #WAV2,3 ----
i_w3 = find_idx(lambda s: 'strncmp(rx_buffer, "#WAV2,3"' in s)
i_open3 = find_idx(lambda s: '{' in s, i_w3)
i_close3 = close_brace(i_open3)
assert i_close3 > i_open3, "WAV2,3 brace not balanced"

new_w3 = '''{
    usb_tx_busy = 1;   // 开始大数据传输
    usb_CDC_send_text("#OK,");

    // --- SysParam（320 字节参数区，HEX）---
    uint8 *sp = (uint8 *)&SysParam;
    for (int i = 0; i < (int)sizeof(SysParam); i++)
        send_hex_byte(sp[i]);
    usb_CDC_send_text(",");

    // --- CH1 数据：仅打包前 3000 个真实采样点 ---
    // 上位机只取前 3000 点；不再补 128 填充到 4801，减少阻塞主循环的 HEX 打包量。
    static uint8 ch1_data[3000];
    uint8 ch1_data1;
    for (int j = 0; j < 3000; j++)
    {
        ch1_data1 = (uint8)(scopesettings.channel1.tracebuffer[j] & 0xFF);
        ch1_data[j] = (ch1_data1 * scopesettings.channel1.input_calibration[scopesettings.channel1.samplevoltperdiv]) >> VOLTAGE_SHIFTER;
    }
    for (int j = 0; j < 3000; j++)
        send_hex_byte(ch1_data[j]);

    // --- CH2：仅在通道开启时打包第二段；关闭时不发 CH2 数据 ---
    if (scopesettings.channel2.enable)
    {
        usb_CDC_send_text(",");
        static uint8 ch2_data[3000];
        for (int j = 0; j < 3000; j++)
            ch2_data[j] = (uint8)(scopesettings.channel2.tracebuffer[j] & 0xFF);
        for (int j = 0; j < 3000; j++)
            send_hex_byte(ch2_data[j]);
    }

    usb_CDC_send_text("\\n");
    usb_CDC_in_ep_callback();
    usb_tx_busy = 0;   // 全部发送完成
}
'''

lines = lines[:i_w3+1] + [new_w3] + lines[i_close3+1:]

# ---- #WAV2,2（重新定位）----
i_w2 = find_idx(lambda s: 'strncmp(rx_buffer, "#WAV2,2"' in s)
i_open2 = find_idx(lambda s: '{' in s, i_w2)
i_close2 = close_brace(i_open2)
assert i_close2 > i_open2, "WAV2,2 brace not balanced"

new_w2 = '''else if (strncmp(rx_buffer, "#WAV2,2", 7) == 0)
{
    // 旧版遗留的假数据调试分支（此前回传 (j%254)+1 锯齿，非真实波形）。
    // 该格式已废弃，明确返回未知命令，避免上位机误取假波形。
    usb_CDC_send_text("#ERR,UNKNOWN\\n");
    usb_CDC_in_ep_callback();
}
'''
lines = lines[:i_w2] + [new_w2] + lines[i_close2+1:]

open(p, "w", encoding="utf-8").write("".join(lines))
print("patched OK")
