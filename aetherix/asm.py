"""
Mid-level assembler layer.

`Program` lets you write bootloader/kernel code as a sequence of Pythonic
instruction calls plus symbolic labels (`.label("loop")`, `.jmp("loop")`),
and resolves forward/backward jump offsets automatically in a two-pass
assembly step. Every instruction still bottoms out in the C encoder in
`_native.py` — nothing here does its own byte-patching guesswork; sizes
are deterministic per instruction form, so the two passes always agree.

This is deliberately explicit: `prog.mov16(AX, 0x07C0)` reads like assembly
for people who know assembly, while `Program.org(...)`/`BootSector` in
`aetherix.boot` wrap common patterns for people who don't.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ._native import NativeBuffer
from . import regs


@dataclass
class _Node:
    size: int
    emit: Callable[[NativeBuffer, int, dict], None]
    # emit(buf, this_node_offset, labels) -> writes bytes for this node into buf
    label: Optional[str] = None  # set if this node is a label marker (size 0)


class Program:
    """A sequence of instructions assembled into raw machine code bytes."""

    def __init__(self, bits: int = 16):
        assert bits in (16, 32, 64)
        self.bits = bits
        self._nodes: List[_Node] = []
        self._label_counter = 0
        self._base_address = 0

    def set_base_address(self, addr: int) -> "Program":
        """Record this program's own absolute load address, so
        `mov32_label`/`mov16_label` can compute a label's real runtime
        address without the caller needing to pass `base=` every time.
        `Kernel` sets this automatically for your kernel-entry function's
        `prog` (see `aetherix.kernel.builder`)."""
        self._base_address = addr
        return self

    def unique_label(self, prefix: str = "L") -> str:
        """Generate a fresh, collision-free label name -- useful when
        building code from a loop or a reusable helper function that may
        emit the same instruction pattern (and thus the same label names)
        many times in one program."""
        self._label_counter += 1
        return f"__{prefix}_{self._label_counter}"

    # -- bookkeeping ------------------------------------------------------

    def label(self, name: str) -> "Program":
        self._nodes.append(_Node(size=0, emit=lambda buf, off, labels: None, label=name))
        return self

    def _fixed(self, size: int, fn: Callable[[NativeBuffer], None]) -> "Program":
        self._nodes.append(_Node(size=size, emit=lambda buf, off, labels: fn(buf)))
        return self

    def _measure(self, fn: Callable[[NativeBuffer], None]) -> int:
        scratch = NativeBuffer()
        fn(scratch)
        return len(scratch)

    # -- raw data -----------------------------------------------------------

    def db(self, *values: int) -> "Program":
        for v in values:
            self._fixed(1, lambda buf, v=v: buf.call("aeth_db", v & 0xFF))
        return self

    def dw(self, *values: int) -> "Program":
        for v in values:
            self._fixed(2, lambda buf, v=v: buf.call("aeth_dw", v & 0xFFFF))
        return self

    def dd(self, *values: int) -> "Program":
        for v in values:
            self._fixed(4, lambda buf, v=v: buf.call("aeth_dd", v & 0xFFFFFFFF))
        return self

    def ascii(self, text: str, zero_terminate: bool = False) -> "Program":
        data = text.encode("utf-8") + (b"\x00" if zero_terminate else b"")
        self._nodes.append(_Node(size=len(data), emit=lambda buf, off, labels, d=data: buf.dbytes(d)))
        return self

    def raw_bytes(self, data: bytes) -> "Program":
        """Embed an arbitrary binary blob as a single node (one bulk write
        at assembly time), rather than `db()`'s one-Python-object-per-byte
        approach. Use this for anything more than a few dozen bytes --
        e.g. an embedded image's palette/pixel data, which can easily be
        tens of thousands of bytes and would otherwise mean tens of
        thousands of individual node objects and ctypes calls."""
        data = bytes(data)
        self._nodes.append(_Node(size=len(data), emit=lambda buf, off, labels, d=data: buf.dbytes(d)))
        return self

    def ascii_cp437(self, text: str, zero_terminate: bool = False, errors: str = "replace") -> "Program":
        """Like `ascii()`, but for text that will be *displayed* on a real
        VGA/BIOS text-mode screen rather than stored as data. VGA hardware
        shows one glyph per byte from its built-in CP437 font -- it does
        not understand UTF-8 -- so this encodes through
        `aetherix.encoding.to_vga_bytes` instead of raw UTF-8. Used by
        `BootSector.print_message`, `drivers.vga.print_string`, and
        `drivers.terminal.putchar_imm`; use this directly if you're
        emitting your own screen-text data blob."""
        from .encoding import to_vga_bytes
        data = to_vga_bytes(text, errors=errors) + (b"\x00" if zero_terminate else b"")
        self._nodes.append(_Node(size=len(data), emit=lambda buf, off, labels, d=data: buf.dbytes(d)))
        return self

    def pad_to(self, size: int, fill: int = 0x00) -> "Program":
        """Pad with `fill` bytes until the program reaches `size` bytes total."""
        current = self.size()
        if current > size:
            raise ValueError(f"Program already {current} bytes, cannot pad to {size}")
        for _ in range(size - current):
            self.db(fill)
        return self

    def size(self) -> int:
        return sum(n.size for n in self._nodes)

    # -- 16-bit real-mode instructions --------------------------------------

    def cli(self):
        return self._fixed(1, lambda b: b.call("aeth_cli"))

    def sti(self):
        return self._fixed(1, lambda b: b.call("aeth_sti"))

    def hlt(self):
        return self._fixed(1, lambda b: b.call("aeth_hlt"))

    def nop(self):
        return self._fixed(1, lambda b: b.call("aeth_nop"))

    def cld(self):
        return self._fixed(1, lambda b: b.call("aeth_cld"))

    def ret(self):
        return self._fixed(1, lambda b: b.call("aeth_ret"))

    def mov16(self, reg: int, imm: int):
        return self._fixed(3, lambda b: b.call("aeth_mov_r16_imm16", reg, imm & 0xFFFF))

    def mov8(self, reg: int, imm: int):
        return self._fixed(2, lambda b: b.call("aeth_mov_r8_imm8", reg, imm & 0xFF))

    def mov_sreg(self, sreg: int, r16: int):
        return self._fixed(2, lambda b: b.call("aeth_mov_sreg_r16", sreg, r16))

    def mov_from_sreg(self, r16: int, sreg: int):
        return self._fixed(2, lambda b: b.call("aeth_mov_r16_sreg", r16, sreg))

    def mov_rr16(self, dst: int, src: int):
        return self._fixed(2, lambda b: b.call("aeth_mov_r16_r16", dst, src))

    def mov_rr8(self, dst: int, src: int):
        return self._fixed(2, lambda b: b.call("aeth_mov_r8_r8", dst, src))

    def xor16(self, dst: int, src: int):
        return self._fixed(2, lambda b: b.call("aeth_xor_r16_r16", dst, src))

    def push16(self, reg: int):
        return self._fixed(1, lambda b: b.call("aeth_push_r16", reg))

    def pop16(self, reg: int):
        return self._fixed(1, lambda b: b.call("aeth_pop_r16", reg))

    def push32(self, reg: int):
        """Same opcode as push16 (0x50+reg) -- in a 32-bit code segment
        this pushes the full 32-bit register. Provided as a clearly-named
        alias for use in Program(bits=32) code."""
        return self._fixed(1, lambda b: b.call("aeth_push_r16", reg))

    def pop32(self, reg: int):
        return self._fixed(1, lambda b: b.call("aeth_pop_r16", reg))

    def interrupt(self, num: int):
        return self._fixed(2, lambda b: b.call("aeth_int", num & 0xFF))

    def store16(self, disp: int, reg: int):
        return self._fixed(4, lambda b: b.call("aeth_mov_mem16_r16", disp & 0xFFFF, reg))

    def store16_label(self, label: str, reg: int, extra_offset: int = 0, base: int = 0):
        """mov [base + address_of(label) + extra_offset], reg -- like
        mov16_label but stores a register instead of loading an address.
        Lets you update one field of a data structure (e.g. a Disk Address
        Packet) whose address isn't known until assembly."""
        size = 4
        def emit(buf, off, labels, reg=reg, base=base, extra_offset=extra_offset):
            target = labels[label]
            disp = (base + target + extra_offset) & 0xFFFF
            buf.call("aeth_mov_mem16_r16", disp, reg)
        self._nodes.append(_Node(size=size, emit=emit))
        return self

    def add_rr16(self, dst: int, src: int):
        """Same opcode as add_rr32 (0x01 /r) -- in a 16-bit code segment
        this adds the 16-bit registers. Named separately for clarity."""
        return self._fixed(2, lambda b: b.call("aeth_add_r32_r32", dst, src))

    def sub_rr16(self, dst: int, src: int):
        return self._fixed(2, lambda b: b.call("aeth_sub_r32_r32", dst, src))

    def load16(self, reg: int, disp: int):
        return self._fixed(4, lambda b: b.call("aeth_mov_r16_mem16", reg, disp & 0xFFFF))

    def load8_si(self, reg: int):
        """mov reg, [si] -- register-indirect load, no displacement."""
        return self._fixed(2, lambda b: b.call("aeth_mov_r8_mem_si", reg))

    def inc16(self, reg: int):
        return self._fixed(1, lambda b: b.call("aeth_inc_r16", reg))

    def cmp8(self, reg: int, imm: int):
        return self._fixed(3, lambda b: b.call("aeth_cmp_r8_imm8", reg, imm & 0xFF))

    def test_al(self, imm: int):
        """test al, imm -- sets ZF = ((al & imm) == 0), does not modify al."""
        return self._fixed(2, lambda b: b.call("aeth_test_al_imm8", imm & 0xFF))

    def or_al(self, imm: int):
        return self._fixed(2, lambda b: b.call("aeth_or_al_imm8", imm & 0xFF))

    def and_al(self, imm: int):
        return self._fixed(2, lambda b: b.call("aeth_and_al_imm8", imm & 0xFF))

    def cmp16(self, reg: int, imm: int):
        return self._fixed(4, lambda b: b.call("aeth_cmp_r16_imm16", reg, imm & 0xFFFF))

    def in_al(self, port: Optional[int] = None):
        if port is None:
            return self._fixed(1, lambda b: b.call("aeth_in_al_dx"))
        return self._fixed(2, lambda b: b.call("aeth_in_al_imm8", port & 0xFF))

    def out_al(self, port: Optional[int] = None):
        if port is None:
            return self._fixed(1, lambda b: b.call("aeth_out_dx_al"))
        return self._fixed(2, lambda b: b.call("aeth_out_imm8_al", port & 0xFF))

    def far_jmp16(self, seg: int, off: int):
        return self._fixed(5, lambda b: b.call("aeth_far_jmp16", seg & 0xFFFF, off & 0xFFFF))

    def lgdt(self, disp: int):
        return self._fixed(5, lambda b: b.call("aeth_lgdt_mem16", disp & 0xFFFF))

    def mov_eax_cr0(self):
        return self._fixed(3, lambda b: b.call("aeth_mov_eax_cr0"))

    def mov_cr0_eax(self):
        return self._fixed(3, lambda b: b.call("aeth_mov_cr0_eax"))

    def or_eax(self, imm: int):
        return self._fixed(6, lambda b: b.call("aeth_or_eax_imm32", imm & 0xFFFFFFFF))

    # -- jumps (label-based, two-pass resolved) -----------------------------

    def mov16_label(self, reg: int, label: str, base: int = 0):
        """mov reg, (base + address of label), 16-bit -- for pointing a
        register at a data blob (e.g. a disk address packet) placed
        elsewhere in the same program, where the blob's exact offset isn't
        known until assembly (variable-length code may precede it)."""
        size = 3
        def emit(buf, off, labels, size=size, reg=reg, base=base):
            target = labels[label]
            value = (base + target) & 0xFFFF
            buf.call("aeth_mov_r16_imm16", reg, value)
        self._nodes.append(_Node(size=size, emit=emit))
        return self

    def mov32_label(self, reg: int, label: str, base: int = None):
        """mov reg, (base + address of label), 32-bit -- like
        `mov16_label`, but for 32-bit code. If `base` is omitted, uses this
        Program's own base address (see `set_base_address`) -- the
        expected case for a kernel body referencing a data blob (e.g. an
        embedded image) placed elsewhere in the same program."""
        size = 5
        def emit(buf, off, labels, size=size, reg=reg, base=base):
            target = labels[label]
            resolved_base = self._base_address if base is None else base
            value = (resolved_base + target) & 0xFFFFFFFF
            buf.call("aeth_mov_r32_imm32", reg, value)
        self._nodes.append(_Node(size=size, emit=emit))
        return self

    def jmp(self, label: str):
        """Near jump (16-bit: rel16 / 32-bit: rel32) to a label."""
        size = 3 if self.bits == 16 else 5
        def emit(buf, off, labels, size=size):
            target = labels[label]
            rel = target - (off + size)
            if self.bits == 16:
                buf.call("aeth_jmp_rel16", rel & 0xFFFF if rel >= 0 else rel)
            else:
                buf.call("aeth_jmp_rel32", rel)
        self._nodes.append(_Node(size=size, emit=emit))
        return self

    def jcc(self, cc: int, label: str):
        """Conditional jump to a label. 16-bit uses rel8 (short range, <=127
        bytes) which is sufficient for tight loops in a boot sector; 32-bit
        kernel code uses the rel32 near form with unlimited range."""
        size = 2 if self.bits == 16 else 6
        def emit(buf, off, labels, size=size, cc=cc):
            target = labels[label]
            rel = target - (off + size)
            if self.bits == 16:
                if not (-128 <= rel <= 127):
                    raise ValueError(f"Short jump to '{label}' out of range ({rel}); restructure code")
                buf.call("aeth_jcc_rel8", cc, rel)
            else:
                buf.call("aeth_jcc_rel32", cc, rel)
        self._nodes.append(_Node(size=size, emit=emit))
        return self

    def call(self, label: str):
        if self.bits not in (32, 64):
            raise NotImplementedError("call/rel32 is only implemented for 32-bit and 64-bit code")
        size = 5
        def emit(buf, off, labels, size=size):
            target = labels[label]
            rel = target - (off + size)
            buf.call("aeth_call_rel32", rel)
        self._nodes.append(_Node(size=size, emit=emit))
        return self

    # -- 32-bit protected-mode instructions ----------------------------------

    def mov32(self, reg: int, imm: int):
        return self._fixed(5, lambda b: b.call("aeth_mov_r32_imm32", reg, imm & 0xFFFFFFFF))

    def mov_rr32(self, dst: int, src: int):
        return self._fixed(2, lambda b: b.call("aeth_mov_r32_r32", dst, src))

    def store32(self, disp: int, reg: int):
        return self._fixed(6, lambda b: b.call("aeth_mov_mem32_r32", disp & 0xFFFFFFFF, reg))

    def load32(self, reg: int, disp: int):
        return self._fixed(6, lambda b: b.call("aeth_mov_r32_mem32", reg, disp & 0xFFFFFFFF))

    def store8(self, disp: int, imm: int):
        return self._fixed(7, lambda b: b.call("aeth_mov_mem8_imm8", disp & 0xFFFFFFFF, imm & 0xFF))

    def store8_reg(self, disp: int, reg: int):
        return self._fixed(6, lambda b: b.call("aeth_mov_mem8_r8", disp & 0xFFFFFFFF, reg))

    def load8(self, reg: int, disp: int):
        return self._fixed(6, lambda b: b.call("aeth_mov_r8_mem8", reg, disp & 0xFFFFFFFF))

    def add32(self, reg: int, imm: int):
        return self._fixed(6, lambda b: b.call("aeth_add_r32_imm32", reg, imm & 0xFFFFFFFF))

    def sub32(self, reg: int, imm: int):
        return self._fixed(6, lambda b: b.call("aeth_sub_r32_imm32", reg, imm & 0xFFFFFFFF))

    def cmp32(self, reg: int, imm: int):
        return self._fixed(6, lambda b: b.call("aeth_cmp_r32_imm32", reg, imm & 0xFFFFFFFF))

    def inc32(self, reg: int):
        return self._fixed(1, lambda b: b.call("aeth_inc_r32", reg))

    # -- 32-bit register-indirect addressing: [reg] / [reg+disp8] -----------
    # base register must be EAX/ECX/EDX/EBX/ESI/EDI (not ESP/EBP).

    def load8_ind(self, r8: int, base: int):
        """mov r8, [base]"""
        return self._fixed(2, lambda b: b.call("aeth_mov_r8_mem_reg32", r8, base))

    def store8_ind(self, base: int, r8: int):
        """mov [base], r8"""
        return self._fixed(2, lambda b: b.call("aeth_mov_mem_reg32_r8", base, r8))

    def store8_ind_imm(self, base: int, imm: int):
        """mov byte [base], imm8"""
        return self._fixed(3, lambda b: b.call("aeth_mov_mem_reg32_imm8", base, imm & 0xFF))

    def store8_ind_disp(self, base: int, disp: int, r8: int):
        """mov [base+disp8], r8"""
        return self._fixed(3, lambda b: b.call("aeth_mov_mem_reg32_disp8_r8", base, disp, r8))

    def store8_ind_disp_imm(self, base: int, disp: int, imm: int):
        """mov byte [base+disp8], imm8"""
        return self._fixed(4, lambda b: b.call("aeth_mov_mem_reg32_disp8_imm8", base, disp, imm & 0xFF))

    def add_rr32(self, dst: int, src: int):
        return self._fixed(2, lambda b: b.call("aeth_add_r32_r32", dst, src))

    def sub_rr32(self, dst: int, src: int):
        return self._fixed(2, lambda b: b.call("aeth_sub_r32_r32", dst, src))

    def out_ax16(self):
        """out dx, ax -- 16-bit port write; DX must already hold the port.
        Only valid in 32-bit code (see the native encoder's docstring for
        why); needed for ports that expect a word write rather than the
        byte-only out_al()."""
        return self._fixed(2, lambda b: b.call("aeth_out_dx_ax16"))

    def busy_wait(self, scratch_reg: int, iterations: int):
        """A simple decrement-and-compare busy-wait loop. Clobbers
        `scratch_reg`. There's no timer/sleep instruction in this encoder
        subset, so this is the only way to get an audible-length delay
        (e.g. for a speaker beep) -- it burns CPU cycles rather than
        actually sleeping, exactly like the classic "empty for-loop delay"
        technique in early PC BIOS/DOS-era code."""
        label = self.unique_label("delay")
        self.mov32(scratch_reg, iterations)
        self.label(label)
        self.sub32(scratch_reg, 1)
        self.cmp32(scratch_reg, 0)
        self.jcc(regs.JNZ, label)
        return self

    # -- assembly ------------------------------------------------------------

    # -- 64-bit long-mode instructions (UEFI applications) ------------------
    # Restricted to registers 0-7 (RAX/RCX/RDX/RBX/RSP/RBP/RSI/RDI) for this
    # first pass -- see native/encoder.c's comment for why. Register
    # numbering matches regs.EAX etc (0-7); use those constants for r64s too.

    def push64(self, reg: int):
        return self._fixed(1, lambda b: b.call("aeth64_push_r64", reg))

    def pop64(self, reg: int):
        return self._fixed(1, lambda b: b.call("aeth64_pop_r64", reg))

    def ret64(self):
        return self._fixed(1, lambda b: b.call("aeth64_ret"))

    def nop64(self):
        return self._fixed(1, lambda b: b.call("aeth64_nop"))

    def hlt64(self):
        return self._fixed(1, lambda b: b.call("aeth64_hlt"))

    def mov64(self, reg: int, imm: int):
        """mov r64, imm64 (movabs) -- full 8-byte immediate. Use mov64_zx
        for small constants (3 bytes shorter, no REX needed)."""
        return self._fixed(10, lambda b: b.call("aeth64_mov_r64_imm64", reg, imm & 0xFFFFFFFFFFFFFFFF))

    def mov64_zx(self, reg: int, imm: int):
        """mov r32, imm32 -- zero-extends into the full r64 register.
        Cheaper than mov64 for constants that fit in 32 bits unsigned."""
        return self._fixed(5, lambda b: b.call("aeth64_mov_r32_imm32_zx", reg, imm & 0xFFFFFFFF))

    def mov_rr64(self, dst: int, src: int):
        return self._fixed(3, lambda b: b.call("aeth64_mov_r64_r64", dst, src))

    def _disp_size(self, disp: int, base: int = 0) -> int:
        size = 3 if -128 <= disp <= 127 else 6
        if (base & 7) == 4:  # RSP/R12 need a mandatory SIB byte -- see native/encoder.c
            size += 1
        return size

    def store64(self, base: int, disp: int, src: int):
        """mov [base + disp], src (64-bit). `disp` may be 0 -- always
        encoded with an explicit displacement byte/dword (see
        native/encoder.c) so any of the 16 base registers work uniformly."""
        size = self._disp_size(disp, base) + 1  # +1 for the REX prefix
        return self._fixed(size, lambda b: b.call("aeth64_mov_mem_r64", base, disp, src))

    def load64(self, dst: int, base: int, disp: int):
        size = self._disp_size(disp, base) + 1
        return self._fixed(size, lambda b: b.call("aeth64_mov_r64_mem", dst, base, disp))

    def movzx64_mem16(self, dst: int, base: int, disp: int):
        """movzx r64, word ptr [base+disp] -- zero-extends a 16-bit
        memory value into a 64-bit register. Used to read individual
        fields out of small fixed-size structs (e.g. UEFI's
        EFI_INPUT_KEY) without needing a shift instruction."""
        size = self._disp_size(disp, base) + 2  # +1 REX, +1 extra opcode byte (0F B7)
        return self._fixed(size, lambda b: b.call("aeth64_movzx_r64_mem16", dst, base, disp))

    def lea_rip_label(self, dst: int, label: str):
        """lea r64, [rip + label] -- the position-independent way to get
        the address of embedded data (e.g. a UCS-2 string), since a PE
        image can be loaded at any base address. `rip` at the time of
        this computation is the address of the *next* instruction, which
        this resolves correctly regardless of what comes before/after."""
        size = 7
        def emit(buf, off, labels, size=size, dst=dst):
            target = labels[label]
            rip_after_instr = off + size
            rel = target - rip_after_instr
            buf.call("aeth64_lea_r64_rip", dst, rel)
        self._nodes.append(_Node(size=size, emit=emit))
        return self

    def call_r64(self, reg: int):
        return self._fixed(2, lambda b: b.call("aeth64_call_r64", reg))

    def add64(self, reg: int, imm: int):
        return self._fixed(7, lambda b: b.call("aeth64_add_r64_imm32", reg, imm & 0xFFFFFFFF))

    def sub64(self, reg: int, imm: int):
        return self._fixed(7, lambda b: b.call("aeth64_sub_r64_imm32", reg, imm & 0xFFFFFFFF))

    def cmp64(self, reg: int, imm: int):
        return self._fixed(7, lambda b: b.call("aeth64_cmp_r64_imm32", reg, imm & 0xFFFFFFFF))

    def cmp_rr64(self, a: int, b_reg: int):
        return self._fixed(3, lambda b: b.call("aeth64_cmp_r64_r64", a, b_reg))

    def xor_rr64(self, dst: int, src: int):
        return self._fixed(3, lambda b: b.call("aeth64_xor_r64_r64", dst, src))

    def assemble(self) -> bytes:
        """Resolve all labels and emit the final machine code bytes."""
        # Pass 1: compute offsets (sizes are already known per-node).
        labels = {}
        offset = 0
        for node in self._nodes:
            if node.label is not None:
                labels[node.label] = offset
            offset += node.size

        # Pass 2: emit for real, now that every label is resolved.
        buf = NativeBuffer()
        offset = 0
        for node in self._nodes:
            if node.label is None:
                node.emit(buf, offset, labels)
            offset += node.size
        return buf.to_bytes()
