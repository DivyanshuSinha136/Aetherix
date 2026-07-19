"""
UEFI subsystem tests: PE32+ structure, real x86-64 execution (Unicorn),
and -- when available -- independent validation of the GPT/FAT32 disk
image via gdisk/fsck.vfat/mtools (real, external tools; these checks are
skipped gracefully if the tools aren't installed, same pattern as the
BIOS-side tests skip gracefully without Unicorn).
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import unicorn  # noqa: F401
    HAVE_UNICORN = True
except ImportError:
    HAVE_UNICORN = False


def _build_hello_efi(tmp_path) -> Path:
    from aetherix.uefi.app import UefiApp
    from aetherix.uefi import console

    app = UefiApp()

    @app.entry
    def main(prog, sys_table):
        console.clear_screen(prog, sys_table)
        console.println(prog, "Hello from Aetherix UEFI!", sys_table)

    return app.build(str(Path(tmp_path) / "hello.efi"))


def test_pe_file_is_recognized_as_efi_application(tmp_path):
    efi_path = _build_hello_efi(tmp_path)
    data = efi_path.read_bytes()

    assert data[0:2] == b"MZ", "missing DOS header magic"
    e_lfanew = int.from_bytes(data[0x3C:0x40], "little")
    assert data[e_lfanew:e_lfanew + 4] == b"PE\x00\x00", "missing PE signature"

    machine = int.from_bytes(data[e_lfanew + 4:e_lfanew + 6], "little")
    assert machine == 0x8664, f"expected IMAGE_FILE_MACHINE_AMD64, got {hex(machine)}"

    opt_header_off = e_lfanew + 4 + 20
    pe_magic = int.from_bytes(data[opt_header_off:opt_header_off + 2], "little")
    assert pe_magic == 0x020B, f"expected PE32+ magic, got {hex(pe_magic)}"

    subsystem = int.from_bytes(data[opt_header_off + 68:opt_header_off + 70], "little")
    assert subsystem == 10, f"expected IMAGE_SUBSYSTEM_EFI_APPLICATION (10), got {subsystem}"

    if shutil.which("file"):
        result = subprocess.run(["file", str(efi_path)], capture_output=True, text=True)
        assert "EFI application" in result.stdout, result.stdout

    print("PASS test_pe_file_is_recognized_as_efi_application")


def test_uefi_hello_world_executes_correctly(tmp_path):
    if not HAVE_UNICORN:
        print("SKIP (unicorn not installed)")
        return
    from emulate_uefi import run_efi_app

    efi_path = _build_hello_efi(tmp_path)
    result = run_efi_app(str(efi_path))

    assert result["clear_screen_calls"] == 1, (
        f"expected exactly one ClearScreen call, got {result['clear_screen_calls']}"
    )
    assert len(result["output_string_calls"]) == 1, (
        f"expected exactly one OutputString call, got {result['output_string_calls']}"
    )
    call = result["output_string_calls"][0]
    assert call["text"] == "Hello from Aetherix UEFI!\r\n", (
        f"wrong string reached ConOut->OutputString: {call['text']!r}"
    )
    assert result["final_rax"] == 0, (
        f"expected EFI_SUCCESS (0) returned in RAX, got {hex(result['final_rax'])}"
    )
    print("PASS test_uefi_hello_world_executes_correctly")


def test_disk_image_gpt_and_fat32_are_valid(tmp_path):
    from aetherix.uefi.diskimage import write_disk_image

    efi_path = _build_hello_efi(tmp_path)
    img_path = write_disk_image(efi_path.read_bytes(), str(Path(tmp_path) / "disk.img"))

    if shutil.which("gdisk"):
        result = subprocess.run(["gdisk", "-l", str(img_path)], capture_output=True, text=True)
        assert "GPT: present" in result.stdout, result.stdout
        assert "EF00" in result.stdout, f"EFI System Partition type not found: {result.stdout}"
    else:
        print("  (gdisk not installed -- skipping independent GPT validation)")

    if shutil.which("mcopy"):
        from aetherix.uefi.gpt import SECTOR_SIZE, PARTITION_ARRAY_SECTORS
        esp_byte_offset = (2 + PARTITION_ARRAY_SECTORS) * SECTOR_SIZE  # LBA 34
        extracted = Path(tmp_path) / "extracted.efi"
        result = subprocess.run(
            ["mcopy", "-n", "-i", f"{img_path}@@{esp_byte_offset}",
             "::/EFI/BOOT/BOOTX64.EFI", str(extracted)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"mcopy failed: {result.stderr}"
        assert extracted.read_bytes() == efi_path.read_bytes(), (
            "file extracted from the disk image via mtools doesn't match the original .efi"
        )
    else:
        print("  (mtools not installed -- skipping independent FAT32 extraction check)")

    print("PASS test_disk_image_gpt_and_fat32_are_valid")


def test_keyboard_and_cursor_control_execute_correctly(tmp_path):
    if not HAVE_UNICORN:
        print("SKIP (unicorn not installed)")
        return
    from emulate_uefi import run_efi_app
    from aetherix.uefi.app import UefiApp
    from aetherix.uefi import console, keyboard
    from aetherix import regs

    app = UefiApp()

    @app.entry
    def main(prog, sys_table):
        console.clear_screen(prog, sys_table)
        console.set_cursor_position(prog, sys_table, 10, 5)
        console.println(prog, "Press a key...", sys_table)
        keyboard.read_key(prog, sys_table, regs.R12, regs.R13)
        console.enable_cursor(prog, sys_table, True)

    efi_path = app.build(str(Path(tmp_path) / "keytest.efi"))
    result = run_efi_app(str(efi_path), key_sequence=[(0, ord("X"))])

    assert result["clear_screen_calls"] == 1
    assert result["set_cursor_position_calls"] == [(10, 5)], result["set_cursor_position_calls"]
    assert result["enable_cursor_calls"] == [True], result["enable_cursor_calls"]
    assert result["output_string_calls"][0]["text"] == "Press a key...\r\n"
    assert result["final_rax"] == 0
    print("PASS test_keyboard_and_cursor_control_execute_correctly")


def test_locate_protocol_and_generic_call_execute_correctly(tmp_path):
    """Also a regression test for a real bug found during development:
    RSP and R12 both encode to the same 3-bit ModRM field (rm=100),
    which x86-64 always requires a SIB byte for regardless of
    addressing mode -- using R12 as a base register (e.g. holding a
    located protocol's interface pointer, a very natural choice) without
    one silently corrupts the instruction stream. This test specifically
    uses R12 for that reason."""
    if not HAVE_UNICORN:
        print("SKIP (unicorn not installed)")
        return
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE, UcError
    from unicorn.x86_const import UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8, UC_X86_REG_RAX, UC_X86_REG_RSP
    import emulate_uefi as E
    from aetherix.uefi.app import UefiApp
    from aetherix.uefi import console, boot_services, protocol
    from aetherix.uefi.guid import guid_bytes
    from aetherix import regs

    FAKE_GUID = "9042A9DE-23DC-4A38-96FB-7ADED080516A"
    FAKE_INTERFACE_ADDR = 0x504000
    CUSTOM_METHOD_STUB = 0x510060

    app = UefiApp()

    @app.entry
    def main(prog, sys_table):
        boot_services.locate_protocol(prog, sys_table, FAKE_GUID, regs.R12)
        prog.cmp64(regs.EAX, 0)
        ok_label = prog.unique_label("ok")
        prog.jcc(regs.JZ, ok_label)
        console.println(prog, "Protocol not found!", sys_table)
        prog.label(ok_label)
        protocol.call(prog, regs.R12, 0x10, args=[("imm", 42)])

    efi_path = app.build(str(Path(tmp_path) / "exttest.efi"))
    data = efi_path.read_bytes()

    e_lfanew = int.from_bytes(data[0x3C:0x40], "little")
    coff_off = e_lfanew + 4
    opt_header_size = int.from_bytes(data[coff_off + 16:coff_off + 18], "little")
    opt_header_off = coff_off + 20
    section_table_off = opt_header_off + opt_header_size
    sec = data[section_table_off:section_table_off + 40]
    virtual_size = int.from_bytes(sec[8:12], "little")
    virtual_address = int.from_bytes(sec[12:16], "little")
    raw_size = int.from_bytes(sec[16:20], "little")
    raw_offset = int.from_bytes(sec[20:24], "little")
    entry_rva = int.from_bytes(data[opt_header_off + 16:opt_header_off + 20], "little")
    text_bytes = data[raw_offset:raw_offset + raw_size]

    mu = Uc(UC_ARCH_X86, UC_MODE_64)
    mu.mem_map(E.IMAGE_BASE, 0x100000)
    mu.mem_map(E.SYSTEM_TABLE_ADDR, 0x1000)
    mu.mem_map(E.CONOUT_ADDR, 0x1000)
    mu.mem_map(E.BOOTSERVICES_ADDR, 0x1000)
    mu.mem_map(FAKE_INTERFACE_ADDR, 0x1000)
    mu.mem_map(E.OUTPUT_STRING_STUB & ~0xFFF, 0x1000)
    mu.mem_map(E.STACK_TOP - 0x10000, 0x10000)
    mu.mem_map(E.RETURN_SENTINEL & ~0xFFF, 0x1000)
    mu.mem_write(E.IMAGE_BASE + virtual_address, text_bytes + b"\x00" * (virtual_size - len(text_bytes)))
    mu.mem_write(E.SYSTEM_TABLE_ADDR + E.SYSTEM_TABLE_CONOUT_OFFSET, E.CONOUT_ADDR.to_bytes(8, "little"))
    mu.mem_write(E.SYSTEM_TABLE_ADDR + E.SYSTEM_TABLE_BOOTSERVICES_OFFSET, E.BOOTSERVICES_ADDR.to_bytes(8, "little"))
    mu.mem_write(E.CONOUT_ADDR + E.CONOUT_OUTPUTSTRING_OFFSET, E.OUTPUT_STRING_STUB.to_bytes(8, "little"))
    mu.mem_write(E.BOOTSERVICES_ADDR + E.BS_LOCATE_PROTOCOL_OFFSET, E.LOCATE_PROTOCOL_STUB.to_bytes(8, "little"))
    mu.mem_write(FAKE_INTERFACE_ADDR + 0x10, CUSTOM_METHOD_STUB.to_bytes(8, "little"))
    for stub in (E.OUTPUT_STRING_STUB, E.LOCATE_PROTOCOL_STUB, CUSTOM_METHOD_STUB):
        mu.mem_write(stub, b"\xc3")

    calls = {"locate_protocol": [], "custom_method": [], "output": []}

    def hook(uc, addr, size, _u):
        if addr == E.LOCATE_PROTOCOL_STUB:
            rcx = uc.reg_read(UC_X86_REG_RCX)
            r8 = uc.reg_read(UC_X86_REG_R8)
            g = bytes(uc.mem_read(rcx, 16))
            calls["locate_protocol"].append(g)
            if g == guid_bytes(FAKE_GUID):
                uc.mem_write(r8, FAKE_INTERFACE_ADDR.to_bytes(8, "little"))
                uc.reg_write(UC_X86_REG_RAX, 0)
            else:
                uc.reg_write(UC_X86_REG_RAX, E.EFI_NOT_FOUND)
        elif addr == CUSTOM_METHOD_STUB:
            rcx = uc.reg_read(UC_X86_REG_RCX)
            rdx = uc.reg_read(UC_X86_REG_RDX)
            calls["custom_method"].append((rcx, rdx))
            uc.reg_write(UC_X86_REG_RAX, 0)
        elif addr == E.OUTPUT_STRING_STUB:
            rdx = uc.reg_read(UC_X86_REG_RDX)
            raw = bytes(uc.mem_read(rdx, 256))
            end = raw.find(b"\x00\x00")
            calls["output"].append(raw[:end].decode("utf-16-le", "replace"))
            uc.reg_write(UC_X86_REG_RAX, 0)
        elif addr == E.RETURN_SENTINEL:
            uc.emu_stop()

    mu.hook_add(UC_HOOK_CODE, hook)
    entry = E.IMAGE_BASE + entry_rva
    mu.reg_write(UC_X86_REG_RCX, 0x1234)
    mu.reg_write(UC_X86_REG_RDX, E.SYSTEM_TABLE_ADDR)
    sp = E.STACK_TOP - 0x100
    mu.mem_write(sp, E.RETURN_SENTINEL.to_bytes(8, "little"))
    mu.reg_write(UC_X86_REG_RSP, sp)

    try:
        mu.emu_start(entry, E.RETURN_SENTINEL, count=200_000)
    except UcError as e:
        raise AssertionError(f"emulation crashed (likely a R12/RSP SIB-byte encoding bug): {e}")

    assert any(g == guid_bytes(FAKE_GUID) for g in calls["locate_protocol"]), (
        "LocateProtocol was never called with the expected GUID"
    )
    assert calls["output"] == [], f"took the 'not found' failure path unexpectedly: {calls['output']}"
    assert calls["custom_method"] == [(FAKE_INTERFACE_ADDR, 42)], calls["custom_method"]
    print("PASS test_locate_protocol_and_generic_call_execute_correctly")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        test_pe_file_is_recognized_as_efi_application(d)
    with tempfile.TemporaryDirectory() as d:
        test_uefi_hello_world_executes_correctly(d)
    with tempfile.TemporaryDirectory() as d:
        test_disk_image_gpt_and_fat32_are_valid(d)
    with tempfile.TemporaryDirectory() as d:
        test_keyboard_and_cursor_control_execute_correctly(d)
    with tempfile.TemporaryDirectory() as d:
        test_locate_protocol_and_generic_call_execute_correctly(d)
