import os, re

SRC = r"firmware\repos\Atlan4\Scope v0.28a"
str_re = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')
# display_text( x, y, "text" )
call_re = re.compile(r'display_text\s*\([^,;]*,[^,;]*,\s*"((?:[^"\\]|\\.)*)"')

existing = set()
cf = os.path.join(SRC, "chinese_font.c")
txt = open(cf, encoding="utf-8").read()
for m in re.finditer(r'\{\s*"((?:[^"\\]|\\.)*)"\s*,\s*"', txt):
    existing.add(m.group(1).encode().decode("unicode_escape"))

found = {}
for root, _, files in os.walk(SRC):
    for fn in files:
        if not fn.endswith(".c"): continue
        fp = os.path.join(root, fn)
        for i, line in enumerate(open(fp, encoding="utf-8", errors="ignore").read().splitlines(), 1):
            for m in call_re.finditer(line):
                s = m.group(1)
                if not re.search(r'[A-Za-z]', s): continue
                if s in existing: continue
                # 跳过纯格式/单位/型号/碎片
                if re.fullmatch(r'[0-9A-Za-z ./_\-:%×x]*', s) and not re.search(r'[a-z]{3}', s): continue
                found.setdefault(s, []).append(f"{fn}:{i}")

print(f"已覆盖 {len(existing)} 条；display_text 未覆盖字面量 {len(found)} 个：\n")
for s, loc in sorted(found.items()):
    print(f"  {s!r}  <- {loc[0]}")
