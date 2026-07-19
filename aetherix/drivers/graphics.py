"""
VGA graphics driver -- Mode 13h (320x200, 256-color, linear framebuffer).

This is *legacy VGA graphics*, not a general GPU driver (see
`aetherix.drivers.hal` -- `gpu` remains a scaffolded/not-implemented slot
for modern framebuffer/GOP/VBE work). Mode 13h is what every hobby OS
tutorial reaches for first because it needs nothing beyond a single BIOS
call to set up: a fixed 320x200 byte-per-pixel framebuffer at physical
address 0xA0000, with pixel values indexing a 256-entry color palette you
program yourself through the VGA DAC (ports 0x3C8/0x3C9).

To use this, build your `Kernel`/`Project` with `graphics_mode=MODE_13H`
-- the mode-set BIOS call has to happen in real mode, before the
protected-mode switch, so it lives in the kernel's 16-bit trampoline (see
`aetherix.kernel.builder`), not in your 32-bit kernel-entry function.
Once in Mode 13h there is no text-mode VGA output anymore (`vga`/`terminal`
won't work) until you switch back with a mode-set call of your own.

Use `aetherix.imaging.load_image` to prepare a source image (PNG/JPG/etc,
any size) into an `ImageAsset` (resized, quantized to <=256 colors, and
ready to embed) before passing it to `show_image`.
"""
from __future__ import annotations

from .. import regs
from ..asm import Program

MODE_13H = 0x13
GFX_WIDTH = 320
GFX_HEIGHT = 200
GFX_BASE = 0xA0000

_VGA_DAC_INDEX_PORT = 0x3C8
_VGA_DAC_DATA_PORT = 0x3C9


def load_palette(prog: Program, palette_label: str, entry_count: int = 256) -> Program:
    """Program the VGA DAC with a palette (`entry_count * 3` bytes: R,G,B
    per entry, each 0-63 -- VGA DAC precision is 6 bits per channel, *not*
    8-bit like modern displays; `aetherix.imaging` already scales for
    this). `palette_label` must reference a data blob of exactly that many
    bytes placed elsewhere in the same program (e.g. via
    `prog.raw_bytes(asset.palette_bytes)` under a label).

    Clobbers EAX, EDX, ESI, ECX.
    """
    loop_label = prog.unique_label("pal_loop")

    prog.mov32(regs.EDX, _VGA_DAC_INDEX_PORT)
    prog.mov8(regs.AL, 0x00)
    prog.out_al()  # select DAC write starting at palette index 0

    prog.mov32(regs.EDX, _VGA_DAC_DATA_PORT)
    prog.mov32_label(regs.ESI, palette_label)
    prog.mov32(regs.ECX, entry_count * 3)

    prog.label(loop_label)
    prog.load8_ind(regs.AL, regs.ESI)
    prog.out_al()  # DAC index auto-increments every 3 bytes written
    prog.inc32(regs.ESI)
    prog.sub32(regs.ECX, 1)
    prog.cmp32(regs.ECX, 0)
    prog.jcc(regs.JNZ, loop_label)
    return prog


