"""
UEFI console output via EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL (ConOut).

There's no VGA text-mode framebuffer to write to directly here the way
BIOS-booted kernels can -- UEFI text output goes through a protocol
function-pointer call into firmware (`ConOut->OutputString`), which also
means the string must be UCS-2 (UTF-16LE), not ASCII -- that's what the
UEFI spec's CHAR16 strings are.

Structure offsets used here (all fixed by the UEFI spec, x86-64 natural
alignment):

    EFI_SYSTEM_TABLE.ConOut                       offset 0x40 (64)
    EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL.OutputString  offset 0x08
    EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL.ClearScreen   offset 0x30 (48)
    EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL.SetCursorPosition offset 0x38 (56)
    EFI_SIMPLE_TEXT_OUTPUT_PROTOCOL.EnableCursor  offset 0x40 (64)

Built on `protocol.call` (see that module if you need a ConOut method not
wrapped here, e.g. SetAttribute for text color).
"""
from __future__ import annotations

from .. import regs
from ..asm import Program
from . import protocol

SYSTEM_TABLE_CONOUT_OFFSET = 0x40
CONOUT_OUTPUTSTRING_OFFSET = 0x08
CONOUT_CLEARSCREEN_OFFSET = 0x30
CONOUT_SETCURSORPOSITION_OFFSET = 0x38
CONOUT_ENABLECURSOR_OFFSET = 0x40

_SCRATCH = regs.ESI  # holds ConOut transiently; must not be EAX/ECX (protocol.call's
                       # internals) or the register holding EFI_SYSTEM_TABLE* (EBX, by
                       # UefiApp's convention) or RDX/R8/R9 (protocol.call's arg slots)


def _get_conout(prog: Program, system_table_reg: int) -> None:
    """Loads ConOut into the scratch register. Internal helper."""
    prog.load64(_SCRATCH, system_table_reg, SYSTEM_TABLE_CONOUT_OFFSET)


def print_string(prog: Program, text: str, system_table_reg: int) -> Program:
    """Call ConOut->OutputString to print a compile-time-known string.
    `system_table_reg` must hold the EFI_SYSTEM_TABLE pointer (the
    register `UefiApp`'s generated prologue saved it in -- RBX by
    convention; see `app.py`). `\\r\\n` is NOT added automatically --
    UEFI's console, like a real terminal, needs an explicit carriage
    return before the newline to return to the start of the next line."""
    str_label = prog.unique_label("uefi_str")
    skip_label = prog.unique_label("uefi_str_skip")

    _get_conout(prog, system_table_reg)
    protocol.call(prog, _SCRATCH, CONOUT_OUTPUTSTRING_OFFSET, args=[("label", str_label)])

    prog.jmp(skip_label)
    prog.label(str_label)
    prog.raw_bytes(text.encode("utf-16-le") + b"\x00\x00")
    prog.label(skip_label)
    return prog


def println(prog: Program, text: str, system_table_reg: int) -> Program:
    """print_string with a trailing \\r\\n."""
    return print_string(prog, text + "\r\n", system_table_reg)


def clear_screen(prog: Program, system_table_reg: int) -> Program:
    """Call ConOut->ClearScreen. Takes only This, no further arguments."""
    _get_conout(prog, system_table_reg)
    protocol.call(prog, _SCRATCH, CONOUT_CLEARSCREEN_OFFSET, args=[])
    return prog


def set_cursor_position(prog: Program, system_table_reg: int, column: int, row: int) -> Program:
    """Call ConOut->SetCursorPosition(This, Column, Row). `column`/`row`
    are compile-time-known integers (0-based)."""
    _get_conout(prog, system_table_reg)
    protocol.call(prog, _SCRATCH, CONOUT_SETCURSORPOSITION_OFFSET,
                  args=[("imm", column), ("imm", row)])
    return prog


def enable_cursor(prog: Program, system_table_reg: int, visible: bool) -> Program:
    """Call ConOut->EnableCursor(This, Visible). Not supported by every
    firmware/console combination -- check EAX after the call if you need
    to know whether it actually took effect."""
    _get_conout(prog, system_table_reg)
    protocol.call(prog, _SCRATCH, CONOUT_ENABLECURSOR_OFFSET,
                  args=[("imm", 1 if visible else 0)])
    return prog
