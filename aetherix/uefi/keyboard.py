"""
UEFI keyboard input via EFI_SIMPLE_TEXT_INPUT_PROTOCOL (ConIn).

Structure offsets (fixed by the UEFI spec, x86-64 natural alignment):

    EFI_SYSTEM_TABLE.ConIn                     offset 0x30 (48)
    EFI_SIMPLE_TEXT_INPUT_PROTOCOL.ReadKeyStroke offset 0x08

ReadKeyStroke returns EFI_NOT_READY (nonzero EAX) if no key is waiting,
so reading a key means polling it in a loop -- the same busy-poll
approach `aetherix.drivers.keyboard` uses on the BIOS side, for the same
reason (no interrupt-driven wake without an IDT set up, which UEFI
applications don't get by default either). A real interrupt/event-driven
wait would use WaitForEvent on ConIn->WaitForKey via Boot Services --
a natural next step (see CONTRIBUTING.md).
"""
from __future__ import annotations

from .. import regs
from ..asm import Program

SYSTEM_TABLE_CONIN_OFFSET = 0x30
CONIN_READKEYSTROKE_OFFSET = 0x08

_SCRATCH_A = regs.EAX
_SCRATCH_C = regs.ECX
_SCRATCH_D = regs.EDX


def read_key(prog: Program, system_table_reg: int, scancode_reg: int, char_reg: int) -> Program:
    """Blocks (busy-polls) until a key is pressed. Leaves the UEFI scan
    code in `scancode_reg` (0 for an ordinary character key -- check this
    first for special keys like arrows/function keys, which have a
    UnicodeChar of 0) and the UCS-2 character in `char_reg` (0 if the key
    has no character, e.g. arrow keys).

    `scancode_reg`/`char_reg` must not be RAX/RCX/RDX (used internally).
    """
    if scancode_reg in (_SCRATCH_A, _SCRATCH_C, _SCRATCH_D) or char_reg in (_SCRATCH_A, _SCRATCH_C, _SCRATCH_D):
        raise ValueError("scancode_reg/char_reg must not be RAX/RCX/RDX -- they're used internally here")

    key_buf_label = prog.unique_label("uefi_keybuf")
    retry_label = prog.unique_label("uefi_key_retry")
    skip_label = prog.unique_label("uefi_keybuf_skip")

    prog.label(retry_label)
    prog.load64(_SCRATCH_A, system_table_reg, SYSTEM_TABLE_CONIN_OFFSET)  # RAX = ConIn
    prog.mov_rr64(_SCRATCH_C, _SCRATCH_A)                                   # RCX (This) = ConIn
    prog.lea_rip_label(_SCRATCH_D, key_buf_label)                           # RDX (Key*) = &scratch buffer
    prog.load64(_SCRATCH_A, _SCRATCH_A, CONIN_READKEYSTROKE_OFFSET)         # RAX = ConIn->ReadKeyStroke
    prog.sub64(regs.ESP, 0x20)
    prog.call_r64(_SCRATCH_A)
    prog.add64(regs.ESP, 0x20)
    prog.cmp64(_SCRATCH_A, 0)
    prog.jcc(regs.JNZ, retry_label)  # EFI_NOT_READY (or any error) -- keep polling

    prog.lea_rip_label(_SCRATCH_A, key_buf_label)
    prog.movzx64_mem16(scancode_reg, _SCRATCH_A, 0)  # EFI_INPUT_KEY.ScanCode
    prog.movzx64_mem16(char_reg, _SCRATCH_A, 2)      # EFI_INPUT_KEY.UnicodeChar

    prog.jmp(skip_label)
    prog.label(key_buf_label)
    prog.raw_bytes(b"\x00\x00\x00\x00")  # scratch: ReadKeyStroke writes the 4-byte EFI_INPUT_KEY here
    prog.label(skip_label)
    return prog
