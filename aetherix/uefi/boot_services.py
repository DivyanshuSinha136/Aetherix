"""
EFI_BOOT_SERVICES calls -- currently just LocateProtocol, the standard
way to reach most UEFI protocols beyond ConIn/ConOut (which hang directly
off EFI_SYSTEM_TABLE). Combine this with `protocol.call` to use a
protocol this package doesn't have a dedicated wrapper for -- see the
module docstring in protocol.py.
"""
from __future__ import annotations

from .. import regs
from ..asm import Program
from . import protocol
from .guid import guid_bytes

SYSTEM_TABLE_BOOTSERVICES_OFFSET = 0x60
BS_LOCATE_PROTOCOL_OFFSET = 0x140


def locate_protocol(prog: Program, system_table_reg: int, guid: str, out_reg: int) -> Program:
    """Look up a protocol interface by GUID via
    BootServices->LocateProtocol(&Guid, NULL, &Interface). On success
    (EAX == 0 / EFI_SUCCESS after this), `out_reg` holds the protocol
    interface pointer -- pass it as `table_reg` to `protocol.call` to
    invoke its methods. Returns EFI_NOT_FOUND (nonzero) in EAX if the
    protocol isn't available on this platform, e.g. no Graphics Output
    Protocol on a serial-console-only system -- always check EAX rather
    than assuming success.

    `guid` is the canonical string form, e.g.
    "9042A9DE-23DC-4A38-96FB-7ADED080516A" (Graphics Output Protocol).
    `out_reg` must not be RAX/RCX/RDX/R8 (used internally); RSI/RDI/RBX/
    R12-R15 are all fine (callee-saved, so they also survive further
    calls).
    """
    if out_reg in (regs.EAX, regs.ECX, regs.EDX, regs.R8):
        raise ValueError("out_reg must not be RAX/RCX/RDX/R8 -- they're used internally here")

    guid_label = prog.unique_label("locate_protocol_guid")
    out_ptr_label = prog.unique_label("locate_protocol_outptr")
    skip_label = prog.unique_label("locate_protocol_skip")

    prog.load64(regs.EAX, system_table_reg, SYSTEM_TABLE_BOOTSERVICES_OFFSET)  # RAX = BootServices

    protocol.call(
        prog, regs.EAX, BS_LOCATE_PROTOCOL_OFFSET,
        args=[("label", guid_label), ("imm", 0), ("label", out_ptr_label)],
        this_arg=False,
    )

    prog.lea_rip_label(regs.EDX, out_ptr_label)
    prog.load64(out_reg, regs.EDX, 0)

    prog.jmp(skip_label)
    prog.label(guid_label)
    prog.raw_bytes(guid_bytes(guid))
    prog.label(out_ptr_label)
    prog.raw_bytes(b"\x00" * 8)  # scratch: LocateProtocol writes the interface pointer here
    prog.label(skip_label)
    return prog
