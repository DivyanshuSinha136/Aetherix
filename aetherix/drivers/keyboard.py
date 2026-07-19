"""
PS/2 keyboard driver.

Polls the 8042 PS/2 controller directly (status port 0x64, data port 0x60).
This works on real PC hardware and every mainstream emulator/hypervisor
(QEMU, VirtualBox, VMware, Bochs) because the 8042 (or its USB-legacy
emulation) is present from boot without needing a USB HID stack.

Two ways to use this driver:

  - `wait_key_scancode(prog)` -- low-level: blocks for one scancode (make
    *or* break code) in AL. You handle translation/state yourself. This
    is what the rest of this module is built on.

  - `read_char(prog)` -- high-level: blocks until a *printable* key is
    typed, tracks Shift automatically (uppercase letters, shifted
    punctuation), and silently ignores key-release events and
    non-printable keys (arrows, function keys, etc). Leaves an ASCII byte
    in AL. Call `init(prog)` once at kernel startup first.

Scancodes are Scancode Set 1 (the PS/2 default after BIOS init). A "break"
code (key release) is always the make code with bit 7 set (make | 0x80) --
that rule holds for every single-byte code below. Extended keys (arrows,
Insert/Delete/Home/End/Page Up/Down, right Ctrl/Alt) send a 0xE0 prefix
byte before their code and aren't covered here yet -- see CONTRIBUTING.md.
"""
from __future__ import annotations

from .. import regs
from ..asm import Program

PS2_DATA_PORT = 0x60
PS2_STATUS_PORT = 0x64
PS2_OUTPUT_FULL = 0x01  # bit 0 of status register: 1 = a scancode is waiting

# Runtime state for read_char(). Placed right after terminal.py's variables
# (0x00020000/0x00020004) -- see that module's docstring for why kernel
# "variables" are just reserved fixed addresses here rather than a heap.
SHIFT_STATE_VAR = 0x00020008


def wait_key_scancode(prog: Program, label_prefix: str = "kbwait") -> Program:
    """Blocks (busy-poll) until a key event (press OR release) arrives,
    leaves the raw scancode in AL. Caller is responsible for translating
    scancode -> ASCII and handling press/release if desired -- see
    `read_char` for a ready-made version of that."""
    loop_label = prog.unique_label(f"{label_prefix}_loop")
    prog.label(loop_label)
    prog.in_al(PS2_STATUS_PORT)
    prog.test_al(PS2_OUTPUT_FULL)   # ZF = 1 while no scancode is waiting
    prog.jcc(regs.JZ, loop_label)
    prog.in_al(PS2_DATA_PORT)
    return prog


def init(prog: Program) -> Program:
    """Zero the Shift-state variable. Call once at kernel startup before
    using `read_char`."""
    prog.store8(SHIFT_STATE_VAR, 0)
    return prog


# -- Scancode Set 1: make codes -> unshifted / shifted ASCII -------------
#
# Unshifted: lowercase letters, digits, space, and the standard US QWERTY
# punctuation. Shifted: uppercase letters and the punctuation's shifted
# symbol (matching a standard US keyboard layout).

SCANCODE_SET1_ASCII = {
    0x1E: "a", 0x30: "b", 0x2E: "c", 0x20: "d", 0x12: "e", 0x21: "f",
    0x22: "g", 0x23: "h", 0x17: "i", 0x24: "j", 0x25: "k", 0x26: "l",
    0x32: "m", 0x31: "n", 0x18: "o", 0x19: "p", 0x10: "q", 0x13: "r",
    0x1F: "s", 0x14: "t", 0x16: "u", 0x2F: "v", 0x11: "w", 0x2D: "x",
    0x15: "y", 0x2C: "z", 0x39: " ",
    0x02: "1", 0x03: "2", 0x04: "3", 0x05: "4", 0x06: "5",
    0x07: "6", 0x08: "7", 0x09: "8", 0x0A: "9", 0x0B: "0",
    0x29: "`", 0x0C: "-", 0x0D: "=",
    0x1A: "[", 0x1B: "]", 0x2B: "\\",
    0x27: ";", 0x28: "'",
    0x33: ",", 0x34: ".", 0x35: "/",
}

SCANCODE_SET1_SHIFTED = {
    **{code: ch.upper() for code, ch in SCANCODE_SET1_ASCII.items() if ch.isalpha()},
    0x02: "!", 0x03: "@", 0x04: "#", 0x05: "$", 0x06: "%",
    0x07: "^", 0x08: "&", 0x09: "*", 0x0A: "(", 0x0B: ")",
    0x29: "~", 0x0C: "_", 0x0D: "+",
    0x1A: "{", 0x1B: "}", 0x2B: "|",
    0x27: ":", 0x28: '"',
    0x33: "<", 0x34: ">", 0x35: "?",
    0x39: " ",  # space is unaffected by shift
}

