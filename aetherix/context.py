"""
`Project` is the top-level, batteries-included entry point most people
should start from. It wires together a `BootSector`, `Kernel`, `Archive`,
and `DiskImage` with sensible defaults (computing the kernel's sector
count automatically, generating a standard bootloader that loads exactly
that many sectors, etc.) while still exposing every underlying object for
people who want to override specific pieces.

    with Project("MyOS") as os:
        @os.kernel_entry
        def main(prog, drivers):
            drivers.vga.clear(prog)
            drivers.vga.print_string(prog, "Hello from Aetherix!")
            prog.hlt()

        os.build("myos.img")
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from .boot.realmode import BootSector
from .drivers.hal import HAL
from .fs.simplefs import Archive
from .image.diskimage import DiskImage
from .kernel.builder import Kernel


class Project:
    def __init__(self, name: str, boot_message: Optional[str] = None, hal: Optional[HAL] = None,
                 graphics_mode: Optional[int] = None):
        self.name = name
        self.boot_message = boot_message if boot_message is not None else f"{name} booting..."
        self.hal = hal or HAL()
        self.kernel = Kernel(hal=self.hal, graphics_mode=graphics_mode)
        self.archive = Archive()
        self.last_output_path: Optional[Path] = None

    # -- decorators -----------------------------------------------------

    def kernel_entry(self, fn):
        """Register the kernel's main function. See module docstring."""
        return self.kernel.entry(fn)

    # -- filesystem -------------------------------------------------------

    def add_file(self, name: str, data) -> "Project":
        self.archive.add_file(name, data)
        return self

    def add_path(self, path, name: str = None) -> "Project":
        self.archive.add_path(path, name=name)
        return self

    # -- build --------------------------------------------------------------

    def build(self, output_path: Union[str, Path], total_size: Union[int, str, None] = "floppy_1_44mb") -> Path:
        kernel_bytes = self.kernel.assemble()
        sectors = (len(kernel_bytes) + 511) // 512

        boot = BootSector.standard(kernel_sectors=sectors, message=self.boot_message)
        boot_bytes = boot.assemble()

        image = DiskImage()
        image.set_boot_sector(boot_bytes)
        image.set_kernel(kernel_bytes)
        if len(self.archive):
            image.set_filesystem(self.archive.build())

        path = image.write(output_path, total_size=total_size)
        self.last_output_path = path
        return path

    # -- context manager ------------------------------------------------

    def __enter__(self) -> "Project":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False  # never swallow exceptions
