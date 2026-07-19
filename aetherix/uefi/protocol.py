"""
Generic UEFI protocol/service call helper -- the extension point for
calling *any* UEFI function this package doesn't already wrap with a
dedicated helper (like `console.py`'s ConOut calls or `keyboard.py`'s
ConIn calls).

UEFI functions come in two calling shapes:

  - **Protocol interface methods** (COM-like): the interface pointer
    itself is passed as the first argument ("This"), e.g.
    `ConOut->OutputString(ConOut, String)`. Use `call(..., this_arg=True)`
    (the default).
  - **Service table functions** (EFI_BOOT_SERVICES / EFI_RUNTIME_SERVICES
    members): plain functions with no self-pointer -- the table is only
    used to find the function pointer itself, e.g.
    `BootServices->LocateProtocol(Guid, Registration, Interface)` takes
    three explicit arguments, none of which is BootServices itself. Use
    `call(..., this_arg=False)`.

To find and use a protocol this package doesn't wrap yet: look up its
GUID and the byte offsets of the methods you need in the UEFI
specification (or any EDK2 header), then combine this with
`boot_services.locate_protocol` (to get the interface pointer) and
`call` (to invoke its methods) -- no changes to this package required.
"""
from __future__ import annotations

from .. import regs
from ..asm import Program

# Microsoft x64 ABI integer argument registers, in order.
_ARG_REGS = [regs.ECX, regs.EDX, regs.R8, regs.R9]

MAX_ARGS = 4  # RCX/RDX/R8/R9 -- a 5th+ argument would need stack passing (not implemented)


def call(prog: Program, table_reg: int, method_offset: int, args=(),
         this_arg: bool = True, result_reg: int = None) -> Program:
    """Call a function pointer stored at `table_reg[method_offset]`.

    `args` is a list of additional arguments beyond (or instead of, if
    `this_arg=False`) the interface pointer itself. Each entry is either
    a `Program` register number (its current value is moved into the
    right argument register) or a tuple:
      - `("imm", value)`   -- an integer immediate
      - `("label", name)`  -- the RIP-relative address of a label (e.g.
                              an embedded string or scratch buffer)

    If `this_arg` is True (default -- correct for protocol interfaces
    like ConIn/ConOut), `table_reg`'s value is passed as the first
    argument (RCX) and `args` fills RDX/R8/R9 (so up to 3 entries). If
    `this_arg` is False (correct for EFI_BOOT_SERVICES/
    EFI_RUNTIME_SERVICES members, which have no self-pointer), `args`
    fills RCX/RDX/R8/R9 directly (so up to 4 entries).

    EFI_STATUS ends up in RAX after the call; pass `result_reg` to also
    copy it elsewhere (skip if RAX itself is fine, e.g. immediately
    followed by `prog.cmp64(regs.EAX, 0)`).

    Order of operations matters here, same as any hand-written assembly:
    the function pointer is loaded first (into RAX), then `This` (if
    any) into RCX, then `args` into the remaining registers in order --
    so don't use RAX/RCX as an `args` register source (their values are
    already overwritten by the time `args` are placed); hold such a
    value in another register first if you need it.
    """
    max_args = MAX_ARGS - (1 if this_arg else 0)
    if len(args) > max_args:
        raise ValueError(
            f"protocol.call supports at most {max_args} entries in `args` here "
            f"(this_arg={this_arg}) -- a 5th+ argument would need stack passing, "
            "not implemented"
        )
    if this_arg and table_reg in (regs.EAX, regs.ECX):
        raise ValueError("table_reg must not be RAX/RCX -- they're used internally here")

    prog.load64(regs.EAX, table_reg, method_offset)  # RAX = function pointer

    arg_regs = list(_ARG_REGS)
    if this_arg:
        prog.mov_rr64(regs.ECX, table_reg)
        arg_regs = arg_regs[1:]

    for dst, arg in zip(arg_regs, args):
        if isinstance(arg, tuple):
            kind, value = arg
            if kind == "imm":
                if 0 <= value <= 0xFFFFFFFF:
                    prog.mov64_zx(dst, value)
                else:
                    prog.mov64(dst, value)
            elif kind == "label":
                prog.lea_rip_label(dst, value)
            else:
                raise ValueError(f"unknown arg kind {kind!r} -- expected 'imm' or 'label'")
        else:
            prog.mov_rr64(dst, arg)

    prog.sub64(regs.ESP, 0x20)  # MS x64 ABI: caller-reserved shadow space
    prog.call_r64(regs.EAX)
    prog.add64(regs.ESP, 0x20)

    if result_reg is not None and result_reg != regs.EAX:
        prog.mov_rr64(result_reg, regs.EAX)
    return prog