# Named scancodes for keys handled specially (rather than via the ASCII
# tables above), since they trigger behavior rather than printing
# themselves. A "break" code is the make code with bit 7 set (| 0x80).
SCANCODE_ESCAPE = 0x01
SCANCODE_BACKSPACE = 0x0E
SCANCODE_TAB = 0x0F
SCANCODE_ENTER = 0x1C
SCANCODE_LCTRL = 0x1D
SCANCODE_LSHIFT = 0x2A
SCANCODE_RSHIFT = 0x36
SCANCODE_LALT = 0x38
SCANCODE_CAPSLOCK = 0x3A
SCANCODE_F1 = 0x3B
SCANCODE_F2 = 0x3C
SCANCODE_F3 = 0x3D
SCANCODE_F4 = 0x3E
SCANCODE_F5 = 0x3F
SCANCODE_F6 = 0x40
SCANCODE_F7 = 0x41
SCANCODE_F8 = 0x42
SCANCODE_F9 = 0x43
SCANCODE_F10 = 0x44
SCANCODE_F11 = 0x57
SCANCODE_F12 = 0x58

BREAK_BIT = 0x80  # OR'd onto a make code to get its release code


def read_char(prog: Program) -> Program:
    """Blocks until a printable key is typed, automatically handling
    Shift (uppercase letters, shifted punctuation) and silently ignoring
    key-release events and non-printable keys (arrows, function keys,
    Ctrl/Alt/CapsLock, etc -- anything not in the ASCII tables above).
    Leaves the resulting ASCII byte in AL.

    Call `init(prog)` once at kernel startup before using this. Clobbers
    AL and BL; does not touch other general-purpose registers, so it's
    safe to drop into an existing dispatch loop (see `examples/aether_shell.py`).
    """
    loop_label = prog.unique_label("readchar_loop")
    shift_on_label = prog.unique_label("readchar_shift_on")
    shift_off_label = prog.unique_label("readchar_shift_off")
    ignore_break_label = prog.unique_label("readchar_ignore_break")
    got_char_label = prog.unique_label("readchar_got")
    no_match_label = prog.unique_label("readchar_no_match")

    prog.label(loop_label)
    wait_key_scancode(prog, label_prefix=f"readchar_{loop_label}")  # AL = scancode

    prog.cmp8(regs.AL, SCANCODE_LSHIFT)
    prog.jcc(regs.JZ, shift_on_label)
    prog.cmp8(regs.AL, SCANCODE_RSHIFT)
    prog.jcc(regs.JZ, shift_on_label)
    prog.cmp8(regs.AL, SCANCODE_LSHIFT | BREAK_BIT)
    prog.jcc(regs.JZ, shift_off_label)
    prog.cmp8(regs.AL, SCANCODE_RSHIFT | BREAK_BIT)
    prog.jcc(regs.JZ, shift_off_label)

    # Any other break code (bit 7 set) -- ignore, don't echo on key release.
    prog.test_al(BREAK_BIT)
    prog.jcc(regs.JNZ, ignore_break_label)

    # Dispatch against every known make-code, respecting Shift state.
    all_codes = sorted(set(SCANCODE_SET1_ASCII) | set(SCANCODE_SET1_SHIFTED))
    for code in all_codes:
        skip_label = prog.unique_label("readchar_key_skip")
        use_shifted_label = prog.unique_label("readchar_use_shifted")
        unshifted_ch = SCANCODE_SET1_ASCII.get(code, SCANCODE_SET1_SHIFTED.get(code))
        shifted_ch = SCANCODE_SET1_SHIFTED.get(code, unshifted_ch)

        prog.cmp8(regs.AL, code)
        prog.jcc(regs.JNZ, skip_label)
        prog.load8(regs.BL, SHIFT_STATE_VAR)
        prog.cmp8(regs.BL, 0)
        prog.jcc(regs.JNZ, use_shifted_label)
        prog.mov8(regs.AL, ord(unshifted_ch))
        prog.jmp(got_char_label)
        prog.label(use_shifted_label)
        prog.mov8(regs.AL, ord(shifted_ch))
        prog.jmp(got_char_label)
        prog.label(skip_label)

    prog.jmp(no_match_label)

    prog.label(shift_on_label)
    prog.store8(SHIFT_STATE_VAR, 1)
    prog.jmp(loop_label)

    prog.label(shift_off_label)
    prog.store8(SHIFT_STATE_VAR, 0)
    prog.jmp(loop_label)

    prog.label(ignore_break_label)
    prog.jmp(loop_label)

    prog.label(no_match_label)
    prog.jmp(loop_label)

    prog.label(got_char_label)
    return prog
