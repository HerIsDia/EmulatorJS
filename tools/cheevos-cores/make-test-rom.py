#!/usr/bin/env python3
"""Build a tiny NES ROM that makes the achievement condition `0xH0010=1` become true.

The program stores 0 at $0010, busy-waits about 3 seconds, stores 1 at $0010 and loops forever.
rcheevos only awards an achievement after seeing its condition false at least once, hence the wait.

Prints the RetroAchievements hash of the ROM (MD5 of the file without its 16-byte iNES header).

Usage: make-test-rom.py OUTPUT.nes
"""

import hashlib
import sys

PROGRAM = bytes([
    0xA9, 0x00,        # C000: LDA #$00
    0x85, 0x10,        # C002: STA $10        ; condition false
    0xA9, 0x10,        # C004: LDA #$10
    0x85, 0x11,        # C006: STA $11        ; 16 outer iterations (~3 s)
    0xA2, 0x00,        # C008: LDX #$00
    0xA0, 0x00,        # C00A: LDY #$00
    0x88,              # C00C: DEY
    0xD0, 0xFD,        # C00D: BNE $C00C
    0xCA,              # C00F: DEX
    0xD0, 0xF8,        # C010: BNE $C00A
    0xC6, 0x11,        # C012: DEC $11
    0xD0, 0xF2,        # C014: BNE $C008
    0xA9, 0x01,        # C016: LDA #$01
    0x85, 0x10,        # C018: STA $10        ; condition true -> achievement
    0x4C, 0x1A, 0xC0,  # C01A: JMP $C01A
])


def build() -> bytes:
    prg = bytearray(16384)  # mapped at $C000 (and mirrored at $8000)
    prg[:len(PROGRAM)] = PROGRAM
    for vector in (0x3FFA, 0x3FFC, 0x3FFE):  # NMI, RESET, IRQ -> $C000
        prg[vector:vector + 2] = bytes([0x00, 0xC0])
    header = b"NES\x1a" + bytes([1, 1, 0, 0]) + bytes(8)  # 1x16 KiB PRG, 1x8 KiB CHR, mapper 0
    return header + bytes(prg) + bytes(8192)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    rom = build()
    with open(sys.argv[1], "wb") as f:
        f.write(rom)
    print(hashlib.md5(rom[16:]).hexdigest())


if __name__ == "__main__":
    main()
