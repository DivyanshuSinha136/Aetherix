# Aetherix — Usage Guide

This document walks through every layer of the API, from the batteries-
included `Project` down to raw instruction-level control. Read top to
bottom if you're new; jump to a section if you already know what you're
looking for.

Contents:

1. [Install](#1-install)
2. [Quick start](#2-quick-start)
3. [The `Project` API](#3-the-project-api)
4. [Drivers: `vga`, `keyboard`, `speaker`](#4-drivers-vga-keyboard-speaker)
4a. [Graphics: images and animations](#4a-graphics-images-and-animations)
5. [The `HAL` (hardware registry)](#5-the-hal-hardware-registry)
6. [Embedding files: `Archive` / AVFS](#6-embedding-files-archive--avfs)
7. [Disk images: `DiskImage`, sizes, writing to real media](#7-disk-images-diskimage-sizes-writing-to-real-media)
8. [Writing your own bootloader: `BootSector`](#8-writing-your-own-bootloader-bootsector)
9. [Writing your own kernel: `Kernel`](#9-writing-your-own-kernel-kernel)
10. [Raw instruction-level control: `Program`](#10-raw-instruction-level-control-program)
11. [Registers and condition codes: `regs`](#11-registers-and-condition-codes-regs)
12. [The CLI](#12-the-cli)
13. [Running what you built](#13-running-what-you-built)
14. [Verifying output yourself](#14-verifying-output-yourself)
15. [Extending Aetherix: adding a driver or opcode](#15-extending-aetherix-adding-a-driver-or-opcode)
16. [Common errors and what they mean](#16-common-errors-and-what-they-mean)
17. [Full worked example](#17-full-worked-example)
18. [UEFI support (a separate boot path)](#18-uefi-support-a-separate-boot-path)

---

## 1. Install

```bash
pip install -e .
```

You need a C compiler (gcc/clang/MSVC) available on the machine that
installs the package — the native encoder core (`native/encoder.c`) gets
compiled once, either during install or automatically on first `import
aetherix` for a dev checkout. After that, building images needs **no**
external tools (no nasm, no ld). Running what you build needs an emulator
(QEMU/VirtualBox/VMware) or real hardware.

```bash
python -c "import aetherix; print(aetherix.__version__)"
```

For graphics/image support (`aetherix.imaging`, section 4a), also install
Pillow -- a build-time-only dependency, not needed by the OS you build:

```bash
pip install -e ".[imaging]"
```

---

## 2. Quick start

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

```bash
qemu-system-i386 -drive file=myos.img,format=raw
```

That's the entire pipeline: bootloader generation, kernel assembly,
protected-mode switch, and disk image writing all happen inside
`os.build(...)`.

---

## 3. The `Project` API

`Project` is the object most people should start from. It owns a `Kernel`,
an `Archive` (for embedded files), and a `HAL` (hardware registry), and
wires them into a `BootSector` + `DiskImage` automatically when you call
`build()`.

```python
from aetherix import Project

proj = Project(
    name="MyOS",                          # used in default boot message
    boot_message="MyOS is starting...",   # optional, overrides the default
    graphics_mode=None,                    # set to graphics.MODE_13H for VGA graphics (section 4a);
                                            # leave as None for text-mode vga/terminal output
)
```

### `@proj.kernel_entry`

Registers your kernel's main function. It receives:
- `prog` — a 32-bit `Program` to append instructions to (this is your
  kernel body, running in protected mode with a flat memory model)
- `drivers` — the project's `HAL` instance (see section 4/5)

```python
@proj.kernel_entry
def main(prog, drivers):
    drivers.vga.clear(prog)
    prog.hlt()
```

You can only register one entry function per `Project`/`Kernel`. Calling
`@proj.kernel_entry` again replaces the previous one.

### `proj.add_file(name, data)` / `proj.add_path(path, name=None)`

Embed a file into the disk image at build time (see section 6 for the
format and its limits).

```python
proj.add_file("readme.txt", "Hello from disk!\n")   # str or bytes
proj.add_path("assets/logo.bin")                      # reads from disk
proj.add_path("assets/logo.bin", name="logo.img")     # override stored name
```

### `proj.build(output_path, total_size="floppy_1_44mb")`

Assembles the kernel, computes how many sectors it needs, builds a
standard bootloader that loads exactly that many sectors, assembles any
embedded file archive, and writes the final flat disk image.

```python
out_path = proj.build("myos.img")                      # 1.44MB floppy image (default)
out_path = proj.build("myos.img", total_size="hdd_64mb")  # bigger image
out_path = proj.build("myos.img", total_size=2 * 1024 * 1024)  # exact byte count
out_path = proj.build("myos.img", total_size=None)      # no padding, minimal size
```

Returns a `pathlib.Path`. Also stored at `proj.last_output_path`.

### Context manager

`with Project(...) as proj:` doesn't do resource cleanup (there's no
open handle to release) — it exists so `Project` composes naturally with
other context-managed code in a build script, and to make the visual
scope of "everything about this OS project" explicit. It never swallows
exceptions raised inside the `with` block.

You can also skip the `with` entirely:

```python
proj = Project("MyOS")

@proj.kernel_entry
def main(prog, drivers):
    ...

proj.build("myos.img")
```

---

## 4. Drivers: `vga`, `keyboard`, `speaker`

These are plain Python modules — each function takes a `Program` (and
usually other arguments) and appends instructions to it. Access them
through `drivers` in your kernel entry function, or import directly:

```python
from aetherix.drivers import vga, keyboard, speaker
```

### `vga` — VGA text-mode framebuffer (0xB8000, 80x25, 16 colors)

```python
vga.clear(prog, attr=vga.WHITE_ON_BLACK)

vga.print_string(prog, "Hello!", row=0, col=0, attr=vga.GREEN_ON_BLACK)

# Write a single character held at runtime in a register (e.g. an echoed
# keypress) to a fixed screen cell:
vga.print_at_runtime(prog, row=5, col=0, char_reg=regs.AL, attr=vga.WHITE_ON_BLACK)
```

Built-in attribute constants: `WHITE_ON_BLACK`, `GREEN_ON_BLACK`,
`RED_ON_BLACK`, `YELLOW_ON_BLACK`. You can also build your own attribute
byte: low nibble = foreground color (0–15), high nibble = background
color (0–7), e.g. `0x1F` = white text on blue background.

`print_string` unrolls one memory write per character at **compile
time** — great for short, fixed status/boot messages. It is not meant for
building a full scrolling terminal (see section 15/roadmap for where that
belongs).

### `keyboard` — PS/2 controller polling

Low-level: blocks for one scancode, make **or** break (key release) code:

```python
keyboard.wait_key_scancode(prog)   # blocks for one key event; leaves the raw scancode in AL
```

High-level: blocks until a *printable* key is typed, handling Shift
automatically (uppercase letters, shifted symbols) and silently ignoring
key releases and non-printable keys (arrows, function keys, etc):

```python
keyboard.init(prog)          # call once at kernel startup -- zeroes the Shift-state flag
keyboard.read_char(prog)     # blocks; leaves an ASCII byte in AL
```

`SCANCODE_SET1_ASCII` covers 48 keys: letters, digits, space, Tab, and
standard US-QWERTY punctuation (`` ` - = [ ] \ ; ' , . / ``).
`SCANCODE_SET1_SHIFTED` is the parallel shifted version (uppercase
letters, `!@#$%^&*()_+{}|:"<>?`) — `read_char` picks between the two at
runtime based on whether Shift is currently held. Extended keys (arrows,
Insert/Delete/Home/End/Page Up/Down, right Ctrl/Alt) send a two-byte
`0xE0`-prefixed sequence that isn't handled yet — a natural next driver
contribution (see `CONTRIBUTING.md`).

```python
from aetherix.drivers.keyboard import SCANCODE_SET1_ASCII, SCANCODE_SET1_SHIFTED
print(SCANCODE_SET1_ASCII[0x1E], SCANCODE_SET1_SHIFTED[0x1E])   # 'a' 'A'
print(SCANCODE_SET1_ASCII[0x02], SCANCODE_SET1_SHIFTED[0x02])   # '1' '!'
```

Named constants for keys handled specially rather than via the ASCII
tables: `SCANCODE_ESCAPE, SCANCODE_BACKSPACE, SCANCODE_TAB, SCANCODE_ENTER,
SCANCODE_LCTRL, SCANCODE_LSHIFT, SCANCODE_RSHIFT, SCANCODE_LALT,
SCANCODE_CAPSLOCK, SCANCODE_F1` through `SCANCODE_F12`. A break (release)
code is always the make code with bit 7 set — `BREAK_BIT = 0x80` is
provided for this (`SCANCODE_LSHIFT | BREAK_BIT` is Left Shift's release
code).

If you need both printable-key echoing *and* to catch special keys
(Enter, Escape, F-keys, power controls) in the same loop, `read_char`
alone won't do it — it deliberately never returns for those. Inline the
same Shift-aware dispatch pattern it uses instead; see
`examples/aether_shell.py` for a complete example that does exactly this.

### `speaker` — PC speaker via PIT channel 2

```python
speaker.beep_start(prog, frequency_hz=1000)
# ... later ...
speaker.beep_stop(prog)
```

There's no timer/sleep instruction in this encoder subset, so
`beep_start`/`beep_stop` back-to-back is inaudible -- use `speaker.beep`
for an actual audible duration:

```python
speaker.beep(prog, frequency_hz=880, duration_iterations=1_500_000)
```

`duration_iterations` is a busy-wait cycle count (CPU/emulator-speed
dependent, not a calibrated millisecond value) -- adjust to taste. Clobbers
`EBX` (see `Program.busy_wait` in section 10).

### `terminal` — a *runtime* VGA cursor

Unlike `vga.print_string`, which places text at a compile-time-known
position, `terminal` tracks a cursor at runtime -- this is what lets a
kernel echo keystrokes as they arrive rather than only printing messages
whose screen position is decided when you build the image.

```python
term = drivers.terminal
term.init(prog, start_row=3)          # cursor starts at the left of row 3

term.putchar_imm(prog, "H")            # print one (compile-time-known) char
term.putchar_imm(prog, "i")            # at the runtime cursor, advance it,
                                        # and wrap to the next row at column 80

term.newline(prog)                     # jump to the start of the next row
term.backspace(prog)                   # erase the previous char, move cursor back
```

State lives at two fixed memory addresses (`terminal.CURSOR_ADDR_VAR`,
`terminal.COL_COUNT_VAR`, both near `0x00020000`) rather than a general
heap -- the encoder's 32-bit addressing is either a fixed absolute address
or `[register]`, so kernel-level "variables" are just reserved memory
cells the kernel promises not to reuse for anything else. If your kernel
grows past ~128KB, move these.

`putchar_imm` takes a Python string character known at build time --
that's exactly the shape you need for a scancode-dispatch loop like
`aether_shell.py`'s (see section 17), where the character to print is
whichever branch of a compile-time comparison chain matched, not a value
computed at runtime. For a character held in a register at runtime
instead (e.g. straight from `keyboard.read_char`), use `putchar_reg`:

```python
keyboard.read_char(prog)                        # AL = the typed character
terminal.putchar_reg(prog, regs.AL)              # print it at the cursor
```

`char_reg` must not be `EBX`/`ECX` (used internally to track the cursor).

### `power` — restart, reboot, shutdown, sleep

```python
power.restart(prog)              # soft: jump back to the top of the kernel's own entry point
power.reboot(prog)                # hard: keyboard-controller reset pulse (real hardware + emulators)
power.shutdown(prog)              # QEMU/Bochs-only ACPI soft-off magic port; halts if unsupported
power.sleep_until_keypress(prog)  # waits for a key, then returns (NOT real ACPI sleep)
```

Read this table before assuming more than it says:

| Function | What it actually does | Where it works |
|---|---|---|
| `restart` | Jumps to a label `Kernel` places at the very start of your entry function | Everywhere -- it's just a jump |
| `reboot` | Waits for the keyboard controller to be ready, then sends the CPU-reset pulse (port 0x64, command 0xFE) | Real hardware and every mainstream emulator -- BIOS POST runs again |
| `shutdown` | Writes the well-known magic value to QEMU/Bochs's ACPI shutdown ports (0x604/0xB004) | **QEMU and Bochs only.** Does nothing on real hardware or other emulators/hypervisors -- falls through to a halt loop there |
| `sleep_until_keypress` | Busy-polls for a keypress, then returns | Not a reduced-power state -- there's no interrupt-driven wake here (no IDT/PIC setup). It's a pause, named for the closest available behavior |

Real ACPI shutdown/sleep need parsing the ACPI tables (RSDP -> RSDT/XSDT
-> FADT -> DSDT) and interpreting AML bytecode to find the actual PM1
control port and sleep-type values -- a substantial project on its own,
not implemented here. See `aetherix/drivers/power.py`'s module docstring
and `CONTRIBUTING.md` if you want to build it.

`power.restart` requires the kernel to have been built via
`Kernel`/`Project` (which places the jump target automatically); using a
bare `Program` directly, put your own label at the top of your code and
`prog.jmp(...)` it yourself.

---

## 4a. Graphics: images and animations

VGA text mode (what `vga`/`terminal` use) can't show images. For that you
need VGA Mode 13h -- 320x200 resolution, 256 colors, a byte-per-pixel
linear framebuffer at physical address `0xA0000`. This is legacy VGA
graphics (not a modern GPU/framebuffer driver -- see section 5), but it's
enough for a loading screen, a background, and composited images.

### Enabling graphics mode

The BIOS call that sets the video mode has to happen in real mode, before
the protected-mode switch -- your 32-bit kernel body has no BIOS access.
So graphics mode is a `Kernel`/`Project` construction-time setting, not
something you call from inside `@kernel_entry`:

```python
from aetherix import Project
from aetherix.drivers import graphics

with Project("MyOS", graphics_mode=graphics.MODE_13H) as os:
    @os.kernel_entry
    def main(prog, drivers):
        ...  # vga/terminal text-mode calls won't work here anymore
```

### Preparing images: `aetherix.imaging` (needs Pillow)

```bash
pip install Pillow
```

```python
from aetherix import imaging

asset = imaging.load_image("background.png")            # resized to 320x200, quantized to <=256 colors
icon = imaging.load_image("logo.png", width=64, height=64, fit=False)  # exact size, no resize
```

`load_image` returns an `ImageAsset` (`.width`, `.height`, `.palette_bytes`,
`.pixel_bytes`) ready to embed. Quantization works fine for photos/logos;
sharp text or thin line art may show some color banding at 256 colors.

**If more than one image will be on screen at the same time** (e.g. a
background plus a logo), prepare them together instead of separately:

```python
background, logo = imaging.load_images_shared_palette([
    ("background.png", 320, 200, True),   # (path, width, height, fit)
    ("logo.png", 64, 64, False),
])
```

This matters because VGA Mode 13h has **one 256-color palette for the
whole screen**, not one per image. If you prepare images independently,
each gets its own best-fit palette -- and only the most-recently-uploaded
palette is actually active in hardware, so an earlier image silently
displays through the wrong color table. `load_images_shared_palette`
quantizes every image against one combined palette so this can't happen.
(This isn't a workaround for a bug in Aetherix -- it's how indexed-color
VGA graphics has always worked; any real implementation has to handle it.)

### Displaying images: `aetherix.drivers.graphics`

```python
@os.kernel_entry
def main(prog, drivers):
    drivers.graphics.show_image(prog, background, x=0, y=0)
    drivers.graphics.show_image(prog, logo, x=128, y=68)   # composited on top
    prog.hlt()
```

`show_image` embeds the asset's palette/pixel bytes inline in `prog`,
uploads the palette to the VGA DAC, and blits the pixels at `(x, y)`.
`x + width` must be `<= 320` and `y + height` must be `<= 200`.

If you're managing the palette yourself (e.g. uploading it once for
several images that already share one), the two steps are available
separately:

```python
drivers.graphics.load_palette(prog, palette_label, entry_count=256)
drivers.graphics.blit(prog, pixel_label, width, height, x=0, y=0)
```

### A simple "video": `play_frames`

There's no timer interrupt in this codebase, so "video" here means a
sequence of images shown in order with a busy-wait delay between them --
a real boot animation, just not frame-rate-calibrated:

```python
frames = [imaging.load_image(f"loading_{i}.png") for i in range(6)]

@os.kernel_entry
def main(prog, drivers):
    drivers.graphics.play_frames(prog, frames, delay_iterations=3_000_000, loop=False)
    # falls through here once all frames have shown (loop=False)
    drivers.graphics.show_image(prog, background)
    prog.hlt()
```

`delay_iterations` is a CPU/emulator-speed-dependent busy-wait cycle
count, not a calibrated millisecond value -- adjust to taste. Pass
`loop=True` for a looping animation (in which case control never falls
through -- use `Kernel.no_auto_halt()`/pair it with your own logic if you
need something to happen after).

`aetherix.imaging.load_animation` loads an animated GIF/WEBP/PNG directly
into a list of frames, if that's more convenient than separate PNG files:

```python
frames = imaging.load_animation("boot.gif", max_frames=20)
```

See `examples/graphics_os.py` for a complete, verified example (loading
animation -> background + composited logo), with self-contained
placeholder art generated by `examples/assets/generate_assets.py`.

---

## 5. The `HAL` (hardware registry)

`HAL` is what `drivers` actually is inside your kernel entry function. It
tells you, honestly, what's implemented vs. what's a documented extension
point:

```python
from aetherix import HAL

hal = HAL()
print(hal.implemented())   # ['vga', 'keyboard', 'speaker']
print(hal.scaffolded())    # ['gpu', 'usb', 'ethernet', 'printer', 'fan', 'battery', 'camera']
```

Accessing a scaffolded driver raises `NotImplementedError` with an
explanation of what it needs (e.g. `hal.usb` tells you it needs a
UHCI/EHCI/xHCI host controller driver) rather than silently doing
nothing.

### Registering your own driver

```python
from aetherix.drivers.hal import HAL
import my_serial_driver   # your module, following the vga.py/keyboard.py shape

hal = HAL()
hal.register("serial", my_serial_driver)

proj = Project("MyOS", hal=hal)

@proj.kernel_entry
def main(prog, drivers):
    drivers.serial.write_byte(prog, ord("X"))
```

See `CONTRIBUTING.md` for the expected shape of a driver module and a
concrete list of realistic next drivers to build (serial port, parallel
printer, PIT timer, PCI enumeration).

---

## 6. Embedding files: `Archive` / AVFS

AVFS ("Aetherix Volume File System") is a small, **read-only, build-time**
file archive — you embed files when you build the image; there's no
kernel-side code yet that parses it back out (that's a natural thing to
build once you have a driver that can read disk sectors from inside the
kernel, rather than just at boot via BIOS).

```python
from aetherix import Archive

archive = Archive()
archive.add_file("readme.txt", "Hello from disk!\n")   # str -> utf-8 encoded, or raw bytes
archive.add_path("assets/logo.bin")                      # reads a file from disk
archive.add_path("assets/logo.bin", name="logo.img")     # override the stored name

blob = archive.build()   # bytes -- header + file table + raw file data
```

Limits: file names are UTF-8, up to 32 bytes; up to 255 files per archive.
Via `Project.add_file`/`Project.add_path`, an archive is built automatically
and appended after the kernel's sectors when you call `proj.build(...)`.

Archive layout (if you want to parse it yourself, e.g. from kernel code):

```
offset 0:  magic        4 bytes   b"AVFS"
offset 4:  version      1 byte    currently 1
offset 5:  file_count   1 byte    up to 255
offset 6:  entries      file_count * 40 bytes:
               name      32 bytes  UTF-8, NUL-padded
               offset    4 bytes   little-endian, from start of archive
               size      4 bytes   little-endian, bytes
offset 6 + file_count*40: raw file data, back-to-back in entry order
```

---

## 7. Disk images: `DiskImage`, sizes, writing to real media

`DiskImage` is the lower-level object `Project.build()` uses internally.
Use it directly if you want to assemble a boot sector/kernel/filesystem
combination that didn't come from `Project` (e.g. a hand-built
`BootSector` for a custom boot flow).

```python
from aetherix import DiskImage, SIZE_PRESETS

img = DiskImage()
img.set_boot_sector(boot_bytes)   # must be exactly 512 bytes, ending 0x55 0xAA
img.set_kernel(kernel_bytes)       # any length; padded to a sector boundary automatically
img.set_filesystem(archive_bytes)  # optional, appended after the kernel

data = img.build(total_size="floppy_1_44mb")   # bytes
path = img.write("myos.img", total_size="floppy_1_44mb")   # writes to disk, returns Path
```

`total_size` accepts:
- A preset name from `SIZE_PRESETS`: `"floppy_1_44mb"` (default),
  `"floppy_2_88mb"`, `"hdd_10mb"`, `"hdd_64mb"`, `"hdd_128mb"`
- An explicit integer byte count
- `None` — no padding; the image is exactly boot sector + kernel + fs, no more

Raises `ValueError` if the assembled content is larger than the requested
total size (pick a bigger preset, or shrink your kernel/files).

### Writing to a USB stick / real disk

```bash
sudo dd if=myos.img of=/dev/sdX bs=4M status=progress conv=fsync
```

**Double- and triple-check `/dev/sdX`** — this overwrites the entire
target device with no confirmation prompt. VirtualBox and VMware both
accept the same raw `.img`/`.bin` file directly as a virtual disk;
QEMU via `-drive file=myos.img,format=raw`.

---

## 8. Writing your own bootloader: `BootSector`

Most people don't need this directly — `Project.build()` generates a
standard one for you. Use `BootSector` yourself if you want a custom boot
flow (different disk geometry, a multi-stage loader, extra diagnostics
before loading the kernel, etc).

### The standard bootloader

```python
from aetherix import BootSector

boot = BootSector.standard(kernel_sectors=12, message="MyOS booting...")
boot_bytes = boot.assemble()   # exactly 512 bytes, ends 0x55 0xAA
```

`standard()` does, in order: segment setup, boot message (if given), A20
line enable, BIOS CHS disk load (with retry on error) of `kernel_sectors`
sectors into `0x0000:0x7E00`, a far jump into the loaded kernel, and a
halt loop as a defensive fallback if the jump is ever skipped.

### Building a custom bootloader step by step

```python
from aetherix.boot.realmode import BootSector
from aetherix import regs

b = BootSector()
b.setup_segments(stack_top=0x7C00)
b.print_message("Loading MyOS...")
b.enable_a20()
b.load_kernel_lba(sectors=20, start_lba=1)
b.jump_to_kernel(segment=0x0000, offset=0x7E00)
b.halt_forever()

boot_bytes = b.assemble()
```

`load_kernel_lba` (what `standard()` uses) loads sectors via BIOS INT 13h
extended reads (`ah=0x42`), which have no disk-geometry/track-boundary
limitations — it checks for extension support first and prints a clear
fatal message and halts if the BIOS doesn't have it (essentially never,
on anything made in the last ~25 years). It reads in `chunk_sectors`-sized
pieces (default 64 = 32KB) rather than one call for the whole kernel:
real-mode segment:offset addressing has a 16-bit offset, so a single read
of more than ~65 sectors would need an offset past 0xFFFF, which silently
wraps or fails on real hardware. A `load_kernel_chs` method also
exists using the classic CHS read (`ah=0x02`) for targeting very old
BIOSes, but a **single CHS call cannot cross a BIOS track boundary** —
requesting more sectors than fit in the current track fails silently into
this method's retry loop. Prefer `load_kernel_lba` unless you specifically
need CHS.

Every method returns `self`, so they chain. `b.prog` is the underlying
16-bit `Program` — you can call any `Program` method on it directly for
things not covered by the high-level methods:

```python
b.prog.mov16(regs.AX, 0x1234)   # raw instruction, right alongside the high-level calls
```

`assemble()` raises `ValueError` if your code doesn't fit in 510 bytes
(the last 2 bytes are reserved for the `0x55 0xAA` signature) — move logic
into the kernel/a second stage if you hit this.

---

## 9. Writing your own kernel: `Kernel`

`Kernel` builds the 16-bit protected-mode entry trampoline, a flat GDT,
and your 32-bit kernel body, and glues them into one blob with correctly
computed addresses (verified by disassembly — see section 14).

```python
from aetherix import Kernel, HAL

k = Kernel(hal=HAL())   # hal is optional; defaults to a fresh HAL()

@k.entry
def main(prog, drivers):
    drivers.vga.clear(prog)
    drivers.vga.print_string(prog, "Kernel running!")
    prog.hlt()

kernel_bytes = k.assemble()          # the full loadable kernel image (bytes)
sectors_needed = k.sector_count()    # how many 512-byte disk sectors this needs
```

`k.entry` is the same decorator as `Project.kernel_entry` (in fact
`Project.kernel_entry` just forwards to it). Calling `assemble()` or
`sector_count()` more than once is safe — each call rebuilds the 32-bit
body fresh rather than appending to shared state.

### `k.no_auto_halt()`

By default, `Kernel` appends a safety-net `hlt` loop after your entry
function, in case it doesn't already end in one. If your kernel has its
own infinite loop (e.g. polling for input forever) and you don't want the
extra halt appended:

```python
k.no_auto_halt()
```

### Memory model inside your kernel body

Your 32-bit kernel body runs with a **flat** memory model: the GDT's code
and data segments both have base=0, limit=4GB, so linear address ==
physical address everywhere. That's why `vga.clear()` can just write to
`0xB8000` directly with no segment math. Your kernel is loaded at physical
address `0x7E00` (right after the 512-byte boot sector) and stays there —
there's no relocation step, and no code above the 1MB boundary yet (see
`CONTRIBUTING.md` for what a "long mode"/64-bit or higher-half kernel
would need on top of this).

---

## 10. Raw instruction-level control: `Program`

Every higher-level builder (`BootSector`, `Kernel`) is just composing
`Program` calls. You can use `Program` completely on its own for full,
byte-level control over what gets emitted — no BootSector/Kernel/Project
involved at all.

```python
from aetherix.asm import Program
from aetherix import regs

p = Program(bits=16)   # or bits=32 for protected-mode code
p.cli()
p.xor16(regs.AX, regs.AX)
p.mov_sreg(regs.DS, regs.AX)
p.mov16(regs.SP, 0x7C00)
p.sti()

code_bytes = p.assemble()
```

### Labels and jumps

`Program` resolves labels in two passes, so forward references just work:

```python
p = Program(bits=16)
p.jmp("skip")
p.mov8(regs.AL, 0x41)     # skipped
p.label("skip")
p.label("loop")
p.in_al(0x60)
p.jcc(regs.JZ, "loop")    # jump back if ZF set
```

`jcc` in 16-bit mode uses the short (`rel8`) form and raises `ValueError`
if the target is more than 127 bytes away — restructure the code (e.g.
split into two jumps) if you hit this. In 32-bit mode, `jcc`/`jmp` use the
near (`rel32`) form with effectively unlimited range within the image.

### Instruction reference (what's available today)

**16-bit (real mode):**
`cli, sti, hlt, nop, cld, ret, mov16, mov8, mov_sreg, mov_from_sreg,
mov_rr16, mov_rr8, xor16, push16, pop16, interrupt(imm8), store16, load16,
cmp8, cmp16, test_al, or_al, and_al, in_al, out_al, far_jmp16, lgdt,
mov_eax_cr0, mov_cr0_eax, or_eax, jmp(label), jcc(cc, label)`

`in_al`/`out_al` are available in 32-bit code too (same opcodes, no 32-bit
variant needed) -- but ports numbered 256 or higher (e.g. the VGA DAC at
`0x3C8`/`0x3C9`, used by `graphics.load_palette`) need the DX-register
form (`in_al()`/`out_al()` with no port argument), and **setting DX from
32-bit code must use `mov32(EDX, port)`, not `mov16(DX, port)`** --
`mov16` emits a bare `B8+reg, imm16` with no operand-size prefix, which
inside a 32-bit code segment is read as a 32-bit-immediate `mov r32,
imm32` instead, consuming two extra bytes it was never given and
corrupting whatever comes next in the instruction stream. `mov32` loads
the full register correctly; the CPU's `IN`/`OUT` DX-form instructions
only look at the low 16 bits (DX) of it regardless.

**32-bit (protected mode):**
`mov32, mov_rr32, store32, load32, store8, store8_reg, load8, add32,
sub32, cmp32, inc32, jmp(label), jcc(cc, label), call(label), hlt32,
load8_ind, store8_ind, store8_ind_imm, store8_ind_disp, store8_ind_disp_imm,
add_rr32, sub_rr32, busy_wait(scratch_reg, iterations)`

The `*_ind*` methods are register-indirect addressing (`[reg]` /
`[reg+disp8]`) -- the base register must be `EAX/ECX/EDX/EBX/ESI/EDI`
(not `ESP`/`EBP`, which need a SIB byte this encoder subset doesn't
generate). This is what lets you build runtime data structures like the
`terminal` driver's moving cursor, where an address isn't known until the
program is running -- the other 32-bit memory instructions
(`store32`/`load32`/`store8`/etc.) only support a fixed, compile-time-known
absolute address.

**Raw data (either mode):**
`db(*values), dw(*values), dd(*values), ascii(text, zero_terminate=False),
raw_bytes(data), pad_to(size, fill=0x00)`

`raw_bytes` embeds an arbitrary `bytes` blob as a single node (one bulk
write at assembly time) -- use this instead of `db()` for anything more
than a few dozen bytes (e.g. an embedded image's pixel data), since `db()`
creates one Python object per byte and doesn't scale to tens of thousands
of bytes.

**Addressing embedded data (32-bit):**
`set_base_address(addr)`, `mov32_label(reg, label, base=None)`

A kernel's 32-bit body doesn't know its own load address until `Kernel`
computes it (trampoline + GDT sizes come first) -- `Kernel` calls
`set_base_address` on your `prog` automatically before your entry function
runs, so `prog.mov32_label(reg, label)` gives you the absolute runtime
address of a `label` defined elsewhere in the same program (e.g. pointing
`ESI` at an embedded image's pixel data) without you tracking addresses by
hand. This is what `aetherix.drivers.graphics` uses internally.

**Stack (32-bit):**
`push32(reg)`, `pop32(reg)` -- aliases for `push16`/`pop16`: the opcode
(`0x50+reg`/`0x58+reg`) is identical, and pushes/pops the full 32-bit
register when executed in a 32-bit code segment. The separate names exist
so 32-bit code doesn't read as if it's only handling 16 bits.

If an instruction you need isn't here, see section 15 — adding one to the
native encoder is usually a dozen lines of C plus a `ctypes` signature.

### Generating unique labels from reusable code

If you write a helper function that emits the same instruction pattern
(and thus the same label names) more than once in one program -- e.g. a
function called once per dispatched key in a keyboard loop -- hardcoded
label strings will collide. `Program.unique_label` generates a fresh name
each call:

```python
label = prog.unique_label("my_helper")   # e.g. "__my_helper_7"
prog.label(label)
...
prog.jmp(label)
```

This is exactly how `aetherix.drivers.terminal` avoids label collisions
across repeated `putchar_imm`/`newline`/`backspace` calls.

---

## 11. Registers and condition codes: `regs`

```python
from aetherix import regs

regs.AX, regs.CX, regs.DX, regs.BX, regs.SP, regs.BP, regs.SI, regs.DI   # 16-bit
regs.EAX, regs.ECX, regs.EDX, regs.EBX, regs.ESP, regs.EBP, regs.ESI, regs.EDI  # 32-bit (same numbering)
regs.AL, regs.CL, regs.DL, regs.BL, regs.AH, regs.CH, regs.DH, regs.BH   # 8-bit
regs.ES, regs.CS, regs.SS, regs.DS, regs.FS, regs.GS                       # segment registers

regs.JZ, regs.JE, regs.JNZ, regs.JNE, regs.JC, regs.JB, regs.JNC, regs.JAE,
regs.JBE, regs.JA, regs.JS, regs.JNS, regs.JL, regs.JGE, regs.JLE, regs.JG
```

(16-bit and 32-bit registers share the same numbering — `regs.AX == regs.EAX == 0`
— because which width applies depends entirely on whether you're using a
`Program(bits=16)` or `Program(bits=32)`, not on which constant name you
imported.)

---

## 12. The CLI

```bash
aetherix new MyOS               # scaffold a starter project script in the current directory
aetherix new MyOS -d ./projects  # scaffold it somewhere else
aetherix info                    # list implemented vs. scaffolded hardware
```

`aetherix new MyOS` writes `myos.py` with a working `Project` skeleton
(clear screen, print a message, wait for a keypress) that you can run
immediately with `python myos.py`.

---

## 13. Running what you built

```bash
# QEMU (recommended -- fastest iteration loop)
qemu-system-i386 -drive file=myos.img,format=raw

# VirtualBox: create a VM, attach myos.img as a raw disk (VBoxManage
# convertfromraw can wrap it if VirtualBox's UI insists on a .vdi)
VBoxManage convertfromraw myos.img myos.vdi --format VDI

# VMware: point a virtual machine's disk at myos.img directly (rename
# with a .img extension if VMware's UI is picky about extensions)

# Real hardware / a USB stick
sudo dd if=myos.img of=/dev/sdX bs=4M status=progress conv=fsync
```

---

## 14. Verifying output yourself

Don't take correctness on faith — every layer of Aetherix's own output was
verified this way during development, and you can repeat it on anything
you build:

```bash
# Boot sector (first 512 bytes) -- 16-bit real mode
head -c 512 myos.img > boot.bin
objdump -D -b binary -m i8086 boot.bin

# Kernel (starts at sector 1) -- begins with a 16-bit trampoline
dd if=myos.img of=kernel.bin bs=512 skip=1
head -c 23 kernel.bin > trampoline.bin
objdump -D -b binary -m i8086 trampoline.bin

# The 32-bit kernel body starts 53 bytes into the kernel blob
# (23-byte trampoline + 6-byte GDTR + 24-byte GDT table)
dd if=kernel.bin of=body32.bin bs=1 skip=53
objdump -D -b binary -m i386 body32.bin
```

Look for: the boot sector ending in `55 aa`, the trampoline's `lgdt`/`mov
cr0`/`ljmp` sequence, and your kernel logic (e.g. `movb $0x20,0xb8000` for
a VGA clear, or `in $0x64,%al` for a keyboard poll) in the 32-bit
disassembly.

### Going further: actual execution, not just disassembly

Disassembly proves the bytes are plausible x86 -- it doesn't prove the
boot sequence actually completes (a disk read that silently fails and
retries forever still disassembles perfectly fine). For real confidence,
execute the image under CPU emulation with Unicorn Engine:

```bash
pip install unicorn
python tests/emulate.py myos.img
```

This prints the real-mode boot message, every disk read that occurred
(LBA, sector count, destination address), and the final VGA text buffer
contents -- i.e. what a real screen would actually show. `tests/test_emulation.py`
runs this automatically as a regression test whenever `unicorn` is
installed.

---

## 15. Extending Aetherix: adding a driver or opcode

See `CONTRIBUTING.md` for the full guide. Short version:

- **New opcode**: add the byte-emitting function to `native/encoder.c`,
  register its `ctypes` signature in `aetherix/_native.py`, add a
  `Program` method in `aetherix/asm.py`, and a byte-exact test in
  `tests/test_core.py`.
- **New driver**: write a module like `aetherix/drivers/vga.py` (plain
  functions taking a `Program`), register it with
  `HAL().register("name", module)`, and move it from `SCAFFOLDED` to
  `IMPLEMENTED` in `aetherix/drivers/hal.py` once it's verified.

`CONTRIBUTING.md` lists realistic next drivers in rough order of
tractability: serial port (UART 16550), parallel-port printer, PIT-based
timer/sleep, PCI enumeration (the real prerequisite for GPU/NIC/USB work),
and a real writable filesystem (FAT12 is the traditional starting point).

---

## 16. Common errors and what they mean

| Error | Cause | Fix |
|---|---|---|
| `Boot sector code is N bytes -- must fit in 510 bytes` | Your `BootSector` code is too big | Move logic into the kernel, or a second boot stage |
| `Short jump to 'X' out of range` | A 16-bit `jcc` target is more than 127 bytes away | Restructure with an intermediate `jmp`, or move the code to 32-bit |
| `No kernel entry function registered` | Called `Kernel.assemble()`/`Project.build()` without `@k.entry`/`@proj.kernel_entry` | Register an entry function first |
| `Driver 'X' is not implemented yet in Aetherix` | You accessed a scaffolded HAL slot (e.g. `drivers.usb`) | It's a documented extension point, not a bug — see section 5/15 |
| `Assembled image is N bytes, larger than the requested total size` | Kernel + filesystem content doesn't fit in the chosen `total_size` | Pick a bigger preset/byte count, or shrink content |
| `Could not locate or build the Aetherix native encoder core` | No C compiler found when the native library needed building | Install gcc/clang/MSVC |
| `image at (x,y) size WxH doesn't fit on the 320x200 screen` | `graphics.blit`/`show_image` position + size exceeds the screen | Reduce size or reposition so `x+width<=320` and `y+height<=200` |
| Composited images show visibly wrong colors | Images were prepared independently (`load_image` per image) instead of with `load_images_shared_palette` | Use `imaging.load_images_shared_palette` for any images shown on screen at the same time (section 4a) |
| `power.shutdown` appears to do nothing (halts instead) | Running on real hardware or an emulator other than QEMU/Bochs -- the magic shutdown ports are emulator-specific, not real ACPI | Expected; see the `power` driver table in section 4. Real ACPI shutdown isn't implemented |
| `char_reg must not be EBX/ECX` | Passed EBX or ECX to `terminal.putchar_reg` | Hold the character in a different register (AL is typical) |
| `aetherix.imaging needs Pillow...` | Pillow isn't installed | `pip install Pillow` (a build-time-only dependency; not needed to run the built OS) |
| Screen shows `FATAL: BIOS lacks LBA disk support.` then halts | Booted on a BIOS old enough to lack INT 13h extensions (very rare) | Use `load_kernel_chs` instead, in small enough chunks to stay within one track |

---

## 17. Full worked example

A kernel that clears the screen, prints a message, waits for a keypress,
beeps, and embeds a text file — using every layer covered above at once:

For a fuller, genuinely *interactive* worked example -- a live keyboard-
echoing shell using a runtime cursor, not just fixed messages -- see
`examples/aether_shell.py` and the `terminal` driver in section 4.

```python
from aetherix import Project, regs

with Project("DemoOS", boot_message="DemoOS starting...") as os:

    @os.kernel_entry
    def main(prog, drivers):
        drivers.vga.clear(prog)
        drivers.vga.print_string(prog, "Welcome to DemoOS", row=0, col=0,
                                  attr=drivers.vga.GREEN_ON_BLACK)
        drivers.vga.print_string(prog, "Press any key...", row=2, col=0)

        drivers.keyboard.wait_key_scancode(prog)   # scancode ends up in AL

        drivers.speaker.beep_start(prog, frequency_hz=880)
        # a real kernel would use a timer to time the beep; here we just
        # demonstrate start/stop back-to-back for illustration
        drivers.speaker.beep_stop(prog)

        drivers.vga.print_string(prog, "Key received. Halting.", row=4, col=0,
                                  attr=drivers.vga.YELLOW_ON_BLACK)
        prog.hlt()

    os.add_file("readme.txt", "This file is embedded via AVFS.\n")

    out = os.build("demo_os.img", total_size="floppy_1_44mb")
    print(f"Built {out} ({out.stat().st_size} bytes)")
```

```bash
python demo_os.py
qemu-system-i386 -drive file=demo_os.img,format=raw
```

---

## 18. UEFI support (a separate boot path)

Everything in sections 1-17 is the **BIOS boot path** (`Project`,
`BootSector`, `Kernel`, `aetherix.drivers`): 16-bit real mode, MBR boot
sector, BIOS interrupts. `aetherix.uefi` is a **completely different,
separate subsystem** for UEFI, not an extension of the BIOS path --
firmware loads a PE32+ executable straight into 64-bit long mode, with no
16/32-bit stages, no BIOS interrupts, and no MBR boot sector at all. It
boots from a GPT-partitioned disk with a FAT32 EFI System Partition, not
a flat binary. **You cannot mix the two APIs** -- a `Project`/`Kernel`
build and a `UefiApp` build produce two unrelated kinds of bootable
image; pick one per OS you're building.

**Scope**: this targets UEFI *applications* (use Boot Services, then
return control to firmware -- like a shell command), not a full custom
OS loader (which would mean calling `ExitBootServices`, taking over the
memory map, and setting up your own paging/interrupts -- see
`CONTRIBUTING.md`).

### Quick start

```python
from aetherix.uefi.app import UefiApp
from aetherix.uefi import console, diskimage

app = UefiApp()

@app.entry
def main(prog, sys_table):
    console.clear_screen(prog, sys_table)
    console.println(prog, "Hello from Aetherix UEFI!", sys_table)

efi_path = app.build("build/uefi_hello.efi")
img_path = diskimage.write_disk_image(efi_path.read_bytes(), "build/uefi_hello.img")
```

```bash
qemu-system-x86_64 -bios /path/to/OVMF.fd -drive file=build/uefi_hello.img,format=raw
```

Get `OVMF.fd` from your Linux distro's `ovmf`/`edk2-ovmf` package (Debian/
Ubuntu: `apt install ovmf`, then look under `/usr/share/OVMF/` or
`/usr/share/qemu/OVMF.fd`), or build it from
[tianocore/edk2](https://github.com/tianocore/edk2).

### `UefiApp` — the entry point

```python
app = UefiApp()

@app.entry
def main(prog, sys_table):
    ...
```

`prog` is a `Program(bits=64)` -- see section 10 for the raw 64-bit
instruction methods (`mov64`, `mov64_zx`, `load64`/`store64`,
`lea_rip_label`, `call_r64`, etc). `sys_table` is the register holding
the `EFI_SYSTEM_TABLE*` pointer (`UefiApp` saves it there in its
generated prologue) -- pass it straight through to `console`'s functions.

`UefiApp` generates, around your function: a prologue that follows the
Microsoft x64 calling convention UEFI requires (`RCX`=ImageHandle,
`RDX`=SystemTable at entry -- saved into a callee-saved register before
your code runs, since your own calls will clobber RCX/RDX), and an
epilogue that returns `EFI_SUCCESS` (0). Call `app.no_auto_return()` to
suppress the epilogue if you want to set your own status code or end in
an infinite loop instead.

```python
efi_path = app.build("myapp.efi")   # assembles + wraps in a PE32+ file
code_bytes = app.assemble()          # just the assembled machine code, if you want the PE step separately
```

### `console` — text output and cursor control

```python
console.print_string(prog, "no CRLF added", sys_table)
console.println(prog, "CRLF added automatically", sys_table)
console.clear_screen(prog, sys_table)
console.set_cursor_position(prog, sys_table, column=10, row=5)   # 0-based
console.enable_cursor(prog, sys_table, True)
```

Strings are encoded UCS-2 (UTF-16LE) automatically, as the UEFI spec
requires -- pass a normal Python `str`. Unlike a terminal, UEFI's console
needs an explicit `\r\n` for a new line; `println` adds it, `print_string`
doesn't. `set_cursor_position` needs a 3rd call argument (`Row`), which
goes in R8 per the Microsoft x64 ABI -- this is why the 64-bit encoder
supports the full RAX-R15 register range, not just RAX-RDI.

### `keyboard` — reading key presses

```python
from aetherix import regs

keyboard.read_key(prog, sys_table, scancode_reg=regs.R12, char_reg=regs.R13)
```

Blocks (busy-polls `ConIn->ReadKeyStroke`) until a key is pressed, then
leaves the UEFI scan code in `scancode_reg` (0 for an ordinary character
key -- check this for special keys like arrows/function keys first,
since those have no character) and the UCS-2 character in `char_reg` (0
if the key has none). `scancode_reg`/`char_reg` must not be RAX/RCX/RDX
(used internally) -- R12/R13, RSI/RDI, or RBX all work.

There's no interrupt-driven wait here (that needs `WaitForEvent` via Boot
Services, a natural next contribution -- see `CONTRIBUTING.md`), so like
the BIOS-side keyboard driver, this busy-polls rather than sleeping.

### `protocol` / `boot_services` — the extension mechanism

The built-in helpers above only cover `ConOut`/`ConIn`. For anything
else -- Graphics Output Protocol, Block I/O, Simple File System, or any
UEFI protocol this package doesn't wrap yet -- these two functions are
the general escape hatch, mirroring the BIOS-side `HAL`'s "register your
own driver" philosophy:

```python
from aetherix.uefi import boot_services, protocol
from aetherix import regs

# 1. Find the protocol interface by its GUID (from the UEFI spec or an
#    EDK2 header) -- lands the interface pointer in a register of your
#    choice (not RAX/RCX/RDX/R8, used internally).
boot_services.locate_protocol(prog, sys_table, "9042A9DE-23DC-4A38-96FB-7ADED080516A", regs.R12)
prog.cmp64(regs.EAX, 0)                 # EAX = EFI_STATUS; 0 = found
prog.jcc(regs.JNZ, "not_found_label")   # handle failure -- not every platform has every protocol

# 2. Call any method on it by its byte offset within the protocol struct
#    (also from the spec/header) -- up to 3 more arguments beyond the
#    interface pointer itself (RDX, R8, R9).
protocol.call(prog, regs.R12, 0x30, args=[("imm", 42), ("label", "some_data")])
```

`protocol.call(..., this_arg=True)` (the default) is for protocol
*interfaces* -- COM-like objects where the interface pointer itself is
always the function's first argument, as with `ConOut`/`ConIn`.
`this_arg=False` is for `EFI_BOOT_SERVICES`/`EFI_RUNTIME_SERVICES`
*table members* -- plain functions with no self-pointer, like
`LocateProtocol` itself (which is exactly how `boot_services.py`
implements it, using `protocol.call` internally -- there's nothing
special about the built-in helpers that your own extension code can't
also do).

Each `args` entry is a register number (its current value is used), or
`("imm", value)` for an integer constant, or `("label", name)` for the
RIP-relative address of an embedded string/data label elsewhere in the
same program.

**A register gotcha worth knowing if you write raw 64-bit code by
hand**: `RSP` and `R12` both encode to the same 3-bit ModRM field, which
x86-64 always requires a SIB byte for as a base register, regardless of
addressing mode. `store64`/`load64`/`movzx64_mem16` already handle this
correctly -- but it's exactly the kind of thing that silently corrupts
an instruction stream if you're ever encoding ModRM bytes yourself.

### `diskimage` — GPT + FAT32 bootable image

```python
from aetherix.uefi import diskimage

diskimage.write_disk_image(efi_bytes, "disk.img")                 # ~64MiB default size
diskimage.write_disk_image(efi_bytes, "disk.img", total_size=256*1024*1024)  # explicit size
data = diskimage.build_disk_image(efi_bytes)                        # bytes, if you want to write it yourself
```

The default size is deliberately just over 64MiB -- comfortably past the
65,525-cluster threshold FAT32 needs to be unambiguously recognized as
FAT32 rather than FAT16 (see `aetherix.uefi.fat32`'s module docstring).
Only one file is placed on the ESP: `\EFI\BOOT\BOOTX64.EFI` (the
spec-defined path for removable-media boot, which is what QEMU/OVMF and
most real firmware look for on a USB stick or disk with no boot entry
already registered).

### Writing to a real USB stick

```bash
sudo dd if=build/uefi_hello.img of=/dev/sdX bs=4M status=progress conv=fsync
```

**Double- and triple-check `/dev/sdX`** -- this overwrites the entire
device with no confirmation prompt (same caveat as the BIOS path's disk
images -- see section 7). Boot from it on any UEFI PC with Secure Boot
disabled (this produces an unsigned application).

### Verifying UEFI output yourself

Disassemble the actual machine code (64-bit, Intel syntax is usually
easier to read for long mode):

```bash
objdump -d -M intel your.efi
```

Confirm the file is a real, recognized PE32+ EFI application:

```bash
file your.efi   # -> "PE32+ executable (EFI application) x86-64, for MS Windows"
```

Validate the GPT and FAT32 independently (not just by this codebase's own
logic checking itself) -- install `gdisk` and `mtools`:

```bash
gdisk -l disk.img          # partition table info; open it interactively and run 'v' to verify CRCs
mdir -i "disk.img@@17408" -/ ::/           # list the ESP's contents (17408 = LBA 34 in bytes)
mcopy -i "disk.img@@17408" ::/EFI/BOOT/BOOTX64.EFI extracted.efi
diff extracted.efi your.efi                 # should be identical
```

Or execute it for real, without needing QEMU/OVMF installed, via a
Unicorn (`UC_MODE_64`) harness that simulates enough of
`EFI_SYSTEM_TABLE`/`ConOut`/`ConIn`/`BootServices` to prove the protocol
calls actually happen correctly:

```bash
pip install unicorn
python tests/emulate_uefi.py your.efi
```

This prints every `ConOut`/`ConIn`/`BootServices` call your app actually
made -- string arguments decoded, cursor positions, keys "pressed" -- and
the final `EFI_STATUS` your app returned in RAX. Pass `key_sequence=`/
`protocols=` (see the function's docstring) to script keypresses or fake
protocol lookups for your own app's test.

### Instruction reference (64-bit / long mode)

`push64, pop64, ret64, nop64, hlt64, mov64(reg,imm), mov64_zx(reg,imm),
mov_rr64(dst,src), store64(base,disp,src), load64(dst,base,disp),
movzx64_mem16(dst,base,disp), lea_rip_label(dst,label), call_r64(reg),
add64(reg,imm), sub64(reg,imm), cmp64(reg,imm), cmp_rr64(a,b),
xor_rr64(dst,src), jmp(label), jcc(cc,label), call(label)`

Supports the full register range RAX-R15 (`regs.EAX` through `regs.EDI`
for RAX-RDI, plus `regs.R8`-`regs.R15`) -- registers 8-15 need extra REX
prefix bits (REX.R/B) the encoder adds automatically; you don't need to
think about this when calling these methods, just pass whichever
register number you want. Memory operands (`store64`/`load64`/
`movzx64_mem16`) always encode an explicit displacement (even when it's
0), which sidesteps the special-case encoding RBP/R13 need as a base
register in the "no-displacement" addressing form, and always emit the
mandatory SIB byte RSP/R12 need as a base register (both quirks are
handled for you -- any of the 16 registers work uniformly as a base).

**Position independence is load-bearing, not optional**: only ever use
`lea_rip_label` to get the address of your own embedded data, and
`jmp`/`jcc`/`call` (which are RIP-relative by construction) for control
flow to your own labels. Never use `mov64` (movabs) with an absolute
address pointing at your own code or data -- `aetherix.uefi.pe` doesn't
generate a `.reloc` section, on the premise that code built this way
never needs one. If you break that premise, your app may crash or
misbehave depending on where firmware happens to load it.
