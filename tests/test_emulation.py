"""
End-to-end emulation test: actually executes a built image under Unicorn
Engine and checks what ends up on the (emulated) screen. This is the test
that would have caught the original CHS-disk-read/track-boundary bug --
disassembly alone only proves the bytes are *plausible* x86, not that the
boot sequence actually completes.

Skips gracefully if `unicorn` isn't installed (it's a test-only dependency,
not a runtime dependency of aetherix itself): `pip install unicorn`.
"""
import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import unicorn  # noqa: F401
    HAVE_UNICORN = True
except ImportError:
    HAVE_UNICORN = False

from aetherix import Project


def _build_test_image(tmp_path) -> Path:
    with Project("EmuTestOS", boot_message="EmuTestOS starting...") as proj:
        @proj.kernel_entry
        def main(prog, drivers):
            drivers.vga.clear(prog)
            drivers.vga.print_string(prog, "EMULATION TEST OK", row=0, col=0)
            prog.hlt()

        return proj.build(str(tmp_path / "emutest.img"))


def test_image_boots_and_kernel_runs_under_emulation(tmp_path):
    if not HAVE_UNICORN:
        print("SKIP (unicorn not installed)")
        return
    from emulate import run_image

    img_path = _build_test_image(Path(tmp_path))
    result = run_image(str(img_path))

    assert "EmuTestOS starting..." in result["boot_message"], (
        "Real-mode boot message never printed -- bootloader didn't even start"
    )
    assert result["disk_reads"], (
        "No disk reads observed -- kernel was never loaded from disk"
    )
    lba, count, dest = result["disk_reads"][0]
    assert lba == 1, f"expected kernel load to start at LBA 1, got {lba}"
    assert dest == 0x7E00, f"expected kernel load destination 0x7E00, got {hex(dest)}"

    vga_text = "\n".join(result["vga_rows"])
    assert "EMULATION TEST OK" in vga_text, (
        "Kernel never reached its VGA output -- protected-mode transition "
        f"or kernel body failed. Full VGA buffer: {result['vga_rows']!r}"
    )
    print("PASS test_image_boots_and_kernel_runs_under_emulation")


def test_interactive_shell_echoes_keystrokes_under_emulation():
    if not HAVE_UNICORN:
        print("SKIP (unicorn not installed)")
        return
    from emulate import run_image
    from aetherix.drivers import keyboard as kb

    img_path = Path(__file__).resolve().parent.parent / "build" / "aether_shell.img"
    if not img_path.exists():
        print("SKIP (build/aether_shell.img not built -- run examples/aether_shell.py first)")
        return

    # h, i, space, 2, 0, 2, 6, backspace, escape
    keys = [0x23, 0x17, 0x39, 0x03, 0x0B, 0x03, 0x06, 0x0E, 0x01]
    result = run_image(str(img_path), key_sequence=keys, max_instructions=20_000_000)

    vga_text = "\n".join(result["vga_rows"])
    assert "hi 202" in vga_text, (
        f"Expected typed+backspaced text 'hi 202' on screen, got: {result['vga_rows']!r}"
    )
    assert "Goodbye! System halted." in vga_text, (
        f"Escape key never reached the halt path. VGA buffer: {result['vga_rows']!r}"
    )

    # Shift+h, i, Shift+1(!), Enter, F2 (reboot) -- exercises Shift state
    # and the power control keys in the shell's own inline dispatch (not
    # just keyboard.read_char's internal copy of the same logic).
    keys2 = [0x2A, 0x23, 0xAA, 0x17, 0x2A, 0x02, 0xAA, kb.SCANCODE_ENTER, kb.SCANCODE_F2]
    result2 = run_image(str(img_path), key_sequence=keys2, max_instructions=20_000_000)
    vga_text2 = "\n".join(result2["vga_rows"])
    assert "Hi!" in vga_text2, (
        f"Expected Shift-produced 'Hi!' on screen, got: {result2['vga_rows']!r}"
    )
    assert result2["reboot_requested"], "F2 never triggered a reboot in the shell's own dispatch"

    print("PASS test_interactive_shell_echoes_keystrokes_under_emulation")


