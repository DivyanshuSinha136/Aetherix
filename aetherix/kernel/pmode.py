"""
Flat GDT (Global Descriptor Table) construction for entering 32-bit
protected mode. We use the simplest possible memory model: two overlapping
descriptors (code, data) both with base=0, limit=4GB, so linear address ==
physical address everywhere, exactly like real mode addressing but without
the 16-bit segment size limit. This is what lets kernel code do a plain
`mov [0xB8000], al` and have it just work.
"""
from __future__ import annotations

import struct

NULL_SELECTOR = 0x00
CODE_SELECTOR = 0x08
DATA_SELECTOR = 0x10

GDTR_SIZE = 6      # 2-byte limit + 4-byte linear base address
GDT_TABLE_SIZE = 24  # 3 descriptors * 8 bytes


def _descriptor(base: int, limit: int, access: int, flags: int) -> bytes:
    return struct.pack(
        "<HHBBBB",
        limit & 0xFFFF,
        base & 0xFFFF,
        (base >> 16) & 0xFF,
        access & 0xFF,
        ((limit >> 16) & 0x0F) | (flags & 0xF0),
        (base >> 24) & 0xFF,
    )


def build_flat_gdt_table() -> bytes:
    """Null + flat 32-bit code + flat 32-bit data descriptor, 24 bytes total."""
    null_desc = b"\x00" * 8
    # access: present=1, ring0, type=code, executable, readable
    code_desc = _descriptor(0x00000000, 0xFFFFF, access=0x9A, flags=0xC0)
    # access: present=1, ring0, type=data, writable
    data_desc = _descriptor(0x00000000, 0xFFFFF, access=0x92, flags=0xC0)
    table = null_desc + code_desc + data_desc
    assert len(table) == GDT_TABLE_SIZE
    return table


def build_gdtr(gdt_table_linear_addr: int) -> bytes:
    """The 6-byte pseudo-descriptor that `lgdt` loads: limit (size-1) + base."""
    limit = GDT_TABLE_SIZE - 1
    return struct.pack("<HI", limit, gdt_table_linear_addr)
