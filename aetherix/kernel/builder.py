"""
Kernel builder.

The bootloader hands off to the kernel image still in 16-bit real mode
(that's how every x86 boots). The kernel image therefore begins with a
short 16-bit trampoline that optionally sets a BIOS video mode, builds a
flat GDT, sets the CPU's PE bit, and far-jumps into the 32-bit body -- all
inside the same loaded blob.

Layout of the assembled kernel image:

    [ 16-bit trampoline ][ 6-byte GDTR ][ 24-byte GDT table ][ 32-bit body ]

Because the trampoline only uses fixed-width instructions (an optional
BIOS mode-set call, cli, lgdt, mov cr0, or eax, far jmp), its size is a
known constant for a given `graphics_mode` setting -- so the addresses of
the GDTR/GDT/32-bit-body can be computed analytically before ever emitting
the trampoline's actual immediate values, no forward-reference patching
required. The body's own resolved base address is then handed to it via
`Program.set_base_address` before the entry function runs, so code inside
it can reference embedded data (e.g. `graphics.show_image`'s palette/pixel
blobs) via `prog.mov32_label(...)` without the caller tracking addresses
by hand.
"""
from __future__ import annotations

from typing import Callable, Optional

from .. import regs
from ..asm import Program
from ..boot.realmode import KERNEL_LOAD_OFFSET
from ..drivers.hal import HAL
from . import pmode

# cli(1) + lgdt(5) + mov_eax_cr0(3) + or_eax(6) + mov_cr0_eax(3) + far_jmp16(5)
_TRAMPOLINE_SIZE_BASE = 1 + 5 + 3 + 6 + 3 + 5
# Optional BIOS video mode-set, prepended before everything else:
# mov8(AH,2) + mov8(AL,2) + int(2) = 6 bytes
_MODE_SET_SIZE = 6


class Kernel:
    def __init__(self, hal: Optional[HAL] = None, graphics_mode: Optional[int] = None):
        self.hal = hal or HAL()
        self.graphics_mode = graphics_mode
        self._entry_fn: Optional[Callable[[Program, HAL], None]] = None
        self._auto_halt = True

    def entry(self, fn: Callable[[Program, HAL], None]) -> Callable[[Program, HAL], None]:
        """Decorator: register the kernel's main function.

            @kernel.entry
            def main(prog, drivers):
                drivers.vga.clear(prog)
                ...

        `prog` is the 32-bit Program to append instructions to; `drivers`
        is the HAL instance with the available driver modules.
        """
        self._entry_fn = fn
        return fn

    def no_auto_halt(self) -> "Kernel":
        """Opt out of the automatic trailing `hlt` loop, e.g. if your entry
        function already ends in an infinite loop of its own."""
        self._auto_halt = False
        return self

    def _trampoline_size(self) -> int:
        return _TRAMPOLINE_SIZE_BASE + (_MODE_SET_SIZE if self.graphics_mode is not None else 0)

    def assemble(self, load_offset: int = KERNEL_LOAD_OFFSET) -> bytes:
        if self._entry_fn is None:
            raise RuntimeError(
                "No kernel entry function registered. Use @kernel.entry to "
                "define one before calling assemble()/build()."
            )

        trampoline_size = self._trampoline_size()
        gdtr_offset = trampoline_size
        gdt_table_offset = gdtr_offset + pmode.GDTR_SIZE
        body_offset = gdt_table_offset + pmode.GDT_TABLE_SIZE

        gdt_table_linear = load_offset + gdt_table_offset
        gdtr_linear = load_offset + gdtr_offset
        body_linear = load_offset + body_offset

        # Build the 32-bit kernel body fresh each time this is called, so
        # calling assemble()/sector_count() more than once (a very natural
        # thing to do -- sector_count() needs a size before the final
        # build) never double-appends the entry function's instructions.
        prog32 = Program(bits=32)
        prog32.set_base_address(body_linear)
        prog32.label("__kernel_entry_start")  # power.restart() jumps back here
        self._entry_fn(prog32, self.hal)
        if self._auto_halt:
            prog32.label("__kernel_halt")
            prog32.hlt()
            prog32.jmp("__kernel_halt")
        body_bytes = prog32.assemble()

        trampoline = Program(bits=16)
        if self.graphics_mode is not None:
            # BIOS video mode set (int 0x10, ah=0x00) -- must happen here,
            # still in real mode; the 32-bit body has no BIOS access.
            trampoline.mov8(regs.AH, 0x00)
            trampoline.mov8(regs.AL, self.graphics_mode & 0xFF)
            trampoline.interrupt(0x10)
        trampoline.cli()
        trampoline.lgdt(gdtr_linear & 0xFFFF)
        trampoline.mov_eax_cr0()
        trampoline.or_eax(0x00000001)  # set PE (protection enable) bit
        trampoline.mov_cr0_eax()
        trampoline.far_jmp16(pmode.CODE_SELECTOR, body_linear & 0xFFFF)
        trampoline_bytes = trampoline.assemble()
        assert len(trampoline_bytes) == trampoline_size, (
            f"internal error: trampoline size drifted ({len(trampoline_bytes)} != {trampoline_size})"
        )

        gdtr_bytes = pmode.build_gdtr(gdt_table_linear)
        gdt_table_bytes = pmode.build_flat_gdt_table()

        image = trampoline_bytes + gdtr_bytes + gdt_table_bytes + body_bytes
        return image

    def sector_count(self, load_offset: int = KERNEL_LOAD_OFFSET) -> int:
        """How many 512-byte disk sectors this kernel image needs once
        assembled (rounded up)."""
        size = len(self.assemble(load_offset=load_offset))
        return (size + 511) // 512
