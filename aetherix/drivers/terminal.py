"""
Terminal driver: a *runtime* VGA cursor, as opposed to `vga.print_string`'s
compile-time-fixed text placement. This is what lets a kernel echo
keystrokes as they arrive, rather than only printing messages whose exact
screen position is known when the image is built.

State (two 32-bit values) lives at fixed physical addresses, since the
encoder's 32-bit addressing modes are either "fixed absolute address" or
"[register]" -- there's no general heap/stack-relative addressing here,
so kernel-level "variables" are just reserved memory cells at addresses
the kernel promises not to overwrite for anything else.

    CURSOR_ADDR_VAR  (4 bytes) -- absolute VGA memory address of the next
                                   character cell to write
    COL_COUNT_VAR    (4 bytes) -- current column (0-79), used only to
                                   decide where "Enter" should land

Both live at 0x00020000 -- comfortably past anything this small kernel
occupies, and comfortably below where you'd start needing to worry about
extended memory. If your own kernel grows large enough to reach 128KB,
move these (see `CURSOR_ADDR_VAR`/`COL_COUNT_VAR` below).
"""
from __future__ import annotations

from .. import regs
from ..asm import Program
from .vga import VGA_BASE, VGA_COLS, VGA_ROWS, WHITE_ON_BLACK

CURSOR_ADDR_VAR = 0x00020000
COL_COUNT_VAR = 0x00020004

_SCREEN_END = VGA_BASE + VGA_COLS * VGA_ROWS * 2
_ROW_BYTES = VGA_COLS * 2


def init(prog: Program, start_row: int = 0) -> Program:
    """Initialize the runtime cursor to the start of `start_row`."""
    prog.mov32(regs.EBX, VGA_BASE + start_row * _ROW_BYTES)
    prog.store32(CURSOR_ADDR_VAR, regs.EBX)
    prog.mov32(regs.ECX, 0)
    prog.store32(COL_COUNT_VAR, regs.ECX)
    return prog


def _advance_cursor(prog: Program) -> Program:
    """Shared by putchar_imm/putchar_reg: advance EBX (must already hold
    the just-written cursor address) by 2, wrap to the top of the screen
    if past the end, and update the column counter. Clobbers EBX/ECX."""
    wrap_label = prog.unique_label("term_wrap_screen")
    skip_wrap_label = prog.unique_label("term_skip_wrap")
    col_reset_label = prog.unique_label("term_col_reset")
    col_done_label = prog.unique_label("term_col_done")

    prog.add32(regs.EBX, 2)
    prog.cmp32(regs.EBX, _SCREEN_END)
    prog.jcc(regs.JL, skip_wrap_label)
    prog.label(wrap_label)
    prog.mov32(regs.EBX, VGA_BASE)
    prog.label(skip_wrap_label)
    prog.store32(CURSOR_ADDR_VAR, regs.EBX)

    prog.load32(regs.ECX, COL_COUNT_VAR)
    prog.add32(regs.ECX, 1)
    prog.cmp32(regs.ECX, VGA_COLS)
    prog.jcc(regs.JL, col_done_label)
    prog.label(col_reset_label)
    prog.mov32(regs.ECX, 0)
    prog.label(col_done_label)
    prog.store32(COL_COUNT_VAR, regs.ECX)
    return prog


def putchar_imm(prog: Program, ch: str, attr: int = WHITE_ON_BLACK) -> Program:
    """Print a single, compile-time-known character at the runtime cursor
    position, advance the cursor, and wrap to the next row (or back to the
    top of the screen, if already on the last row) as needed. Clobbers
    EBX/ECX."""
    prog.load32(regs.EBX, CURSOR_ADDR_VAR)
    prog.store8_ind_imm(regs.EBX, ord(ch) & 0xFF)
    prog.store8_ind_disp_imm(regs.EBX, 1, attr & 0xFF)
    _advance_cursor(prog)
    return prog


def putchar_reg(prog: Program, char_reg: int, attr: int = WHITE_ON_BLACK) -> Program:
    """Like putchar_imm, but prints a character held in a register at
    runtime (e.g. the result of `keyboard.read_char`) rather than a
    compile-time-known Python string. `char_reg` must not be EBX or ECX
    (used internally to track the cursor) -- AL/DL/etc. are fine.
    Clobbers EBX/ECX (in addition to reading, but not modifying, `char_reg`)."""
    if char_reg in (regs.EBX, regs.ECX):
        raise ValueError("char_reg must not be EBX/ECX -- they're used internally here")
    prog.load32(regs.EBX, CURSOR_ADDR_VAR)
    prog.store8_ind(regs.EBX, char_reg)
    prog.store8_ind_disp_imm(regs.EBX, 1, attr & 0xFF)
    _advance_cursor(prog)
    return prog


def newline(prog: Program) -> Program:
    """Move the runtime cursor to the start of the next row, wherever the
    current column is (matching typical terminal Enter-key behavior).
    Clobbers EAX/EBX/ECX."""
    wrap_label = prog.unique_label("term_nl_wrap")
    skip_wrap_label = prog.unique_label("term_nl_skip_wrap")

    prog.load32(regs.EAX, COL_COUNT_VAR)     # EAX = current column
    prog.add_rr32(regs.EAX, regs.EAX)         # EAX = column * 2 (bytes into row)
    prog.mov32(regs.ECX, _ROW_BYTES)
    prog.sub_rr32(regs.ECX, regs.EAX)         # ECX = bytes remaining to next row start

    prog.load32(regs.EBX, CURSOR_ADDR_VAR)
    prog.add_rr32(regs.EBX, regs.ECX)

    prog.cmp32(regs.EBX, _SCREEN_END)
    prog.jcc(regs.JL, skip_wrap_label)
    prog.label(wrap_label)
    prog.mov32(regs.EBX, VGA_BASE)
    prog.label(skip_wrap_label)
    prog.store32(CURSOR_ADDR_VAR, regs.EBX)

    prog.mov32(regs.ECX, 0)
    prog.store32(COL_COUNT_VAR, regs.ECX)
    return prog


def backspace(prog: Program, attr: int = WHITE_ON_BLACK) -> Program:
    """Move the runtime cursor back one cell (if not already at the start
    of a row) and erase it to a blank space. Clobbers EBX/ECX."""
    skip_label = prog.unique_label("term_bs_skip")

    prog.load32(regs.ECX, COL_COUNT_VAR)
    prog.cmp32(regs.ECX, 0)
    prog.jcc(regs.JZ, skip_label)

    prog.sub32(regs.ECX, 1)
    prog.store32(COL_COUNT_VAR, regs.ECX)

    prog.load32(regs.EBX, CURSOR_ADDR_VAR)
    prog.sub32(regs.EBX, 2)
    prog.store32(CURSOR_ADDR_VAR, regs.EBX)
    prog.store8_ind_imm(regs.EBX, ord(" "))
    prog.store8_ind_disp_imm(regs.EBX, 1, attr & 0xFF)

    prog.label(skip_label)
    return prog
