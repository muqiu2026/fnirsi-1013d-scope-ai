import re, os, unicodedata

base = r"firmware"
c = open(os.path.join(base, "chinese_font.c"), encoding="utf-8").read()

# 字模码点
cp = re.search(r'zh_codepoints\[[^\]]*\]\s*=\s*\{([^}]*)\}', c).group(1)
codepoints = {int(x, 16) for x in re.findall(r'0x[0-9A-Fa-f]+', cp)}
print("字模码点数:", len(codepoints))

# 翻译表：解析 "EN", "\x..\x.."
trans = re.findall(r'\{\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\}', c)
print("翻译条目:", len(trans))

def unhex_zh(s):
    # \xNN 转 UTF-8 字节再解码
    bs = bytes(int(x, 16) for x in re.findall(r'\\x([0-9A-Fa-f]{2})', s))
    return bs.decode("utf-8", errors="ignore")

missing = {}
new_keys = ["Hours","Minutes","Month","Year","Long","avg","fast","slow","up","down",
            "mode","edge","Always","off","Input","PLS Reset values !",
            "Ref 1","Ref 2","Ref 3","Ref 4","Ref 5","Ref 6","Ref 7","Ref 8"]
en_map = {}
for en, zh in trans:
    zh_text = unhex_zh(zh)
    en_map[en] = zh_text
    for ch in zh_text:
        if ord(ch) > 0x7F and ord(ch) not in codepoints:
            missing.setdefault(ch, []).append(en)

for k in new_keys:
    print(f"  {'OK ' if k in en_map else 'MISS'} {k!r} -> {en_map.get(k,'<无>')}")

if missing:
    print("\n缺字模:")
    for ch, en in missing.items():
        print(f"  {ch} U+{ord(ch):04X}  <- {en}")
else:
    print("\n所有翻译汉字均有字模，无缺字。")
