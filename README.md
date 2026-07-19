# Aetherix

A Python-native operating system development framework for building bootable x86 and x86_64 systems. Aetherix supports both legacy BIOS and modern UEFI firmware, generates machine code directly through its own native encoder, and provides high-level APIs alongside low-level instruction control.

```python
from aetherix import Project

with Project("MyOS") as os:

    @os.kernel_entry
    def main(prog, drivers):
        drivers.vga.clear(prog)
        drivers.vga.print_string(prog, "Hello, world!")
        prog.hlt()

    os.build("myos.img")
```

```
qemu-system-i386 -drive file=myos.img,format=raw
```

Or write it to a USB stick: `sudo dd if=myos.img of=/dev/sdX bs=4M status=progress`
(VirtualBox/VMware also accept the raw `.img`/`.bin` directly as a virtual disk.)

## Install

```
pip install -e .
```

Requires a C compiler (gcc/clang/MSVC) on the machine building the package
— the native encoder core is compiled once, either at install time or on
first import for dev checkouts. **Nothing beyond that is required to
*build* an image** — Aetherix emits its own machine code, it doesn't shell
out to nasm/ld. You do need an emulator (QEMU/VirtualBox/VMware) or real
hardware to *run* what you build.

For image/graphics support (`aetherix.imaging`, used to prepare loading
screens/backgrounds/logos for `aetherix.drivers.graphics`), also install
Pillow -- a build-time-only dependency, not needed by the OS you build:

```
pip install -e ".[imaging]"
```

## Examples

- **`examples/hello_os.py`** -- minimal: prints fixed messages, waits for
  one keypress, halts.
- **`examples/aether_shell.py`** -- a genuinely interactive example OS: a
  live keyboard-echoing shell with a runtime VGA cursor that moves, wraps
  at end-of-row, and responds to Enter/Backspace/Escape/Shift (uppercase
  letters, shifted symbols), plus a speaker beep on F1 and real power
  control -- F2 reboots (works everywhere), F3 attempts a QEMU/Bochs-only
  shutdown. What appears on screen depends on what you type, not a
  fixed script. Verified end-to-end under CPU emulation -- typing,
  Shift, backspace, row-wrap, and all four power/halt paths are
  confirmed by actually executing the built image
  (`tests/test_emulation.py`), not just disassembling it.

```bash
python examples/aether_shell.py
qemu-system-i386 -drive file=build/aether_shell.img,format=raw
```

- **`examples/graphics_os.py`** -- VGA graphics (Mode 13h, 320x200,
  256-color): a boot loading animation (a real "video" -- a sequence of
  frames), a background image, and a home-screen logo composited on top.
  Uses `aetherix.imaging` to prepare ordinary PNG files (resize +
  quantize) at build time; ships with self-contained placeholder art
  (`examples/assets/generate_assets.py`) so it runs with no external
  files. Verified pixel-for-pixel and palette-for-palette correct under
  CPU emulation, including a shared-palette fix needed for compositing
  two independently-authored images correctly (see the Changelog).

```bash
python examples/graphics_os.py
qemu-system-i386 -drive file=build/graphics_os.img,format=raw
```

- **`examples/uefi_hello.py`** -- a **completely separate boot path**:
  a genuine UEFI application (PE32+, 64-bit long mode) on a GPT+FAT32 disk
  image, booted by real, unmodified OVMF firmware in real QEMU and
  confirmed printing the exact expected text before returning cleanly to
  firmware. See "UEFI support" below for how this differs from every
  other example here.

```bash
python examples/uefi_hello.py
qemu-system-x86_64 -bios /path/to/OVMF.fd -drive file=build/uefi_hello.img,format=raw
```

## UEFI support

Everything above (`Project`, `BootSector`, `Kernel`, the `drivers/`
package) is the **BIOS boot path**: 16-bit real mode → 32-bit protected
mode, MBR boot sector, BIOS interrupts. UEFI is a genuinely different
architecture, not an extension of it: firmware loads a **PE32+
executable directly into 64-bit long mode** -- no 16/32-bit stages, no
BIOS interrupts, no MBR boot sector at all. It boots from a **GPT-
partitioned disk with a FAT32 EFI System Partition**, not a flat binary.
I/O goes through UEFI protocol function-pointer calls (Microsoft x64
calling convention, regardless of host OS), not `int` instructions.

