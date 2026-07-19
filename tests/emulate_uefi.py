"""
Real x86-64 execution-based verification for UEFI applications, using
Unicorn Engine (UC_MODE_64). Simulates just enough of the UEFI
environment -- EFI_SYSTEM_TABLE with ConOut, ConIn, and BootServices
whose relevant function pointers are real, callable addresses -- to
prove the generated protocol-call sequences actually work: correct
structure offsets, correct MS x64 ABI argument registers (including
R8/R9), and correctly-decoded UCS-2 strings/EFI_INPUT_KEY structs, all
confirmed by executing the real assembled machine code rather than
reading it.

Not a runtime dependency of Aetherix itself -- only needed to run this
verification script. Install with: pip install unicorn
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE, UcError
from unicorn.x86_const import (
    UC_X86_REG_RCX, UC_X86_REG_RDX, UC_X86_REG_R8, UC_X86_REG_R9,
    UC_X86_REG_RAX, UC_X86_REG_RSP,
)

IMAGE_BASE = 0x400000
SYSTEM_TABLE_ADDR = 0x500000
CONOUT_ADDR = 0x501000
CONIN_ADDR = 0x502000
BOOTSERVICES_ADDR = 0x503000
STACK_TOP = 0x600000
RETURN_SENTINEL = 0x520000  # where we tell Unicorn the app "returned to firmware"

# Stub function addresses -- each holds a single RET (0xC3) byte; our
# UC_HOOK_CODE fires when execution reaches one, lets us observe/fake
# the call's effect, and then execution naturally falls through to the
# RET byte already there.
OUTPUT_STRING_STUB = 0x510000
CLEAR_SCREEN_STUB = 0x510010
READ_KEY_STROKE_STUB = 0x510020
SET_CURSOR_POSITION_STUB = 0x510030
ENABLE_CURSOR_STUB = 0x510040
LOCATE_PROTOCOL_STUB = 0x510050

SYSTEM_TABLE_CONOUT_OFFSET = 0x40
SYSTEM_TABLE_CONIN_OFFSET = 0x30
SYSTEM_TABLE_BOOTSERVICES_OFFSET = 0x60

CONOUT_OUTPUTSTRING_OFFSET = 0x08
CONOUT_CLEARSCREEN_OFFSET = 0x30
CONOUT_SETCURSORPOSITION_OFFSET = 0x38
CONOUT_ENABLECURSOR_OFFSET = 0x40

CONIN_READKEYSTROKE_OFFSET = 0x08

BS_LOCATE_PROTOCOL_OFFSET = 0x140

EFI_NOT_READY = 0x8000000000000006
EFI_NOT_FOUND = 0x800000000000000E


def run_efi_app(efi_path, max_instructions=2_000_000, key_sequence=None,
                 poll_threshold=3, protocols=None):
    """Loads a built .efi file's .text section at IMAGE_BASE, calls its
    entry point exactly as UEFI firmware would, and records every
    ConOut/ConIn/BootServices call actually made.

    `key_sequence`, if given, is a list of `(scancode, unicode_char)`
    tuples fed to ConIn->ReadKeyStroke one at a time -- each takes
    `poll_threshold` polls to arrive (simulating "no key pressed yet"),
    matching how a real busy-poll keyboard read behaves.

    `protocols`, if given, maps a GUID string to a fake interface address
    (any integer) -- BootServices->LocateProtocol succeeds and returns
    that address for a matching GUID, and EFI_NOT_FOUND otherwise.
    """
    data = Path(efi_path).read_bytes()
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
    mu.mem_map(IMAGE_BASE, 0x100000)
    mu.mem_map(SYSTEM_TABLE_ADDR, 0x1000)
    mu.mem_map(CONOUT_ADDR, 0x1000)
    mu.mem_map(CONIN_ADDR, 0x1000)
    mu.mem_map(BOOTSERVICES_ADDR, 0x1000)
    mu.mem_map(OUTPUT_STRING_STUB & ~0xFFF, 0x1000)
    mu.mem_map(STACK_TOP - 0x10000, 0x10000)
    mu.mem_map(RETURN_SENTINEL & ~0xFFF, 0x1000)

    mu.mem_write(IMAGE_BASE + virtual_address, text_bytes + b"\x00" * (virtual_size - len(text_bytes)))

    mu.mem_write(SYSTEM_TABLE_ADDR + SYSTEM_TABLE_CONOUT_OFFSET, CONOUT_ADDR.to_bytes(8, "little"))
    mu.mem_write(SYSTEM_TABLE_ADDR + SYSTEM_TABLE_CONIN_OFFSET, CONIN_ADDR.to_bytes(8, "little"))
    mu.mem_write(SYSTEM_TABLE_ADDR + SYSTEM_TABLE_BOOTSERVICES_OFFSET, BOOTSERVICES_ADDR.to_bytes(8, "little"))

    mu.mem_write(CONOUT_ADDR + CONOUT_OUTPUTSTRING_OFFSET, OUTPUT_STRING_STUB.to_bytes(8, "little"))
    mu.mem_write(CONOUT_ADDR + CONOUT_CLEARSCREEN_OFFSET, CLEAR_SCREEN_STUB.to_bytes(8, "little"))
    mu.mem_write(CONOUT_ADDR + CONOUT_SETCURSORPOSITION_OFFSET, SET_CURSOR_POSITION_STUB.to_bytes(8, "little"))
    mu.mem_write(CONOUT_ADDR + CONOUT_ENABLECURSOR_OFFSET, ENABLE_CURSOR_STUB.to_bytes(8, "little"))
    mu.mem_write(CONIN_ADDR + CONIN_READKEYSTROKE_OFFSET, READ_KEY_STROKE_STUB.to_bytes(8, "little"))
    mu.mem_write(BOOTSERVICES_ADDR + BS_LOCATE_PROTOCOL_OFFSET, LOCATE_PROTOCOL_STUB.to_bytes(8, "little"))

    for stub in (OUTPUT_STRING_STUB, CLEAR_SCREEN_STUB, READ_KEY_STROKE_STUB,
                 SET_CURSOR_POSITION_STUB, ENABLE_CURSOR_STUB, LOCATE_PROTOCOL_STUB):
        mu.mem_write(stub, b"\xc3")

    state = {
        "output_string": [], "clear_screen": 0,
        "set_cursor_position": [], "enable_cursor": [],
        "locate_protocol": [],
        "key_queue": list(key_sequence) if key_sequence else [],
        "poll_count": 0,
    }
    protocols = protocols or {}

    def hook_code(uc, address, size, _user):
        if address == OUTPUT_STRING_STUB:
            rcx = uc.reg_read(UC_X86_REG_RCX)
            rdx = uc.reg_read(UC_X86_REG_RDX)
            raw = bytes(uc.mem_read(rdx, 512))
            end = len(raw)
            for i in range(0, len(raw) - 1, 2):
                if raw[i] == 0 and raw[i + 1] == 0:
                    end = i
                    break
            state["output_string"].append({"this": rcx, "text": raw[:end].decode("utf-16-le", errors="replace")})
            uc.reg_write(UC_X86_REG_RAX, 0)

        elif address == CLEAR_SCREEN_STUB:
            state["clear_screen"] += 1
            uc.reg_write(UC_X86_REG_RAX, 0)

        elif address == SET_CURSOR_POSITION_STUB:
            column = uc.reg_read(UC_X86_REG_RDX)
            row = uc.reg_read(UC_X86_REG_R8)
            state["set_cursor_position"].append((column, row))
            uc.reg_write(UC_X86_REG_RAX, 0)

        elif address == ENABLE_CURSOR_STUB:
            visible = uc.reg_read(UC_X86_REG_RDX)
            state["enable_cursor"].append(bool(visible))
            uc.reg_write(UC_X86_REG_RAX, 0)

        elif address == READ_KEY_STROKE_STUB:
            state["poll_count"] += 1
            if not state["key_queue"] or state["poll_count"] < poll_threshold:
                uc.reg_write(UC_X86_REG_RAX, EFI_NOT_READY)
                return
            state["poll_count"] = 0
            scancode, unicode_char = state["key_queue"].pop(0)
            rdx = uc.reg_read(UC_X86_REG_RDX)  # EFI_INPUT_KEY* out param
            uc.mem_write(rdx, scancode.to_bytes(2, "little") + unicode_char.to_bytes(2, "little"))
            uc.reg_write(UC_X86_REG_RAX, 0)

        elif address == LOCATE_PROTOCOL_STUB:
            rcx = uc.reg_read(UC_X86_REG_RCX)  # EFI_GUID*
            r8 = uc.reg_read(UC_X86_REG_R8)    # VOID** Interface (out param)
            guid_bytes = bytes(uc.mem_read(rcx, 16))
            match_addr = protocols.get(guid_bytes)
            state["locate_protocol"].append(guid_bytes)
            if match_addr is not None:
                uc.mem_write(r8, match_addr.to_bytes(8, "little"))
                uc.reg_write(UC_X86_REG_RAX, 0)
            else:
                uc.reg_write(UC_X86_REG_RAX, EFI_NOT_FOUND)

        elif address == RETURN_SENTINEL:
            uc.emu_stop()

    mu.hook_add(UC_HOOK_CODE, hook_code)

    entry_point = IMAGE_BASE + entry_rva
    mu.reg_write(UC_X86_REG_RCX, 0x1234)
    mu.reg_write(UC_X86_REG_RDX, SYSTEM_TABLE_ADDR)
    stack_ptr = STACK_TOP - 0x100
    mu.mem_write(stack_ptr, RETURN_SENTINEL.to_bytes(8, "little"))
    mu.reg_write(UC_X86_REG_RSP, stack_ptr)

    stop_reason = None
    try:
        mu.emu_start(entry_point, RETURN_SENTINEL, count=max_instructions)
    except UcError as e:
        stop_reason = str(e)

    return {
        "output_string_calls": state["output_string"],
        "clear_screen_calls": state["clear_screen"],
        "set_cursor_position_calls": state["set_cursor_position"],
        "enable_cursor_calls": state["enable_cursor"],
        "locate_protocol_guids": state["locate_protocol"],
        "stop_reason": stop_reason,
        "final_rax": mu.reg_read(UC_X86_REG_RAX),
    }


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/hello.efi"
    result = run_efi_app(path)
    print("stop_reason:", result["stop_reason"])
    print("ClearScreen calls:", result["clear_screen_calls"])
    print("SetCursorPosition calls:", result["set_cursor_position_calls"])
    print("EnableCursor calls:", result["enable_cursor_calls"])
    print("OutputString calls:")
    for call in result["output_string_calls"]:
        print(f"  This=0x{call['this']:x} text={call['text']!r}")
    print("Final RAX (EFI_STATUS):", hex(result["final_rax"]))
