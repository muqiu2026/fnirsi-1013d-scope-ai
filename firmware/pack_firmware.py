#!/usr/bin/env python3
"""
pack_firmware.py - Python reimplementation of mksunxi + flashfilepacker
for the FNIRSI 1013D open firmware (F1C100S, eGON.EXE header).

Replaces the Linux-only mksunxi and flashfilepacker binaries so the firmware
can be built on Windows.

Usage:
  python pack_firmware.py verify   # verify it can reproduce the original bin
  python pack_firmware.py build    # build patched fnirsi_1013d.bin from new scope.bin
"""
import os, sys, struct, hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(BASE, "repos/Atlan4/Scope v0.28a/dist/Debug/GNU_ARM-Linux")

STAMP_VALUE = 0x5F0A6C39
SCOPE_LOAD_OFFSET = 0x5BC00      # flashfilepacker -l 0x5BC00
BOOT_MAGIC = b"eGON.EXE"


def mksunxi(data: bytearray) -> bytearray:
    """Apply the eGON checksum: set length, stamp, sum all u32 words, write sum."""
    length = len(data)
    assert length % 4 == 0, "length must be 4-byte aligned"
    assert data[4:12] == BOOT_MAGIC, "not an eGON.EXE image"
    struct.pack_into("<I", data, 0x10, length)          # length field
    struct.pack_into("<I", data, 0x0C, STAMP_VALUE)     # stamp
    total = 0
    for i in range(0, length, 4):
        total = (total + struct.unpack_from("<I", data, i)[0]) & 0xFFFFFFFF
    struct.pack_into("<I", data, 0x0C, total)           # write checksum
    return data


def flashfilepacker(scope_bin: bytes, template_full: bytes) -> bytes:
    """Place scope.bin at SCOPE_LOAD_OFFSET inside the full firmware image.

    The original firmware layout is:
      0x00000 .. 0x5BFFF : bootloader / SPL / updater
      0x5BC00 .. 0xAA7FF: scope application (eGON.EXE, 0x4EC00 bytes)
      0xAA800 .. end    : 72-byte tail (reserved / signature block)
    We keep the head and the tail, and substitute the scope app in between.
    """
    ORIG_SCOPE_LEN = 0x4EC00
    tail_start = SCOPE_LOAD_OFFSET + ORIG_SCOPE_LEN
    tail = template_full[tail_start:] if len(template_full) > tail_start else b""
    out = bytearray(template_full[:SCOPE_LOAD_OFFSET])
    if len(out) < SCOPE_LOAD_OFFSET:
        out.extend(b"\x00" * (SCOPE_LOAD_OFFSET - len(out)))
    out.extend(scope_bin)
    out.extend(tail)
    return bytes(out)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def verify():
    orig_scope = open(os.path.join(DIST, "orig_scope.bin"), "rb").read()
    full = open(os.path.join(DIST, "fnirsi_1013d.bin"), "rb").read()

    # 1) verify mksunxi reproduces the checksum already present in orig_scope
    test = bytearray(orig_scope)
    stored = struct.unpack_from("<I", test, 0x0C)[0]
    mksunxi(test)
    recomputed = struct.unpack_from("<I", test, 0x0C)[0]
    print(f"[mksunxi] stored={stored:#010x} recomputed={recomputed:#010x} "
          f"{'OK' if stored == recomputed else 'MISMATCH'}")

    # 2) verify flashfilepacker reproduces the original full image
    rebuilt = flashfilepacker(orig_scope, full)
    same = rebuilt == full
    print(f"[pack] rebuilt={len(rebuilt)} original={len(full)} "
          f"{'OK' if same else 'DIFFERENT'}")
    if not same:
        # show first difference
        for i, (a, b) in enumerate(zip(rebuilt, full)):
            if a != b:
                print(f"  first diff at {i:#x}: {a:#04x} vs {b:#04x}")
                break
    print(f"[sha256] original full = {sha256(full)}")


def build():
    scope = open(os.path.join(DIST, "fnirsi_1013d_scope.bin"), "rb").read()
    template = open(os.path.join(DIST, "fnirsi_1013d.bin"), "rb").read()

    print(f"[input] scope.bin = {len(scope)} bytes")
    print(f"[input] template  = {len(template)} bytes")

    # apply eGON checksum to the freshly compiled scope binary
    scope = mksunxi(bytearray(scope))
    print(f"[mksunxi] scope checksum = {struct.unpack_from('<I', scope, 0x0C)[0]:#010x}")

    # pack into full firmware
    full = flashfilepacker(scope, template)
    out = os.path.join(BASE, "fnirsi_1013d_control.bin")
    open(out, "wb").write(full)
    print(f"[output] {out}")
    print(f"[output] size = {len(full)} bytes")
    print(f"[output] sha256 = {sha256(full)}")

    # sanity check
    check = full[SCOPE_LOAD_OFFSET:SCOPE_LOAD_OFFSET+8]
    print(f"[check] scope header @0x{SCOPE_LOAD_OFFSET:X}: {check}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if mode == "verify":
        verify()
    elif mode == "build":
        build()
    else:
        print("Usage: pack_firmware.py verify|build")