def test_graphics_composite_renders_correctly_under_emulation():
    if not HAVE_UNICORN:
        print("SKIP (unicorn not installed)")
        return
    try:
        from PIL import Image
    except ImportError:
        print("SKIP (Pillow not installed)")
        return
    from emulate import run_image
    from aetherix import imaging
    from aetherix.drivers import graphics

    tmp = Path(tempfile.mkdtemp())
    bg_path = tmp / "bg.png"
    logo_path = tmp / "logo.png"

    bg_img = Image.new("RGB", (320, 200))
    px = bg_img.load()
    for y in range(200):
        for x in range(320):
            px[x, y] = (x % 256, y % 256, (x * y) % 256)
    bg_img.save(bg_path)

    logo_img = Image.new("RGB", (32, 32), (200, 30, 30))
    logo_img.save(logo_path)

    background, logo = imaging.load_images_shared_palette([
        (str(bg_path), 320, 200, True),
        (str(logo_path), 32, 32, False),
    ])
    assert background.palette_bytes == logo.palette_bytes, (
        "load_images_shared_palette produced different palettes -- "
        "compositing these on screen would show one image with wrong colors"
    )

    with Project("GfxCompositeTest", graphics_mode=graphics.MODE_13H) as proj:
        @proj.kernel_entry
        def main(prog, drivers):
            drivers.graphics.show_image(prog, background, x=0, y=0)
            drivers.graphics.show_image(prog, logo, x=10, y=10)
            prog.hlt()

        img_path = proj.build(str(tmp / "gfxtest.img"))

    result = run_image(str(img_path), max_instructions=200_000_000)

    fb = result["gfx_framebuffer"]
    pal = result["dac_palette"]

    assert pal == background.palette_bytes, (
        "DAC palette after boot doesn't match the (shared) prepared palette"
    )

    # A background row far from the logo should be untouched.
    row_far = fb[190 * 320: 190 * 320 + 320]
    assert row_far == background.pixel_bytes[190 * 320: 190 * 320 + 320], (
        "Background pixels don't match the source image away from the logo"
    )

    # The logo region should match the logo's own pixel data exactly.
    logo_row0_offset = 10 * 320 + 10
    assert fb[logo_row0_offset: logo_row0_offset + 32] == logo.pixel_bytes[0:32], (
        "Logo pixels don't match at the composited position"
    )

    print("PASS test_graphics_composite_renders_correctly_under_emulation")


def test_multi_chunk_disk_load_under_emulation(tmp_path):
    """A kernel over ~65 sectors needs more than one LBA read call (see
    BootSector.load_kernel_lba's docstring) -- this is what would have
    caught two real bugs found during development: a single oversized
    read silently overflowing a 16-bit segment offset, and a later fix
    that accidentally used DX (whose low byte is DL, the boot drive
    number) as a scratch register, corrupting every chunk after the
    first. Padding a kernel well past 64 sectors with inert data keeps
    this test fast (no heavy rendering/delay loops) while still
    genuinely exercising the multi-chunk path."""
    if not HAVE_UNICORN:
        print("SKIP (unicorn not installed)")
        return
    from emulate import run_image

    with Project("BigKernelTest") as proj:
        @proj.kernel_entry
        def main(prog, drivers):
            drivers.vga.clear(prog)
            drivers.vga.print_string(prog, "BIG KERNEL LOADED OK", row=0, col=0)
            prog.raw_bytes(bytes([0x90]) * 100_000)  # pad well past 64 sectors
            prog.hlt()

        img_path = proj.build(str(Path(tmp_path) / "bigkernel.img"))
        expected_sectors = (len(proj.kernel.assemble()) + 511) // 512

    assert expected_sectors > 64, (
        f"test kernel is only {expected_sectors} sectors -- needs to exceed 64 "
        "to actually exercise the multi-chunk path"
    )

    result = run_image(str(img_path), max_instructions=5_000_000)

    total_loaded = sum(count for _lba, count, _dest in result["disk_reads"])
    assert len(result["disk_reads"]) > 1, (
        f"expected multiple chunked reads for a {expected_sectors}-sector kernel, "
        f"got only {result['disk_reads']}"
    )
    assert total_loaded == expected_sectors, (
        f"chunked reads totaled {total_loaded} sectors, expected {expected_sectors}: "
        f"{result['disk_reads']}"
    )

    vga_text = "\n".join(result["vga_rows"])
    assert "BIG KERNEL LOADED OK" in vga_text, (
        f"kernel body never ran after the multi-chunk load. VGA buffer: {result['vga_rows']!r}"
    )
    print("PASS test_multi_chunk_disk_load_under_emulation")


