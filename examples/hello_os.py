"""
hello_os -- minimal Aetherix example.

Boots to real mode, sets up segments, prints a message via BIOS, loads a
32-bit kernel that switches to protected mode, clears the VGA text screen,
prints a message directly to video memory, waits for a keypress via the
PS/2 controller, then halts.

Run:
    python examples/hello_os.py

Then boot the produced image in an emulator, e.g.:
    qemu-system-i386 -drive file=hello_os.img,format=raw
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aetherix import Project, regs

with Project("HelloOS", boot_message="HelloOS bootloader starting...") as os_:

    @os_.kernel_entry
    def main(prog, drivers):
        drivers.vga.clear(prog, attr=drivers.vga.WHITE_ON_BLACK)
        drivers.vga.print_string(prog, "Hello from Aetherix!", row=0, col=0,
                                  attr=drivers.vga.GREEN_ON_BLACK)
        drivers.vga.print_string(prog, "Press any key to continue...", row=2, col=0)
        drivers.keyboard.wait_key_scancode(prog)
        drivers.vga.print_string(prog, "Key received. System halted.", row=4, col=0,
                                  attr=drivers.vga.YELLOW_ON_BLACK)
        prog.hlt()

    os_.add_file("readme.txt", "This file is embedded in the disk image via AVFS.\n")

    out_dir = Path(__file__).resolve().parent.parent / "build"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = os_.build(str(out_dir / "hello_os.img"))
    print(f"Built: {out} ({out.stat().st_size} bytes)")
    print(f"Kernel sectors: {(len(os_.kernel.assemble()) + 511) // 512}")
    print("Boot it with: qemu-system-i386 -drive file=hello_os.img,format=raw")
