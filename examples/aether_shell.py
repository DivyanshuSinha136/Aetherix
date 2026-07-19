"""
aether_shell -- a genuinely interactive example OS.

Unlike hello_os.py (which prints a few fixed, compile-time-known messages
and waits for one keypress), this boots into a live loop: it reads your
keyboard input in real time -- Shift and all -- and echoes it to the
screen using a runtime VGA cursor (aetherix.drivers.terminal) that
actually moves, wraps at the end of a row, and responds to
Enter/Backspace. There is no fixed script of what appears where; what
shows up on screen depends on what you type.

Controls:
    (letters, digits, punctuation, space)   echoed as typed
    Shift                                    uppercase letters, shifted symbols
    Enter                                    move to the next line
    Backspace                                erase the previous character
    F1                                        beep the PC speaker
    F2                                        reboot (hardware reset)
    F3                                        shutdown (QEMU/Bochs only --
                                               see aetherix.drivers.power)
    Esc                                       goodbye message, then halt

Run:
    python examples/aether_shell.py

Then boot the produced image in an emulator, e.g.:
    qemu-system-i386 -drive file=aether_shell.img,format=raw

Or verify it under CPU emulation without needing QEMU installed:
    pip install unicorn
    python tests/emulate.py build/aether_shell.img
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aetherix import Project, regs

with Project("AetherShell", boot_message="AetherShell starting...") as os_:

    @os_.kernel_entry
    def main(prog, drivers):
        vga = drivers.vga
        kb = drivers.keyboard
        term = drivers.terminal
        power = drivers.power

        kb.init(prog)  # zero the Shift-state flag
        vga.clear(prog)
        vga.print_string(prog, "AetherShell v0.2 -- a live Aetherix demo OS",
                          row=0, col=0, attr=vga.GREEN_ON_BLACK)
        vga.print_string(prog, "Type (Shift works!). Enter=newline  Backspace=erase",
                          row=1, col=0)
        vga.print_string(prog, "F1=beep  F2=reboot  F3=shutdown  Esc=halt",
                          row=2, col=0)
        term.init(prog, start_row=4)

        prog.label("shell_loop")
        kb.wait_key_scancode(prog)  # blocks; raw scancode (make OR break) ends up in AL

        prog.cmp8(regs.AL, kb.SCANCODE_ESCAPE)
        prog.jcc(regs.JZ, "shell_halt")
        prog.cmp8(regs.AL, kb.SCANCODE_ENTER)
        prog.jcc(regs.JZ, "shell_enter")
        prog.cmp8(regs.AL, kb.SCANCODE_BACKSPACE)
        prog.jcc(regs.JZ, "shell_backspace")
        prog.cmp8(regs.AL, kb.SCANCODE_F1)
        prog.jcc(regs.JZ, "shell_beep")
        prog.cmp8(regs.AL, kb.SCANCODE_F2)
        prog.jcc(regs.JZ, "shell_reboot")
        prog.cmp8(regs.AL, kb.SCANCODE_F3)
        prog.jcc(regs.JZ, "shell_shutdown")

        # Shift press/release: update the flag keyboard.read_char also
        # uses, then go straight back to waiting -- Shift never prints.
        prog.cmp8(regs.AL, kb.SCANCODE_LSHIFT)
        prog.jcc(regs.JZ, "shell_shift_on")
        prog.cmp8(regs.AL, kb.SCANCODE_RSHIFT)
        prog.jcc(regs.JZ, "shell_shift_on")
        prog.cmp8(regs.AL, kb.SCANCODE_LSHIFT | kb.BREAK_BIT)
        prog.jcc(regs.JZ, "shell_shift_off")
        prog.cmp8(regs.AL, kb.SCANCODE_RSHIFT | kb.BREAK_BIT)
        prog.jcc(regs.JZ, "shell_shift_off")

        # Any other break code (bit 7 set) -- ignore, don't echo on release.
        prog.test_al(kb.BREAK_BIT)
        prog.jcc(regs.JNZ, "shell_loop")

        # Dispatch any other recognized key, Shift-aware: compare the
        # scancode against each known entry and, on a match, print
        # whichever of its two (compile-time-known) characters matches
        # the current runtime Shift state. Same technique
        # keyboard.read_char uses internally, inlined here because this
        # loop also needs to catch Enter/Backspace/F-keys in the same
        # pass, which read_char deliberately doesn't return for.
        all_codes = sorted(set(kb.SCANCODE_SET1_ASCII) | set(kb.SCANCODE_SET1_SHIFTED))
        for code in all_codes:
            skip_label = prog.unique_label("key_skip")
            use_shifted_label = prog.unique_label("key_use_shifted")
            unshifted_ch = kb.SCANCODE_SET1_ASCII.get(code, kb.SCANCODE_SET1_SHIFTED.get(code))
            shifted_ch = kb.SCANCODE_SET1_SHIFTED.get(code, unshifted_ch)

            prog.cmp8(regs.AL, code)
            prog.jcc(regs.JNZ, skip_label)
            prog.load8(regs.BL, kb.SHIFT_STATE_VAR)
            prog.cmp8(regs.BL, 0)
            prog.jcc(regs.JNZ, use_shifted_label)
            term.putchar_imm(prog, unshifted_ch)
            prog.jmp("shell_loop")
            prog.label(use_shifted_label)
            term.putchar_imm(prog, shifted_ch)
            prog.jmp("shell_loop")
            prog.label(skip_label)

        prog.jmp("shell_loop")  # unrecognized key -- ignore and keep waiting

        prog.label("shell_shift_on")
        prog.store8(kb.SHIFT_STATE_VAR, 1)
        prog.jmp("shell_loop")

        prog.label("shell_shift_off")
        prog.store8(kb.SHIFT_STATE_VAR, 0)
        prog.jmp("shell_loop")

        prog.label("shell_enter")
        term.newline(prog)
        prog.jmp("shell_loop")

        prog.label("shell_backspace")
        term.backspace(prog)
        prog.jmp("shell_loop")

        prog.label("shell_beep")
        drivers.speaker.beep(prog, frequency_hz=880, duration_iterations=1_500_000)
        prog.jmp("shell_loop")

        prog.label("shell_reboot")
        vga.print_string(prog, "Rebooting...", row=24, col=0, attr=vga.YELLOW_ON_BLACK)
        power.reboot(prog)

        prog.label("shell_shutdown")
        vga.print_string(prog, "Shutting down...", row=24, col=0, attr=vga.YELLOW_ON_BLACK)
        power.shutdown(prog)

        prog.label("shell_halt")
        vga.print_string(prog, "Goodbye! System halted.", row=24, col=0, attr=vga.YELLOW_ON_BLACK)
        prog.hlt()

    out_dir = Path(__file__).resolve().parent.parent / "build"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = os_.build(str(out_dir / "aether_shell.img"))
    print(f"Built: {out} ({out.stat().st_size} bytes)")
    print(f"Kernel sectors: {(len(os_.kernel.assemble()) + 511) // 512}")
    print("Boot it with: qemu-system-i386 -drive file=aether_shell.img,format=raw")