`aetherix.uefi` is a separate subsystem built for this from scratch,
alongside the BIOS path -- not built on top of it:

- **A minimal 64-bit long-mode instruction set** (`Program(bits=64)`,
  `native/encoder.c`) -- REX-prefixed mov/add/sub/cmp, RIP-relative `lea`
  for position-independent data references, indirect `call` through a
  register (for UEFI protocol function pointers). Supports the full
  register range RAX-R15.
- **A PE32+ writer** (`aetherix.uefi.pe`) -- produces a minimal but
  genuinely valid EFI_APPLICATION image. Deliberately has no `.reloc`
  section: since the 64-bit code generation only ever uses RIP-relative/
  rel32 addressing for its own code and data, it's already position-
  independent, so there's nothing to relocate.
- **`UefiApp`** (`aetherix.uefi.app`) -- generates the Microsoft x64 ABI
  prologue/epilogue (saves `EFI_SYSTEM_TABLE*` in a callee-saved
  register, returns `EFI_SUCCESS`) around your `@app.entry` function.
- **Console I/O** (`aetherix.uefi.console`, `aetherix.uefi.keyboard`) --
  `ConOut->OutputString/ClearScreen/SetCursorPosition/EnableCursor` and
  `ConIn->ReadKeyStroke` (busy-polled, same reasoning as the BIOS-side
  keyboard driver), UCS-2 (UTF-16LE) encoded as the UEFI spec requires.
- **A generic protocol-call extension mechanism**
  (`aetherix.uefi.protocol`, `aetherix.uefi.boot_services`) -- the
  built-in console/keyboard helpers only cover ConOut/ConIn; for any
  other UEFI protocol (Graphics Output, Block I/O, File System, or
  anything else), `boot_services.locate_protocol` finds the interface by
  GUID and `protocol.call` invokes an arbitrary method on it by byte
  offset -- no changes to this package needed to use a protocol it
  doesn't wrap yet, just the offsets from the UEFI spec or an EDK2
  header.
- **GPT + FAT32** (`aetherix.uefi.gpt`, `aetherix.uefi.fat32`,
  `aetherix.uefi.diskimage`) -- a real, from-scratch disk image builder:
  protective MBR, primary + backup GPT (with correct CRC32 checksums),
  and a FAT32 EFI System Partition containing `\EFI\BOOT\BOOTX64.EFI`.

**Scope**: this targets UEFI *applications* -- code that uses Boot
Services and returns control to firmware when done, like a shell command.
It is not a full custom OS loader (that would mean calling
`ExitBootServices`, taking over the memory map, and setting up your own
paging/interrupts) -- see `CONTRIBUTING.md` if you want to build that on
top of this.

**Verified at every layer, independently, not just by this codebase's
own logic checking itself:**
- The PE file: recognized by `file` as `PE32+ executable (EFI application) x86-64`
- The machine code: disassembled with `objdump -M intel -m i386:x86-64`
- Real execution: a Unicorn (`UC_MODE_64`) harness simulating
  `EFI_SYSTEM_TABLE`/`ConOut`/`ConIn`/`BootServices` confirms the exact
  right protocol calls happen with the exact right arguments, including
  R8/R9-argument calls (cursor positioning) and a `LocateProtocol` +
  custom-method-call round trip through an arbitrary GUID
  (`tests/emulate_uefi.py`, `tests/test_uefi.py`)
- The GPT: validated with `gdisk` (`gdisk -l disk.img`) -- "No problems found"
- The FAT32 filesystem: validated with `fsck.vfat` and `mtools`
  (`mdir`/`mcopy`), including extracting the file back out and confirming
  it's byte-for-byte identical
- **Real, unmodified OVMF firmware in real QEMU** boots the disk image,
  prints exactly the expected text with correct cursor positioning, and
  correctly blocks on a real keyboard-input prompt without crashing --
  the strongest verification in this entire project.