def test_shift_aware_read_char_under_emulation(tmp_path):
    if not HAVE_UNICORN:
        print("SKIP (unicorn not installed)")
        return
    from emulate import run_image
    from aetherix import regs
    from aetherix.drivers import keyboard

    with Project("ReadCharTest") as proj:
        @proj.kernel_entry
        def main(prog, drivers):
            drivers.keyboard.init(prog)
            drivers.vga.clear(prog)
            drivers.keyboard.read_char(prog)   # shift+a -> 'A'
            drivers.vga.print_at_runtime(prog, row=5, col=0, char_reg=regs.AL)
            drivers.keyboard.read_char(prog)   # b (no shift) -> 'b'
            drivers.vga.print_at_runtime(prog, row=5, col=1, char_reg=regs.AL)
            drivers.keyboard.read_char(prog)   # shift+1 -> '!'
            drivers.vga.print_at_runtime(prog, row=5, col=2, char_reg=regs.AL)
            prog.hlt()

        img_path = proj.build(str(Path(tmp_path) / "readchartest.img"))

    keys = [0x2A, 0x1E, 0xAA, 0x30, 0x2A, 0x02]  # shift-on,a, shift-off,b, shift-on,1
    result = run_image(str(img_path), key_sequence=keys, max_instructions=5_000_000)

    assert result["vga_rows"][5] == "Ab!", (
        f"expected 'Ab!' (shift+a, b, shift+1), got {result['vga_rows'][5]!r} -- "
        "Shift state tracking or the shifted-symbol table is wrong"
    )
    print("PASS test_shift_aware_read_char_under_emulation")


def test_power_control_under_emulation(tmp_path):
    if not HAVE_UNICORN:
        print("SKIP (unicorn not installed)")
        return
    from emulate import run_image
    from aetherix import regs
    from aetherix.drivers import keyboard

    with Project("PowerTest") as proj:
        @proj.kernel_entry
        def main(prog, drivers):
            drivers.keyboard.init(prog)
            drivers.vga.clear(prog)
            drivers.keyboard.wait_key_scancode(prog)
            prog.cmp8(regs.AL, keyboard.SCANCODE_F2)
            prog.jcc(regs.JZ, "do_reboot")
            prog.cmp8(regs.AL, keyboard.SCANCODE_F3)
            prog.jcc(regs.JZ, "do_shutdown")
            drivers.power.restart(prog)   # anything else: restart (jump to top)

            prog.label("do_reboot")
            drivers.power.reboot(prog)

            prog.label("do_shutdown")
            drivers.power.shutdown(prog)

        img_path = proj.build(str(Path(tmp_path) / "powertest.img"))

    # First key doesn't match F2/F3 -> restart loops back to the top, where
    # the second scripted key (F2) is then read and triggers reboot. This
    # proves restart's jump-back actually re-enters the kernel's start.
    result = run_image(str(img_path), key_sequence=[0x1E, keyboard.SCANCODE_F2],
                        max_instructions=5_000_000)
    assert result["reboot_requested"], (
        "reboot was never triggered -- either restart's jump-back or the "
        "keyboard-controller reset pulse isn't working"
    )
    assert not result["shutdown_requested"]

    # Separately confirm the shutdown magic-port write happens too.
    with Project("PowerTest2") as proj2:
        @proj2.kernel_entry
        def main2(prog, drivers):
            drivers.keyboard.init(prog)
            drivers.keyboard.wait_key_scancode(prog)
            prog.cmp8(regs.AL, keyboard.SCANCODE_F3)
            prog.jcc(regs.JZ, "do_shutdown")
            prog.hlt()
            prog.label("do_shutdown")
            drivers.power.shutdown(prog)

        img_path2 = proj2.build(str(Path(tmp_path) / "powertest2.img"))

    result2 = run_image(str(img_path2), key_sequence=[keyboard.SCANCODE_F3],
                         max_instructions=1_000_000)
    assert result2["shutdown_requested"], "QEMU shutdown magic-port write never happened"

    print("PASS test_power_control_under_emulation")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        test_image_boots_and_kernel_runs_under_emulation(Path(d))
    test_interactive_shell_echoes_keystrokes_under_emulation()
    test_graphics_composite_renders_correctly_under_emulation()
    with tempfile.TemporaryDirectory() as d:
        test_multi_chunk_disk_load_under_emulation(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_shift_aware_read_char_under_emulation(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_power_control_under_emulation(Path(d))
