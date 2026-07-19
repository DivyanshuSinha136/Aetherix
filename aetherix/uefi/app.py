"""
UefiApp: builds a UEFI application (.efi file).

Important scope note: this targets UEFI **applications** -- code that
runs using UEFI Boot Services (console output, etc.) and then returns
control to firmware, exactly like a shell command. It is not a full
custom OS loader: that would mean calling ExitBootServices, taking over
the memory map, setting up your own GDT/IDT/paging, and getting a
graphics framebuffer via GOP instead of the boot-time console -- a
substantially larger project than this. See CONTRIBUTING.md if you want
to build that on top of this.

A UEFI application's entry point is called as:

    EFI_STATUS EFIAPI UefiMain(EFI_HANDLE ImageHandle, EFI_SYSTEM_TABLE *SystemTable);

using the Microsoft x64 calling convention (RCX=ImageHandle,
RDX=SystemTable) regardless of host OS -- that's a UEFI spec requirement,
not a Windows-specific detail. `UefiApp` generates a prologue that saves
SystemTable into RBX (callee-saved, so it survives any calls your code
makes) before handing control to your `@app.entry` function, and an
epilogue that returns EFI_SUCCESS (0) once it falls through.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Union

from .. import regs
from ..asm import Program
from .pe import build_pe32plus

SYSTEM_TABLE_REG = regs.EBX  # where the prologue leaves EFI_SYSTEM_TABLE*


class UefiApp:
    def __init__(self):
        self._entry_fn: Optional[Callable[[Program, int], None]] = None
        self._auto_return = True

    def entry(self, fn: Callable[[Program, int], None]) -> Callable[[Program, int], None]:
        """Decorator: register the application's main function.

            @app.entry
            def main(prog, system_table_reg):
                console.println(prog, "Hello from UEFI!", system_table_reg)

        `prog` is the 64-bit Program to append instructions to;
        `system_table_reg` is the register holding the EFI_SYSTEM_TABLE
        pointer (pass it straight through to `aetherix.uefi.console`'s
        functions).
        """
        self._entry_fn = fn
        return fn

    def no_auto_return(self) -> "UefiApp":
        """Opt out of the automatic `return EFI_SUCCESS` epilogue, e.g. if
        your entry function sets its own status code in EAX and returns,
        or ends in an infinite loop of its own."""
        self._auto_return = False
        return self

    def assemble(self) -> bytes:
        if self._entry_fn is None:
            raise RuntimeError(
                "No entry function registered. Use @app.entry to define "
                "one before calling assemble()/build()."
            )

        prog = Program(bits=64)

        # Prologue: RSP%16==8 at entry (return address just pushed by
        # firmware's CALL). One push brings it back to 16-aligned, which
        # `console.py`'s calls (each doing sub/add rsp,0x20 -- a multiple
        # of 16 -- around their own CALL) depend on.
        prog.push64(regs.EBX)
        prog.mov_rr64(SYSTEM_TABLE_REG, regs.EDX)  # RDX = SystemTable (2nd arg)

        self._entry_fn(prog, SYSTEM_TABLE_REG)

        if self._auto_return:
            prog.xor_rr64(regs.EAX, regs.EAX)  # EFI_SUCCESS = 0
            prog.pop64(regs.EBX)
            prog.ret64()

        return prog.assemble()

    def build(self, output_path: Union[str, Path]) -> Path:
        """Assemble and write a complete .efi (PE32+ EFI_APPLICATION)
        file. Entry point is always offset 0 -- the prologue above is
        unconditionally the first thing assembled."""
        code = self.assemble()
        pe = build_pe32plus(code, entry_point_offset=0)
        path = Path(output_path)
        path.write_bytes(pe.data)
        return path
