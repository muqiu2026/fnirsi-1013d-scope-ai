import re, sys

def count(p):
    s = open(p, encoding='utf-8').read()
    s = re.sub(r'/\*.*?\*/', '', s, flags=re.S)
    s = re.sub(r'//[^\n]*', '', s)
    s = re.sub(r'"(?:[^"\\]|\\.)*"', '""', s)
    s = re.sub(r"'(?:[^'\\]|\\.)*'", "''", s)
    return s.count('{'), s.count('}'), s.count('('), s.count(')')

for f in ['usb_interface.c', 'usb_interface.c.bak']:
    p = r'firmware\repos\Atlan4\Scope v0.28a\\' + f
    print(f, count(p))
