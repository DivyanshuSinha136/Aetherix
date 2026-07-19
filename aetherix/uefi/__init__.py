"""
UEFI support -- a completely separate boot path from the BIOS-based
BootSector/Kernel/Project pipeline elsewhere in this package.

UEFI firmware boots a PE32+ executable in 64-bit long mode straight away
-- no 16-bit real mode, no 32-bit protected mode, no BIOS interrupts, no
MBR boot sector. It's a different architecture end to end: a PE32+ file
(pe.py) instead of a flat binary, GPT+FAT32 (gpt.py/fat32.py) instead of
an MBR disk image, UEFI protocol function-pointer calls (console.py)
instead of `int` instructions, and the Microsoft x64 calling convention
(app.py's generated prologue) regardless of host OS.

Quick start:

    from aetherix.uefi.app import UefiApp
    from aetherix.uefi import console, pe, diskimage

    app = UefiApp()

    @app.entry
    def main(prog, sys_table):
        console.clear_screen(prog, sys_table)
        console.println(prog, "Hello from Aetherix UEFI!", sys_table)

    efi_path = app.build("BOOTX64.EFI")
    diskimage.write_disk_image(efi_path.read_bytes(), "uefi.img")

(see examples/uefi_hello.py for a complete, runnable version). Then:
    qemu-system-x86_64 -bios /path/to/OVMF.fd -drive file=uefi.img,format=raw
"""
from .app import UefiApp
from . import console, pe, gpt, fat32, diskimage, keyboard, protocol, boot_services, guid

__all__ = ["UefiApp", "console", "pe", "gpt", "fat32", "diskimage",
           "keyboard", "protocol", "boot_services", "guid"]
