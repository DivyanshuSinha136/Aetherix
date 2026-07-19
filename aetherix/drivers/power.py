"""
Power control: restart, reboot, shutdown, sleep.

Honest scope up front: full ACPI power management (real S5 shutdown, S3
sleep) requires parsing the ACPI tables (RSDP -> RSDT/XSDT -> FADT ->
DSDT) and interpreting AML bytecode to find the actual PM1 control port
and the sleep-type values for each state -- a substantial project on its
own, not implemented here (see CONTRIBUTING.md if you want to build it).
What's here instead:

    IMPLEMENTED  restart   - soft restart: jump back to the top of the
                             kernel's own entry point. No hardware reset,
                             works everywhere, always available.
    IMPLEMENTED  reboot    - hard reboot via the keyboard controller's
                             CPU-reset pulse (port 0x64, command 0xFE).
                             A real, portable technique that works on
                             actual hardware and every mainstream emulator
                             -- BIOS POST runs again afterward.
    LIMITED      shutdown  - ACPI soft-off via the QEMU/Bochs-specific
                             "magic port" trick (ports 0x604/0xB004).
                             Works in QEMU and Bochs. Does **not** work on
                             real hardware or other emulators/hypervisors
                             -- falls through to a halt loop there.
    APPROXIMATE  sleep     - waits for a keypress, then returns. This is
                             NOT a reduced-power ACPI sleep state (no
                             interrupt-driven wake, since there's no IDT/
                             PIC setup in this codebase yet) -- it's a
                             busy-poll pause, named for the closest
                             behavior achievable without that
                             infrastructure. See CONTRIBUTING.md.
"""
from __future__ import annotations

from .. import regs
from ..asm import Program
from . import keyboard

KBC_STATUS_PORT = 0x64
KBC_COMMAND_PORT = 0x64
KBC_INPUT_BUFFER_FULL = 0x02
KBC_PULSE_RESET_LINE = 0xFE

# QEMU/Bochs-specific ACPI shutdown ports: writing 0x2000 (a 16-bit word)
# triggers a soft power-off. Not part of any real hardware standard --
# these are emulator implementation details, widely relied on in hobby OS
# tutorials specifically because real ACPI shutdown is so much more work.
QEMU_SHUTDOWN_PORTS = [0x604, 0xB004]
QEMU_SHUTDOWN_VALUE = 0x2000

KERNEL_ENTRY_LABEL = "__kernel_entry_start"  # set by Kernel.assemble()


def restart(prog: Program) -> Program:
    """Soft restart: jump back to the very first instruction of the
    kernel's own entry function, re-running its startup logic without
    touching any hardware. Always works; this is just a jump. Requires
    the kernel to have been built via `Kernel`/`Project` (which places
    the label this jumps to automatically) -- using a bare `Program`
    directly, place your own label at the top and `prog.jmp(...)` it."""
    prog.jmp(KERNEL_ENTRY_LABEL)
    return prog


def reboot(prog: Program) -> Program:
    """Hard reboot via the keyboard controller's CPU-reset pulse: waits
    for the controller's input buffer to be free, then sends command
    0xFE to port 0x64, which pulses the CPU's reset line. This is the
    classic, portable warm-reboot method used by real-mode/protected-mode
    OSes without ACPI -- works on real hardware and every mainstream
    emulator. BIOS POST runs again afterward, exactly like a physical
    reset button. Never returns (falls through to a halt loop as a
    defensive fallback, in the unlikely case the pulse doesn't take
    effect immediately)."""
    wait_label = prog.unique_label("reboot_wait_kbc")
    halt_label = prog.unique_label("reboot_fallback_halt")

    prog.label(wait_label)
    prog.in_al(KBC_STATUS_PORT)
    prog.test_al(KBC_INPUT_BUFFER_FULL)
    prog.jcc(regs.JNZ, wait_label)

    prog.mov8(regs.AL, KBC_PULSE_RESET_LINE)
    prog.out_al(KBC_COMMAND_PORT)

    prog.label(halt_label)
    prog.hlt()
    prog.jmp(halt_label)
    return prog


def shutdown(prog: Program) -> Program:
    """Attempt ACPI soft-off via the QEMU/Bochs magic shutdown ports.
    Works in QEMU and Bochs; does nothing on real hardware or other
    emulators/hypervisors (real ACPI shutdown needs full ACPI table
    parsing -- see the module docstring). Falls through to a halt loop
    if the shutdown doesn't take effect, so this is always safe to call
    even on a target where it won't work."""
    for port in QEMU_SHUTDOWN_PORTS:
        prog.mov32(regs.EDX, port)
        prog.mov32(regs.EAX, QEMU_SHUTDOWN_VALUE)
        prog.out_ax16()

    halt_label = prog.unique_label("shutdown_fallback_halt")
    prog.label(halt_label)
    prog.hlt()
    prog.jmp(halt_label)
    return prog


def sleep_until_keypress(prog: Program) -> Program:
    """Waits for a key to be pressed, then returns. NOT a real ACPI sleep
    state -- see the module docstring for why (no interrupt-driven wake
    without IDT/PIC setup, which this codebase doesn't have yet). This is
    a busy-poll pause, useful for e.g. a "press any key to wake" screen,
    not a power-saving mechanism."""
    keyboard.wait_key_scancode(prog, label_prefix=prog.unique_label("sleep_wait"))
    return prog
