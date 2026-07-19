"""
Real emulation-based verification, using Unicorn Engine (a CPU emulator,
not just a disassembler) to actually *execute* a built Aetherix disk image
and observe what ends up in VGA memory. This is a stronger check than
disassembly: it proves the boot sector's BIOS calls succeed, the mode
switch actually happens, and the kernel body actually runs -- rather than
just that the bytes look plausible.

Not a runtime dependency of Aetherix itself -- only needed to run this
verification script. Install with: pip install unicorn
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_HOOK_INTR, UC_HOOK_INSN, UcError
from unicorn.x86_const import (
    UC_X86_REG_AH, UC_X86_REG_AL, UC_X86_REG_DS, UC_X86_REG_SI,
    UC_X86_REG_CS, UC_X86_REG_IP, UC_X86_REG_DL, UC_X86_REG_SP,
    UC_X86_REG_EFLAGS, UC_X86_INS_IN, UC_X86_INS_OUT,
)

CF_BIT = 0x1
MEM_SIZE = 4 * 1024 * 1024  # 4MB flat address space


def _clear_cf(uc):
    uc.reg_write(UC_X86_REG_EFLAGS, uc.reg_read(UC_X86_REG_EFLAGS) & ~CF_BIT)


def _set_cf(uc):
    uc.reg_write(UC_X86_REG_EFLAGS, uc.reg_read(UC_X86_REG_EFLAGS) | CF_BIT)


def run_image(image_path, max_instructions=20_000_000, key_sequence=None):
    """Boots `image_path` under emulation. Returns a dict with the real-mode
    boot messages printed via BIOS teletype, and the final VGA text-mode
    buffer contents (what a real screen would show).

    `key_sequence`, if given, is a list of scancodes fed to the emulated
    PS/2 controller one at a time (a fresh one each time the guest polls
    port 0x64 and finds it ready) -- simulating a person typing that exact
    sequence of keys. If omitted, a single scancode (0x1C, Enter) is
    available whenever the guest checks for one.
    """
    disk = bytearray(Path(image_path).read_bytes())

    mu = Uc(UC_ARCH_X86, UC_MODE_16)
    mu.mem_map(0, MEM_SIZE)
    mu.mem_write(0x7C00, bytes(disk[0:512]))

    printed = []
    state = {"reads": [], "boot_drive": 0x80,
             "key_queue": list(key_sequence) if key_sequence is not None else None,
             "poll_count": 0, "poll_threshold": 5,
             "dac_index": 0, "dac_channel": 0, "dac_palette": bytearray(256 * 3),
             "reboot_requested": False, "shutdown_requested": False}
    POLL_THRESHOLD = 5

    def hook_intr(uc, intno, _user):
        if intno == 0x10:
            ah = uc.reg_read(UC_X86_REG_AH)
            al = uc.reg_read(UC_X86_REG_AL)
            if ah == 0x0E:
                printed.append(chr(al))
            _clear_cf(uc)
            return
        if intno == 0x13:
            ah = uc.reg_read(UC_X86_REG_AH)
            dl = uc.reg_read(UC_X86_REG_DL)
            # A real BIOS rejects an unrecognized drive number. Checking
            # this here is what catches register-clobber bugs (e.g. a
            # value accidentally saved into BL/BH and restored *after*
            # something else overwrote it) -- an emulator that always
            # succeeds regardless of DL would miss exactly that class of
            # bug, which is what happened during this package's development.
            if dl != state["boot_drive"]:
                _set_cf(uc)
                uc.reg_write(UC_X86_REG_AH, 0x01)  # invalid function/drive
                return
            if ah == 0x00:  # reset
                _clear_cf(uc)
                return
            if ah == 0x41:  # extensions present check
                _clear_cf(uc)
                return
            if ah == 0x42:  # extended (LBA) read
                ds = uc.reg_read(UC_X86_REG_DS)
                si = uc.reg_read(UC_X86_REG_SI)
                addr = (ds << 4) + si
                dap = bytes(uc.mem_read(addr, 16))
                count = int.from_bytes(dap[2:4], "little")
                buf_off = int.from_bytes(dap[4:6], "little")
                buf_seg = int.from_bytes(dap[6:8], "little")
                lba = int.from_bytes(dap[8:16], "little")
                transfer_bytes = count * 512
                if buf_off + transfer_bytes > 0x10000:
                    # Real-mode segment:offset addressing can't represent
                    # this without the 16-bit offset wrapping within the
                    # same segment. A real BIOS rejects this (or, worse,
                    # silently wraps and corrupts memory) -- this check is
                    # what catches an oversized single-chunk read during
                    # development instead of on real hardware, which is
                    # exactly the bug this harness originally missed by
                    # naively writing the whole transfer to a computed
                    # linear address regardless of size.
                    _set_cf(uc)
                    uc.reg_write(UC_X86_REG_AH, 0x09)  # data boundary error
                    return
                data = bytes(disk[lba * 512: lba * 512 + transfer_bytes])
                dest = (buf_seg << 4) + buf_off
                uc.mem_write(dest, data)
                state["reads"].append((lba, count, dest))
                _clear_cf(uc)
                return
            # Unsupported ah -- report failure rather than silently no-op.
            _set_cf(uc)
            return
        # Unknown interrupt: ignore.

    def hook_in(uc, port, size, _user):
        if port == 0x64:  # PS/2 status port
            state["poll_count"] += 1
            if state["key_queue"] is not None:
                ready = state["poll_count"] >= POLL_THRESHOLD and len(state["key_queue"]) > 0
            else:
                ready = state["poll_count"] >= POLL_THRESHOLD
            return 0x01 if ready else 0x00
        if port == 0x60:  # PS/2 data port
            state["poll_count"] = 0  # this key is now "consumed"; next one needs its own delay
            if state["key_queue"] is not None:
                return state["key_queue"].pop(0) if state["key_queue"] else 0x00
            return 0x1C  # default: scancode for Enter
        if port == 0x92:  # A20 fast gate
            return 0x00
        return 0

    def hook_out(uc, port, size, value, _user):
        if port == 0x3C8:  # VGA DAC write index select
            state["dac_index"] = value & 0xFF
            state["dac_channel"] = 0
        elif port == 0x3C9:  # VGA DAC data (R, then G, then B, auto-increments)
            pos = state["dac_index"] * 3 + state["dac_channel"]
            if pos < len(state["dac_palette"]):
                state["dac_palette"][pos] = value & 0xFF
            state["dac_channel"] += 1
            if state["dac_channel"] == 3:
                state["dac_channel"] = 0
                state["dac_index"] = (state["dac_index"] + 1) & 0xFF
        elif port == 0x64 and value == 0xFE:  # keyboard-controller reboot pulse
            state["reboot_requested"] = True
        elif port in (0x604, 0xB004) and value == 0x2000:  # QEMU/Bochs shutdown magic port
            state["shutdown_requested"] = True

    mu.hook_add(UC_HOOK_INTR, hook_intr)
    mu.hook_add(UC_HOOK_INSN, hook_in, aux1=UC_X86_INS_IN)
    mu.hook_add(UC_HOOK_INSN, hook_out, aux1=UC_X86_INS_OUT)

    mu.reg_write(UC_X86_REG_CS, 0)
    mu.reg_write(UC_X86_REG_IP, 0x7C00)
    mu.reg_write(UC_X86_REG_DL, 0x80)  # BIOS boot drive number
    mu.reg_write(UC_X86_REG_SP, 0x7C00)

    try:
        mu.emu_start(0x7C00, 0x7C00 + 0x100000, count=max_instructions)
    except UcError as e:
        state["stop_reason"] = str(e)

    vga_raw = bytes(mu.mem_read(0xB8000, 80 * 25 * 2))
    vga_text_rows = []
    for row in range(25):
        row_bytes = vga_raw[row * 160: row * 160 + 160]
        chars = "".join(chr(row_bytes[i]) if 32 <= row_bytes[i] < 127 else " "
                         for i in range(0, 160, 2))
        vga_text_rows.append(chars.rstrip())

    gfx_framebuffer = bytes(mu.mem_read(0xA0000, 320 * 200))

    return {
        "boot_message": "".join(printed),
        "vga_rows": vga_text_rows,
        "disk_reads": state["reads"],
        "stop_reason": state.get("stop_reason"),
        "gfx_framebuffer": gfx_framebuffer,
        "dac_palette": bytes(state["dac_palette"]),
        "reboot_requested": state["reboot_requested"],
        "shutdown_requested": state["shutdown_requested"],
    }


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "build/hello_os.img"
    result = run_image(path)
    print("Real-mode boot message:", repr(result["boot_message"]))
    print("Disk reads (lba, count, dest):", result["disk_reads"])
    print("Stop reason:", result["stop_reason"])
    print("VGA text buffer (non-blank rows):")
    for i, row in enumerate(result["vga_rows"]):
        if row.strip():
            print(f"  row {i}: {row!r}")