def blit(prog: Program, pixel_label: str, width: int, height: int,
         x: int = 0, y: int = 0) -> Program:
    """Copy a `width` x `height` block of pixel-index bytes from a data
    blob (referenced by `pixel_label`, placed elsewhere in the same
    program) to the Mode 13h framebuffer at screen position (x, y).
    `x + width` must be <= 320 and `y + height` must be <= 200.

    Clobbers EAX, EBX, ECX, EDX, ESI, EDI.
    """
    if x + width > GFX_WIDTH or y + height > GFX_HEIGHT:
        raise ValueError(
            f"image at ({x},{y}) size {width}x{height} doesn't fit on the "
            f"{GFX_WIDTH}x{GFX_HEIGHT} screen"
        )

    row_loop = prog.unique_label("blit_row")
    col_loop = prog.unique_label("blit_col")
    row_done = prog.unique_label("blit_row_done")
    all_done = prog.unique_label("blit_done")

    prog.mov32_label(regs.ESI, pixel_label)
    prog.mov32(regs.EDI, GFX_BASE + y * GFX_WIDTH + x)
    prog.mov32(regs.EDX, height)

    prog.label(row_loop)
    prog.cmp32(regs.EDX, 0)
    prog.jcc(regs.JZ, all_done)

    prog.mov32(regs.EBX, width)
    prog.label(col_loop)
    prog.cmp32(regs.EBX, 0)
    prog.jcc(regs.JZ, row_done)
    prog.load8_ind(regs.AL, regs.ESI)
    prog.store8_ind(regs.EDI, regs.AL)
    prog.inc32(regs.ESI)
    prog.inc32(regs.EDI)
    prog.sub32(regs.EBX, 1)
    prog.jmp(col_loop)

    prog.label(row_done)
    if GFX_WIDTH - width:
        prog.add32(regs.EDI, GFX_WIDTH - width)  # skip to next row start
    prog.sub32(regs.EDX, 1)
    prog.jmp(row_loop)

    prog.label(all_done)
    return prog


def show_image(prog: Program, image, x: int = 0, y: int = 0) -> Program:
    """Embed an `ImageAsset` (see `aetherix.imaging.load_image`) inline in
    `prog` and display it: uploads its palette, then blits its pixels at
    screen position (x, y). This is the one-call convenience most people
    want; `load_palette`/`blit` are available separately if you're
    managing several images and want to upload a shared palette once."""
    pal_label = prog.unique_label("img_palette")
    pix_label = prog.unique_label("img_pixels")
    skip_label = prog.unique_label("img_data_skip")

    load_palette(prog, pal_label, entry_count=len(image.palette_bytes) // 3)
    blit(prog, pix_label, image.width, image.height, x=x, y=y)

    prog.jmp(skip_label)
    prog.label(pal_label)
    prog.raw_bytes(image.palette_bytes)
    prog.label(pix_label)
    prog.raw_bytes(image.pixel_bytes)
    prog.label(skip_label)
    return prog


def play_frames(prog: Program, frames, delay_iterations: int = 2_000_000,
                 x: int = 0, y: int = 0, loop: bool = True) -> Program:
    """A simple boot-animation / "video": show each `ImageAsset` in
    `frames` in sequence, holding each for `delay_iterations` busy-wait
    cycles (see `Program.busy_wait` -- there's no timer instruction, so
    this is a CPU-cycle delay, not a calibrated frame rate), looping
    forever if `loop` is True. All frames must be the same size.

    This does not return -- if `loop` is True, it ends in an infinite
    loop (use `Kernel.no_auto_halt()`); if False, control falls through
    to whatever comes after (e.g. your own halt loop) once every frame has
    shown once.
    """
    if not frames:
        raise ValueError("play_frames needs at least one frame")

    pal_labels = [prog.unique_label("vid_palette") for _ in frames]
    pix_labels = [prog.unique_label("vid_pixels") for _ in frames]
    data_skip_label = prog.unique_label("vid_data_skip")
    loop_top_label = prog.unique_label("vid_loop_top")

    if loop:
        prog.label(loop_top_label)
    for frame, pal_label, pix_label in zip(frames, pal_labels, pix_labels):
        load_palette(prog, pal_label, entry_count=len(frame.palette_bytes) // 3)
        blit(prog, pix_label, frame.width, frame.height, x=x, y=y)
        prog.busy_wait(regs.EBX, delay_iterations)
    if loop:
        prog.jmp(loop_top_label)

    prog.jmp(data_skip_label)
    for frame, pal_label, pix_label in zip(frames, pal_labels, pix_labels):
        prog.label(pal_label)
        prog.raw_bytes(frame.palette_bytes)
        prog.label(pix_label)
        prog.raw_bytes(frame.pixel_bytes)
    prog.label(data_skip_label)
    return prog