## What this actually is (read before you assume too much)

Building a full production OS with GPU, USB, camera, printer, and network
driver stacks is a multi-year effort even for large teams. This package
does **not** claim to hand you that. What it genuinely gives you:

- A verified-correct (disassembled and checked byte-for-byte) real-mode
  bootloader: segment setup, BIOS teletype output, A20 line enable, BIOS
  CHS disk loading with retry, and a far jump into your kernel.
- A verified-correct 16→32-bit transition: flat GDT construction, `lgdt`,
  CR0.PE set, far jump into a 32-bit code segment — all computed with
  exact, non-guessed offsets (see `tests/test_core.py`, and disassemble
  any image yourself with `objdump -D -b binary -m i8086` / `-m i386`).
- Working drivers for the hardware that's actually reachable without an
  existing OS or bus/protocol stack underneath you:
  - **VGA** text-mode framebuffer (clear screen, print strings)
  - **PS/2 keyboard** polling: full Scancode Set 1 table (letters, digits,
    punctuation, Tab), Shift-aware (uppercase, shifted symbols), plus a
    high-level `read_char` helper. Extended keys (arrows, Insert/Delete/
    Home/End/Page Up/Down) send a two-byte sequence and aren't covered yet.
  - **PC speaker** (PIT-driven beep with an actual audible duration)
  - **Power control** -- `restart` (soft, jump back to kernel start),
    `reboot` (hard, keyboard-controller reset pulse -- works on real
    hardware and every mainstream emulator), plus a `shutdown` that only
    works in QEMU/Bochs (the well-known magic-port trick, not real ACPI)
    and an approximate (non-ACPI) `sleep_until_keypress`. Real ACPI
    shutdown/sleep need parsing the ACPI tables and interpreting AML
    bytecode, which this codebase doesn't do -- see `aetherix.drivers.power`.
  - **Terminal** -- a *runtime* VGA cursor (not just compile-time-fixed
    text) that moves, wraps at end-of-row, and supports newline/backspace
    -- this is what makes `aether_shell.py` an actually interactive OS
    rather than a script of fixed messages
  - **Graphics** -- VGA Mode 13h (320x200, 256-color): palette upload and
    image blitting, so a kernel can show a loading screen, a background,
    and composited images, not just text. `aetherix.imaging` (needs
    Pillow, a build-time-only dependency) converts ordinary image files
    into embeddable form -- see `examples/graphics_os.py`.
- A minimal embedded read-only file archive format (**AVFS**) for bundling
  files into the disk image at build time.
- A Python API designed so both styles work: high-level (`drivers.vga.print_string(...)`)
  and low-level (`prog.mov16(AX, 0x1234)`, straight machine-code control).

### What is *not* implemented (and why)

GPU (beyond VGA Mode 13h -- no modern framebuffer/GOP/VBE/hardware
acceleration/3D), USB, Ethernet/networking, printer, fan control, battery
telemetry, and camera all need real bus enumeration or protocol stacks
(PCI, a USB host controller driver, ACPI, etc.) that don't exist yet in
this codebase. Rather than fake them, `aetherix.drivers.hal.HAL` lists
them as named, documented extension points — `HAL().scaffolded()` tells you
exactly what's missing and what it needs. Building a real driver for one of
these is exactly the kind of "further OS development" this project exists
to make approachable; see `CONTRIBUTING.md`.

