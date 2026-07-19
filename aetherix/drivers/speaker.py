"""
PC speaker driver.

Drives the legacy PC speaker via PIT (8253/8254) channel 2 and system
control port 0x61. Present on essentially all PC-compatible hardware and
emulators. This gives a kernel audible beep/alarm capability without any
audio driver stack.
"""
from __future__ import annotations

from .. import regs
from ..asm import Program

PIT_CHANNEL2 = 0x42
PIT_COMMAND = 0x43
PC_SPEAKER_PORT = 0x61

PIT_BASE_FREQUENCY = 1193182  # Hz, fixed PIT input clock


def beep_start(prog: Program, frequency_hz: int = 1000) -> Program:
    """Start the PC speaker at `frequency_hz` (leaves it on -- call
    `beep_stop` to silence it)."""
    divisor = max(1, min(0xFFFF, PIT_BASE_FREQUENCY // max(1, frequency_hz)))
    prog.mov8(regs.AL, 0xB6)         # channel 2, lobyte/hibyte, square wave
    prog.out_al(PIT_COMMAND)
    prog.mov8(regs.AL, divisor & 0xFF)
    prog.out_al(PIT_CHANNEL2)
    prog.mov8(regs.AL, (divisor >> 8) & 0xFF)
    prog.out_al(PIT_CHANNEL2)
    # Set bits 0 and 1 of port 0x61 to gate the speaker to the PIT output,
    # OR-ing into the existing state so we don't clobber other bits.
    prog.in_al(PC_SPEAKER_PORT)
    prog.or_al(0x03)
    prog.out_al(PC_SPEAKER_PORT)
    return prog


def beep_stop(prog: Program) -> Program:
    """Silence the PC speaker."""
    prog.in_al(PC_SPEAKER_PORT)
    prog.mov8(regs.AL, 0x00)
    prog.out_al(PC_SPEAKER_PORT)
    return prog


def beep(prog: Program, frequency_hz: int = 1000, duration_iterations: int = 2_000_000) -> Program:
    """Start the speaker, hold it for `duration_iterations` busy-wait
    cycles (there's no timer/sleep instruction in this encoder subset --
    see `Program.busy_wait`), then stop it. `duration_iterations` is CPU-
    speed-dependent or emulator-speed-dependent, not a calibrated
    millisecond count; adjust to taste. Clobbers EBX (busy_wait's scratch
    register)."""
    beep_start(prog, frequency_hz)
    prog.busy_wait(regs.EBX, duration_iterations)
    beep_stop(prog)
    return prog
