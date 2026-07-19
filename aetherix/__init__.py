"""
Aetherix -- a cross-platform toolkit for building real-mode bootloaders,
protected-mode kernels, and bootable disk images, written in C and driven
entirely from Python. No nasm, ld, or QEMU required to build an image;
you'll want an emulator (QEMU/VirtualBox/VMware) or real hardware to run it.

Quick start:

    from aetherix import Project

    with Project("MyOS") as os:

        @os.kernel_entry
        def main(prog, drivers):
            drivers.vga.clear(prog)
            drivers.vga.print_string(prog, "Hello, world!")
            prog.hlt()

        os.build("myos.img")

Then: qemu-system-i386 -drive file=myos.img,format=raw
"""
from .context import Project
from .boot.realmode import BootSector
from .kernel.builder import Kernel
from .image.diskimage import DiskImage, SIZE_PRESETS
from .fs.simplefs import Archive
from .drivers.hal import HAL
from .asm import Program
from . import regs
from .drivers import vga, keyboard, speaker, terminal, graphics, power
from . import imaging

__version__ = "0.1.0"

__all__ = [
    "Project", "BootSector", "Kernel", "DiskImage", "SIZE_PRESETS",
    "Archive", "HAL", "Program", "regs", "vga", "keyboard", "speaker", "terminal",
    "graphics", "power", "imaging",
]
