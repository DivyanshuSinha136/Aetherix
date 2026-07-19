"""
VGA text-mode driver.

Writes directly to the VGA text-mode framebuffer at physical address
0xB8000 (80x25, 16 colors), which is available on every PC-compatible
machine straight out of reset without needing a GPU driver stack.
"""
from __future__ import annotations

from .. import regs
from ..asm import Program

VGA_BASE = 0xB8000
VGA_COLS = 80
VGA_ROWS = 25

WHITE_ON_BLACK = 0x0F
GREEN_ON_BLACK = 0x0A
RED_ON_BLACK = 0x0C
YELLOW_ON_BLACK = 0x0E


def clear(prog: Program, attr: int = WHITE_ON_BLACK) -> Program:
    """Clear the screen to spaces with the given attribute byte."""
    for row in range(VGA_ROWS):
        for col in range(VGA_COLS):
            addr = VGA_BASE + (row * VGA_COLS + col) * 2
            prog.store8(addr, ord(" "))
            prog.store8(addr + 1, attr)
    return prog


def print_string(prog: Program, text: str, row: int = 0, col: int = 0,
                  attr: int = WHITE_ON_BLACK, errors: str = "replace") -> Program:
    """Emit instructions that write a literal string directly to VGA memory
    at (row, col). This unrolls one store per character -- simple and
    deterministic, appropriate for the short status/boot messages a kernel
    prints during early init.

    `text` is a normal Python (Unicode) string; it's converted to VGA's
    actual single-byte-per-glyph representation (code page 437) via
    `aetherix.encoding.to_vga_bytes` -- so e.g. 'é' correctly becomes byte
    0x82 (the real CP437 glyph), not its raw codepoint value. Characters
    with no CP437 glyph become '?' by default (`errors='replace'`); pass
    `errors='strict'` to raise instead, or check
    `aetherix.encoding.displayable(text)` ahead of time.
    """
    from ..encoding import to_vga_bytes
    base = VGA_BASE + (row * VGA_COLS + col) * 2
    data = to_vga_bytes(text, errors=errors)
    for i, byte in enumerate(data):
        addr = base + i * 2
        prog.store8(addr, byte)
        prog.store8(addr + 1, attr)
    return prog


def print_at_runtime(prog: Program, row: int, col: int, char_reg: int,
                      attr: int = WHITE_ON_BLACK) -> Program:
    """Write a single character held in an 8-bit register value (`char_reg`
    holds the *register number*, e.g. regs.AL) to a fixed screen cell --
    useful for driver code that echoes runtime input (e.g. keyboard scancodes
    translated to ASCII) rather than a compile-time-known string."""
    addr = VGA_BASE + (row * VGA_COLS + col) * 2
    prog.store8_reg(addr, char_reg)
    prog.store8(addr + 1, attr)
    return prog