There is also no file manager, task manager, calculator, or custom in-OS
programming language yet, no support for extended-scancode keys (arrows,
Insert/Delete/Home/End/Page Up/Down, right Ctrl/Alt -- they send a 0xE0
prefix byte this driver doesn't watch for), no Caps Lock, and no real
ACPI shutdown/sleep (see `aetherix.drivers.power`'s docstring for exactly
what that would need) -- those are natural next layers on top of what's
here. AVFS gives you build-time file embedding today; a runtime-writable
filesystem and the rest of the app suite are next.

## Architecture

```
aetherix/
  asm.py            # two-pass label/jump resolver over the native encoder (16/32/64-bit)
  regs.py           # register / condition-code constants
  context.py        # Project: the batteries-included entry point (BIOS path)
  imaging.py        # build-time image loading/quantization (needs Pillow)
  boot/realmode.py  # BootSector builder (512-byte MBR-style boot sector)
  kernel/
    pmode.py        # flat GDT construction
    builder.py       # Kernel: 16-bit trampoline + GDT + 32-bit body
  drivers/
    vga.py, keyboard.py, speaker.py, terminal.py, graphics.py, power.py   # implemented
    hal.py                                                                 # driver registry + extension points
  fs/simplefs.py    # AVFS embedded file archive
  image/diskimage.py # boot sector + kernel + AVFS -> flat .img/.bin
  uefi/              # separate boot path -- see "UEFI support" above
    app.py           # UefiApp: MS x64 ABI prologue/epilogue + PE assembly
    console.py       # ConOut protocol calls (output, cursor control)
    keyboard.py      # ConIn protocol calls (key input)
    protocol.py      # generic protocol-method-call extension mechanism
    boot_services.py # LocateProtocol (find a protocol interface by GUID)
    guid.py          # EFI_GUID mixed-endian encoding (shared by gpt.py too)
    pe.py            # PE32+ writer
    gpt.py, fat32.py, diskimage.py  # GPT + FAT32 ESP disk image builder
native/
  encoder.c         # the actual x86 instruction encoder (C, no deps)
```

Every layer is independently usable — you can build a raw `Program` and
hand-assemble arbitrary real/protected-mode code without ever touching
`Project`, or add a new driver module and register it with `HAL`.

## CLI

```
aetherix new MyOS          # scaffold a starter project script
aetherix info              # show implemented vs. scaffolded hardware support
```

## Verifying the output yourself

Don't take the byte correctness on faith — disassemble it:

```
head -c 512 myos.img > boot.bin
objdump -D -b binary -m i8086 boot.bin      # boot sector, 16-bit
dd if=myos.img of=kernel.bin bs=512 skip=1
objdump -D -b binary -m i386 kernel.bin      # (after the trampoline offset)
```

Or go further and actually *execute* it under CPU emulation (this is how
the LBA-disk-read fix below was verified — disassembly alone proves the
bytes are plausible x86, not that the boot sequence completes):

```
pip install unicorn
python tests/emulate.py myos.img
```

This prints the real-mode boot message, the disk reads that occurred
(LBA, sector count, destination), and the final VGA text buffer contents
— i.e., what a real screen would show, without needing QEMU/VirtualBox.

## Changelog

- **Added UEFI keyboard input, cursor control, and a generic protocol-
  extension mechanism.** `aetherix.uefi.keyboard` wraps
  `ConIn->ReadKeyStroke` (busy-polled); `aetherix.uefi.console` gained
  `SetCursorPosition`/`EnableCursor`. Both needed real support for R8/R9
  as argument registers (the Microsoft x64 ABI's 3rd/4th integer args),
  so the 64-bit encoder was extended from RAX-RDI to the full RAX-R15
  range. On top of that, `aetherix.uefi.boot_services.locate_protocol` +
  `aetherix.uefi.protocol.call` form a generic extension mechanism: look
  up *any* UEFI protocol by GUID and call *any* method on it by byte
  offset, without needing a dedicated wrapper for it in this package --
  the same escape-hatch philosophy as the BIOS-side `HAL`, adapted to
  UEFI's protocol model. Found and fixed a real encoding bug while
  building this: `RSP` and `R12` both encode to the same 3-bit ModRM
  field, which x86-64 always requires a SIB byte for regardless of
  addressing mode -- using `R12` as a base register (a very natural
  choice for holding a located protocol's interface pointer) silently
  corrupted the instruction stream without it. Verified via a Unicorn
  (`UC_MODE_64`) harness simulating `ConIn`/`BootServices` (proving the
  keyboard polling, R8-argument cursor calls, and the `LocateProtocol` +
  arbitrary-method-call round trip all execute correctly) and, again,
  real unmodified OVMF firmware in real QEMU -- which showed correct
  cursor-positioned output and correctly blocked on a live keyboard
  prompt without crashing.
- **Added UEFI support** (`aetherix.uefi`) -- a completely separate boot
  path from BIOS, built from scratch: a minimal 64-bit long-mode
  instruction set (`Program(bits=64)`), a PE32+ writer, `UefiApp` (MS x64
  ABI prologue/epilogue), UEFI console protocol calls, and a from-scratch
  GPT+FAT32 disk image builder. Verified at every layer independently --
  `file`/`objdump` for the PE structure and machine code, a Unicorn
  `UC_MODE_64` harness for real protocol-call execution, `gdisk`/
  `fsck.vfat`/`mtools` for the GPT and FAT32 (including byte-for-byte
  file extraction), and, most convincingly, **real unmodified OVMF
  firmware in real QEMU actually booting the built disk image** and
  printing the exact expected text before returning cleanly to firmware.
  See the "UEFI support" section above for the full scope (UEFI
  *applications*, not a full custom OS loader) and what it doesn't cover
  yet.
- **Expanded the keyboard driver and added power control.**
  `aetherix.drivers.keyboard`'s scancode table grew from 37 keys
  (letters/digits/space) to 48 (adding Tab and all standard US-QWERTY
  punctuation), plus a full Shift-aware shifted-symbol table (uppercase
  letters, `!@#$%^&*()`, etc.) and named constants for Ctrl/Alt/CapsLock/
  F1-F12. A new high-level `read_char` blocks for a printable key,
  handling Shift press/release automatically. `aetherix.drivers.power`
  is new: `restart` (soft, jumps back to the kernel's own entry point --
  `Kernel` now emits a label there automatically for this), `reboot`
  (hard, via the keyboard-controller reset pulse -- real, portable, works
  on actual hardware and every mainstream emulator), and a `shutdown` +
  `sleep_until_keypress` that are honestly scoped to what's achievable
  without a full ACPI implementation (real ACPI power states need parsing
  ACPI tables and interpreting AML bytecode, which is out of scope for
  now -- see the module's docstring and CONTRIBUTING.md). Needed one new
  native instruction (`out dx, ax` with a 16-bit operand-size override,
  for the shutdown magic-port trick, which expects a word write). All of
  it -- Shift-produced uppercase/symbols, and all three power actions --
  is verified by actually executing built images under CPU emulation, not
  just disassembly (`tests/test_emulation.py`).
- **Fixed two real disk-loading bugs, found via a real-hardware failure
  report**: any kernel larger than ~65 sectors (about 33KB) would hang
  right after the boot message on real QEMU/hardware, despite passing
  this project's own emulation-based tests.
  1. `load_kernel_lba` made a single LBA read call for the entire kernel.
     Real-mode segment:offset addressing has a 16-bit offset -- a kernel
     over ~65 sectors starting at `KERNEL_LOAD_OFFSET` (0x7E00) needs an
     offset past 0xFFFF, which silently wraps within the segment (or is
     rejected outright, which is what a real BIOS does). Fixed by reading
     in 64-sector chunks, advancing the destination by *segment* between
     chunks instead of by offset, which has no such limit.
  2. While fixing that, the chunk-advance math used `DX` as a scratch
     register for a doubling calculation -- forgetting that `DX`'s low
     byte *is* `DL`, the boot drive number. This silently corrupted the
     drive number after the first chunk, so every chunk after it failed.
     Fixed by using `SI` instead (free at that point in the loop).

  Both bugs slipped past this project's own CPU-emulation tests because
  the test harness itself was too lenient: it computed a read's
  destination as a plain linear address regardless of size (missing bug
  1) and never validated `DL` against the real boot drive across calls
  (missing bug 2). The harness (`tests/emulate.py`) now enforces the
  64KB segment boundary and checks `DL` on every `int 13h` call, and
  `tests/test_emulation.py` has a permanent regression test
  (`test_multi_chunk_disk_load_under_emulation`) built specifically to
  catch this class of bug going forward -- a small, fast kernel padded
  past 64 sectors, checked without needing to wait through the display
  examples' slow-to-emulate rendering/delay loops.
- **Added VGA graphics support** (`aetherix.drivers.graphics`, VGA Mode
  13h -- 320x200, 256-color) and a build-time image preparation module
  (`aetherix.imaging`, needs Pillow): loading-screen animations
  (`play_frames`), background images, and composited home-screen images
  (`show_image`), demonstrated end-to-end in `examples/graphics_os.py`.
  This needed a few new framework primitives: `Program.raw_bytes` (O(1)
  nodes for embedding large binary blobs -- the naive one-node-per-byte
  `db()` doesn't scale to a 64,000-byte image), `Program.mov32_label` and
  `Program.set_base_address` (so kernel code can reference embedded data
  whose address isn't known until assembly, the 32-bit counterpart to the
  boot sector's `mov16_label`), and `Kernel(graphics_mode=...)` (the BIOS
  video mode-set call has to happen in real mode, in the kernel's 16-bit
  trampoline, before the protected-mode switch -- the 32-bit body has no
  BIOS access). Also found and fixed a real bug during development: VGA
  Mode 13h has one 256-color palette for the *entire screen*, not one per
  image -- preparing a background and a logo independently (each getting
  its own best-fit palette) meant only the most-recently-uploaded palette
  was actually active on screen, silently corrupting the earlier image's
  colors. Fixed with `imaging.load_images_shared_palette`, which quantizes
  multiple images together against one shared palette; verified
  pixel-for-pixel and palette-for-palette correct by actual CPU execution
  (`tests/test_emulation.py`), not just disassembly.
- **Added a working, interactive example OS** (`examples/aether_shell.py`)
  and the `terminal` driver it's built on (`aetherix/drivers/terminal.py`):
  a runtime VGA cursor with putchar/newline/backspace, instead of only
  compile-time-fixed message printing. This needed three new primitive
  instructions in the native encoder -- register-indirect memory access
  (`[reg]` / `[reg+disp8]`, since the encoder previously only supported
  fixed absolute addresses) and register-register add/sub -- plus a
  `Program.busy_wait` helper (there's no timer instruction, so an audible
  speaker beep needs a CPU-cycle-burning delay loop, same as classic
  BIOS/DOS-era code). Verified end-to-end by scripting a sequence of
  keypresses through the CPU emulator: typing, backspace, row-wrap at
  column 80, and Escape-triggered halt are all confirmed by actual
  execution (`tests/test_emulation.py`).
- **Fixed a register-clobber bug in `load_kernel_lba`**: the boot drive
  number was saved into `BL` "to be safe across BIOS calls," then the very
  next instruction loaded all of `BX` with the `0x55AA` extension-check
  signature -- clobbering the saved value before it was restored into
  `DL`. The BIOS then (correctly) rejected the bogus drive number,
  reporting "lacks LBA disk support" on BIOSes that actually support it
  fine. Fixed by not stashing `DL` at all -- nothing in this method's own
  instructions ever writes to it, so it never needed preserving. The
  emulation test (`tests/emulate.py`) now also validates `DL` against the
  real boot drive on every `int 13h` call specifically to catch this
  class of bug (it previously always succeeded the call regardless of
  `DL`, which is why it missed this the first time).
- **Disk loading now uses LBA extended reads (`int 13h, ah=42h`)**
  instead of a single CHS read (`ah=02h`). A single CHS call cannot cross
  a BIOS track boundary; depending on how an emulator/BIOS geometry-
  translates the disk, a large kernel could silently fail to load and
  hang after the boot message with no error (this is exactly what
  happened when testing against QEMU's default hard-disk geometry). LBA
  reads have no such limitation and are supported by every BIOS shipped
  in the last ~25+ years. `BootSector.standard()` uses LBA by default;
  the old CHS method is still available as `load_kernel_chs()` with a
  documented caveat, for BIOSes old enough to lack extensions.
- Boot-sector string printing now uses a compact runtime loop
  (`mov al,[si]; ...; inc si`) instead of unrolling one BIOS call per
  character — roughly 8x smaller for a given message, which matters in a
  512-byte boot sector.

## License

AGPL-3.0-or-later.
