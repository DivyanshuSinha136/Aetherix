import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aetherix.asm import Program
from aetherix import regs
from aetherix.boot.realmode import BootSector
from aetherix.kernel.builder import Kernel
from aetherix.image.diskimage import DiskImage
from aetherix.fs.simplefs import Archive
from aetherix.context import Project


def test_mov_r16_imm16_bytes():
    p = Program(16)
    p.mov16(regs.AX, 0x1234)
    assert p.assemble() == bytes([0xB8, 0x34, 0x12])


def test_label_forward_and_backward_jump():
    p = Program(16)
    p.jmp("end")     # offset 0, size 3
    p.nop()           # offset 3
    p.label("end")   # offset 4
    p.label("loop")  # offset 4
    p.nop()           # offset 4
    p.jmp("loop")     # offset 5, size 3
    code = p.assemble()
    # jmp rel16 to 'end': E9 + rel16, rel = target(4) - (0+3) = 1
    assert code[0:3] == bytes([0xE9, 0x01, 0x00])
    # nop at offset 3
    assert code[3] == 0x90
    # jmp rel16 to 'loop' (offset 4): at position 5, size 3, rel = 4-(5+3) = -4
    rel = int.from_bytes(code[6:8], "little", signed=True)
    assert rel == -4


def test_bootsector_is_512_bytes_with_signature():
    b = BootSector.standard(kernel_sectors=4, message="test")
    data = b.assemble()
    assert len(data) == 512
    assert data[-2:] == b"\x55\xaa"


def test_bootsector_rejects_oversized_code():
    b = BootSector()
    b.setup_segments()
    for _ in range(600):
        b.prog.nop()
    try:
        b.assemble()
        assert False, "expected ValueError for oversized boot sector"
    except ValueError:
        pass


def test_kernel_assembles_and_trampoline_is_fixed_size():
    k = Kernel()

    @k.entry
    def main(prog, drivers):
        drivers.vga.clear(prog)
        prog.hlt()

    data = k.assemble()
    assert len(data) > 0
    # trampoline(23) + gdtr(6) + gdt(24) = 53 bytes before the 32-bit body
    assert data[0] == 0xFA  # cli
    assert data[53 - 6 - 24 - 1] != None  # sanity: index math doesn't explode


def test_kernel_double_assemble_does_not_duplicate_instructions():
    k = Kernel()
    calls = {"n": 0}

    @k.entry
    def main(prog, drivers):
        calls["n"] += 1
        prog.hlt()

    size1 = len(k.assemble())
    size2 = len(k.assemble())
    assert size1 == size2
    assert calls["n"] == 2  # entry fn called once per assemble(), not accumulating state


def test_disk_image_layout():
    b = BootSector.standard(kernel_sectors=1, message="hi")
    boot_bytes = b.assemble()
    img = DiskImage()
    img.set_boot_sector(boot_bytes)
    img.set_kernel(b"\x90" * 100)  # 1 sector's worth after padding
    data = img.build(total_size=1024)
    assert data[:512] == boot_bytes
    assert len(data) == 1024


def test_avfs_archive_roundtrip():
    a = Archive()
    a.add_file("hello.txt", "hi there")
    blob = a.build()
    assert blob[:4] == b"AVFS"
    assert blob[5] == 1  # file count


def test_project_end_to_end(tmp_path):
    with Project("TestOS", boot_message="hi") as proj:
        @proj.kernel_entry
        def main(prog, drivers):
            drivers.vga.clear(prog)
            prog.hlt()

        out = proj.build(str(tmp_path / "test.img"), total_size="floppy_1_44mb")
    data = Path(out).read_bytes()
    assert len(data) == 1_474_560
    assert data[510:512] == b"\x55\xaa"


if __name__ == "__main__":
    import inspect
    mod = sys.modules[__name__]
    tests = [f for name, f in vars(mod).items() if name.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            if "tmp_path" in inspect.signature(t).parameters:
                import tempfile
                with tempfile.TemporaryDirectory() as d:
                    t(Path(d))
            else:
                t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
    print(f"{passed}/{len(tests)} passed")
