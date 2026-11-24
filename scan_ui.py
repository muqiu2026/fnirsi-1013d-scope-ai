#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描固件源码界面英文字面量，对照现有翻译表，列出未覆盖项。"""
import os, re, sys

SRC = r"C:\Users\yinru\WorkBuddy\2026-11-23-22-23-08\firmware\repos\Atlan4\Scope v0.28a"

# 读取现有翻译表 en 列表（从 chinese_font.c 解析 "EN", "ZH"）
zh_c = os.path.join(SRC, "chinese_font.c")
existing = set()
if os.path.exists(zh_c):
    txt = open(zh_c, encoding="utf-8").read()
    for m in re.finditer(r'\{\s*"((?:[^"\\]|\\.)*)"\s*,\s*"', txt):
        existing.add(m.group(1).encode().decode("unicode_escape"))

str_re = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')
found = {}  # en -> [(file, line)]

def is_ui(s):
    if not s: return False
    if re.fullmatch(r'[\x20-\x7E]*', s) is None: return False  # 非ASCII跳过
    if not re.search(r'[A-Za-z]', s): return False
    # 跳过纯格式串/路径/型号/宏
    if s.startswith(("/", ".", "%", "0x")): return False
    if re.fullmatch(r'[A-Z0-9_/.\-: ]+', s) and not re.search(r'[a-z]', s): return False
    return True

for root, _, files in os.walk(SRC):
    for fn in files:
        if not fn.endswith(".c"): continue
        fp = os.path.join(root, fn)
        try:
            lines = open(fp, encoding="utf-8", errors="ignore").read().splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            for m in str_re.finditer(line):
                s = m.group(1)
                if is_ui(s) and s not in existing:
                    found.setdefault(s, []).append((os.path.basename(fn), i))

print(f"现有翻译条目: {len(existing)}")
print(f"未覆盖英文字面量: {len(found)}\n")
for s, loc in sorted(found.items()):
    f, ln = loc[0]
    print(f'  {s!r}  <- {f}:{ln}')
