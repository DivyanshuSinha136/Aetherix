# Contributing to Aetherix

The most valuable contributions right now are new drivers and new
instruction-encoder opcodes. Both follow the same pattern already used by
`aetherix/drivers/vga.py`, `keyboard.py`, and `speaker.py`.

## Adding an instruction to the native encoder

1. Add the byte-emitting function to `native/encoder.c` (see the existing
   functions for the pattern: a plain C function taking `enc_buf_t *`, plus
   any operands, appending fixed-width bytes).
2. Register its `ctypes` signature in `aetherix/_native.py`'s `_FUNC_SIGS`.
3. Add a corresponding method on `Program` in `aetherix/asm.py`.
4. Add a test in `tests/test_core.py` asserting the exact byte sequence
   (compare against a known-good encoding, e.g. from the Intel SDM or by
   cross-checking with `objdump`/a reference assembler in a scratch
   environment — never merge an opcode you haven't verified byte-for-byte).

## Adding a driver

Drivers are just Python modules with functions that take a `Program` (and
usually append instructions to it) — see `aetherix/drivers/vga.py` for the
shape. To go from "scaffolded" to "implemented" in `aetherix/drivers/hal.py`:

1. Write the driver module under `aetherix/drivers/`.
2. Register it: `HAL().register("usb", my_usb_driver_module)` (or wire it
   into `HAL.__init__` directly once it's solid).
3. Move its name from the `SCAFFOLDED` list to `IMPLEMENTED` in the
   `hal.py` module docstring, and add a short design note (what protocol/
   spec it implements, what it deliberately doesn't handle yet).
4. Disassemble a real build that uses it and confirm the byte sequence
   does what you intend — see the README's "Verifying the output
   yourself" section.

Realistic next contributions, roughly in order of tractability:
- **A dedicated Graphics Output Protocol (GOP) wrapper** --
  `boot_services.locate_protocol` + `protocol.call` (the generic
  extension mechanism) already let you drive GOP yourself today (its
  GUID and `QueryMode`/`SetMode`/frame buffer info are all in the UEFI
  spec), but a `aetherix.uefi.graphics` module wrapping that -- a real
  linear framebuffer, unlike BIOS Mode 13h's fixed 320x200 -- would be a
  natural, genuinely useful dedicated helper on top of it.
- **`EFI_BLOCK_IO_PROTOCOL`/`EFI_SIMPLE_FILE_SYSTEM_PROTOCOL` wrappers**
  -- reading other files from the ESP or other volumes at runtime.
  Same story: reachable today via `locate_protocol`/`protocol.call`
  directly, worth a dedicated wrapper once something needs it often
  enough to justify one.
- **Event-driven keyboard wait** -- `keyboard.read_key` busy-polls
  `ReadKeyStroke`; a real wait would call Boot Services' `WaitForEvent`
  on `ConIn->WaitForKey` instead, avoiding a spin loop. `protocol.call`
  with `this_arg=False` already has what's needed to build this.
- **VFAT long filenames in `aetherix.uefi.fat32`** -- only 8.3 short
  names are supported today (fine for `EFI`/`BOOT`/`BOOTX64.EFI`, all of
  which happen to fit); anything else needs VFAT long-filename directory
  entries, a different (more involved) entry format layered on top of
  the short-name entries.
- **A full custom UEFI OS loader** -- calling `ExitBootServices`,
  parsing the memory map UEFI hands you, and setting up your own
  GDT/IDT/paging from scratch, the way `aetherix.kernel` does for BIOS.
  A UEFI application that never exits boot services (what `UefiApp`
  builds today) is a much smaller, more tractable target than this.
- **ACPI table parsing** -- UEFI actually makes this easier to *start*
  than BIOS does: the RSDP is handed to you directly via
  `EFI_SYSTEM_TABLE->ConfigurationTable` (a GUID-indexed array), no BIOS
  memory scanning needed. The real work (walking RSDT/XSDT -> FADT,
  interpreting the DSDT's AML for `power.shutdown`-equivalent real ACPI
  soft-off) is the same undertaking described below for the BIOS path.
- **Extended scancodes for the keyboard driver** (arrows, Insert/Delete/
  Home/End/Page Up/Down, right Ctrl/Alt) -- Shift-aware typing and a full
  Set 1 punctuation table are done (`aetherix.drivers.keyboard`); these
  keys send a `0xE0` prefix byte before their code, which
  `wait_key_scancode`/`read_char` don't watch for yet. Caps Lock (toggle
  state, not press/hold like Shift) is a similarly small addition.
- **ACPI table parsing** for real `power.shutdown`/sleep -- the current
  `shutdown` only works in QEMU/Bochs (a well-known but emulator-specific
  magic port); real ACPI soft-off needs walking RSDP -> RSDT/XSDT -> FADT
  to find the PM1 control port, and interpreting the DSDT's `\_S5` AML
  object for the actual sleep-type value. A minimal read-only AML parser
  for just that one object is a bounded, realistic first step; a general
  AML interpreter is a much bigger undertaking.
- **GPU beyond Mode 13h** (linear framebuffer via VBE/VESA or GOP, higher
  resolutions/color depths) — Mode 13h graphics (`aetherix.drivers.graphics`)
  is done; a real `gpu` driver is still a scaffolded HAL slot. VBE mode
  info/set (`int 10h, ax=4F01h/4F02h`) is the next realistic step; a real
  GPU driver (mode-setting beyond BIOS/VBE, acceleration) needs a PCI
  driver underneath it first.
- **A real PIT timer (channel 0 + its IRQ)** — `Program.busy_wait` is a
  CPU-cycle-burning delay loop, not an actual timer; a proper one needs
  IDT setup to handle the timer interrupt, which nothing in this codebase
  does yet. This is also the real prerequisite for a genuine ACPI sleep
  (interrupt-driven wake) rather than `power.sleep_until_keypress`'s
  busy-poll approximation.
- **Serial port (UART 16550)** — trivial I/O-port driver, useful for kernel
  debug logging before you trust VGA output.
- **Parallel port printer** — raw byte output to port 0x378, no protocol.
- **PCI enumeration** — the real prerequisite for GPU/NIC/USB work; a
  config-space scanner (ports 0xCF8/0xCFC) is self-contained and doesn't
  need a full driver behind it to be useful on its own.
- **A real writable filesystem** (vs. AVFS's build-time archive) — this is
  a substantial project on its own (FAT12 is the traditional starting
  point for hobby OS dev because BIOS-era floppies used it).

## Code style

- Every native encoder function must be a fixed-width emission for a given
  set of argument *types* (not values) — the two-pass label resolver in
  `asm.py` depends on instruction size being independent of operand
  values. If you need a variable-width encoding, it needs a different
  resolution strategy; open an issue before writing one.
- Prefer small, testable driver functions over large monolithic ones.
- Document what real hardware/protocol behavior you're relying on, and
  what you're deliberately not handling yet — see the comments in
  `keyboard.py`/`speaker.py` for the tone to match.
- If you add a new ModRM-based addressing instruction (in either the
  32-bit or 64-bit encoder), remember: **RSP/R12 both encode to rm=100**,
  which x86 always requires a SIB byte for as a base register regardless
  of addressing mode, and **RBP/R13 both encode to rm=101**, which means
  "no base, disp32 only" under mod=00 specifically. `emit_modrm_disp` in
  `native/encoder.c` already handles both cases for the functions that
  use it — reuse it rather than hand-rolling ModRM bytes, and if you do
  need something it doesn't cover, test explicitly with a base register
  from each of the four register-number "families" (a plain register,
  RSP/R12, RBP/R13, and one more extended register like R8) before
  merging. This exact bug (R12 silently corrupting the instruction
  stream) shipped once already during this project's own development —
  see the README changelog.
