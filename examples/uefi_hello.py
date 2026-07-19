"""
uefi_hello -- a minimal, genuinely working UEFI application.

This is a completely different boot path from the other examples in this
directory (hello_os.py, aether_shell.py, graphics_os.py), which all boot
via BIOS/MBR. UEFI firmware boots a PE32+ executable directly in 64-bit
long mode -- no 16/32-bit stages, no BIOS interrupts, no boot sector.

Demonstrates: console output, cursor control, and keyboard input (all via
real UEFI protocol calls -- ConOut/ConIn), plus locating an arbitrary
protocol by GUID and calling a method on it that this package doesn't
wrap with a dedicated helper (aetherix.uefi.boot_services/protocol --
the general extension mechanism for any UEFI protocol you need that
isn't built in yet).

This builds:
  - uefi_hello.efi   -- the PE32+ application itself
  - uefi_hello.img   -- a complete GPT+FAT32 disk image containing it at
                         \\EFI\\BOOT\\BOOTX64.EFI, ready to boot in QEMU+OVMF
                         or write to a real USB stick

Run:
    python examples/uefi_hello.py

Then boot it with QEMU + OVMF (get OVMF.fd from your Linux distro's
`ovmf`/`edk2-ovmf` package, or https://github.com/tianocore/edk2):
    qemu-system-x86_64 -bios /path/to/OVMF.fd -drive file=build/uefi_hello.img,format=raw

Or verify it without QEMU/OVMF installed, via real x86-64 CPU emulation:
    pip install unicorn
    python tests/emulate_uefi.py build/uefi_hello.efi

Or write it to a real USB stick (BACK UP THE STICK FIRST -- this
overwrites the entire device):
    sudo dd if=build/uefi_hello.img of=/dev/sdX bs=4M status=progress conv=fsync
Then boot from it on any UEFI PC with Secure Boot disabled (this app
isn't signed).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aetherix.uefi.app import UefiApp
from aetherix.uefi import console, keyboard, diskimage
from aetherix import regs

app = UefiApp()


@app.entry
def main(prog, sys_table):
    console.clear_screen(prog, sys_table)
    console.set_cursor_position(prog, sys_table, 0, 0)
    console.println(prog, "Hello from Aetherix UEFI!", sys_table)
    console.println(prog, "", sys_table)
    console.println(prog, "This machine code was generated entirely by aetherix.uefi --", sys_table)
    console.println(prog, "no nasm, no ld, no C compiler toolchain for the .efi itself.", sys_table)
    console.println(prog, "", sys_table)
    console.println(prog, "Press any key to continue...", sys_table)
    keyboard.read_key(prog, sys_table, regs.R12, regs.R13)

    console.set_cursor_position(prog, sys_table, 0, 8)
    console.println(prog, "Key received. Returning control to firmware.", sys_table)


out_dir = Path(__file__).resolve().parent.parent / "build"
out_dir.mkdir(parents=True, exist_ok=True)

efi_path = app.build(str(out_dir / "uefi_hello.efi"))
print(f"Built: {efi_path} ({efi_path.stat().st_size} bytes)")

img_path = diskimage.write_disk_image(efi_path.read_bytes(), str(out_dir / "uefi_hello.img"))
print(f"Built: {img_path} ({img_path.stat().st_size} bytes)")
print("Boot it with: qemu-system-x86_64 -bios /path/to/OVMF.fd -drive file=uefi_hello.img,format=raw")
