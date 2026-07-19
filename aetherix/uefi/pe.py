"""
Minimal PE32+ ("PE+") writer for UEFI applications.

UEFI firmware doesn't boot a raw flat binary the way BIOS does -- it loads
a properly-formed Portable Executable (PE32+) image, the same file format
Windows uses for 64-bit executables, just with the subsystem field set to
EFI_APPLICATION (10) instead of a Windows subsystem.

This writer produces the smallest PE that's still genuinely valid: DOS
header/stub, COFF file header, PE32+ optional header, one combined
".text" section (code + any embedded data together, matching this
codebase's existing "one blob" approach for BIOS kernels), and --
deliberately -- **no .reloc section**. That's not a shortcut: it's safe
specifically because `aetherix.uefi`'s 64-bit code generation only ever
uses RIP-relative addressing (`Program.lea_rip_label`) for data and
rel32 for jumps/calls to same-image labels, never an absolute address to
its own code or data. Code built that way is already position-independent
byte-for-byte, so there's nothing for a .reloc section to fix up --
adding one would just be dead weight. This only holds because of that
constraint: `aetherix.uefi.app` never emits `Program.mov64` (movabs)
pointed at anything inside the image itself. If you write raw 64-bit
`Program` code by hand and use `mov64` with an address of your own label,
you've broken that invariant and do need real relocations, which this
module does not generate.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

IMAGE_FILE_MACHINE_AMD64 = 0x8664
IMAGE_FILE_EXECUTABLE_IMAGE = 0x0002
IMAGE_FILE_LARGE_ADDRESS_AWARE = 0x0020
IMAGE_FILE_RELOCS_STRIPPED = 0x0001  # safe here -- see module docstring

IMAGE_SUBSYSTEM_EFI_APPLICATION = 10
PE32PLUS_MAGIC = 0x020B

IMAGE_SCN_CNT_CODE = 0x00000020
IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_READ = 0x40000000
IMAGE_SCN_MEM_WRITE = 0x80000000

SECTION_ALIGNMENT = 0x1000
FILE_ALIGNMENT = 0x200
DEFAULT_IMAGE_BASE = 0x400000  # arbitrary; moot since our code is position-independent


def _align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


@dataclass
class PEImage:
    """The bytes of a complete .efi (PE32+ EFI_APPLICATION) file, plus the
    layout info needed to compute label addresses within it."""
    data: bytes
    headers_size: int
    text_rva: int
    text_file_offset: int


def build_pe32plus(code_and_data: bytes, entry_point_offset: int = 0,
                    image_base: int = DEFAULT_IMAGE_BASE) -> PEImage:
    """Wrap `code_and_data` (your assembled 64-bit code, with any embedded
    data placed after it in the same blob -- exactly what
    `Program(bits=64).assemble()` produces) in a minimal valid PE32+
    EFI_APPLICATION image. `entry_point_offset` is the byte offset within
    `code_and_data` where execution should start (0 if your entry
    function is the very first thing you assembled)."""

    num_sections = 1
    dos_header_size = 64
    dos_stub = b""  # no DOS stub needed -- UEFI firmware only reads e_lfanew
    pe_sig_size = 4
    coff_header_size = 20
    optional_header_size = 112 + 16 * 8  # standard fields + 16 data directories
    section_header_size = 40 * num_sections

    e_lfanew = dos_header_size + len(dos_stub)
    headers_size_unaligned = e_lfanew + pe_sig_size + coff_header_size + optional_header_size + section_header_size
    headers_size = _align_up(headers_size_unaligned, FILE_ALIGNMENT)

    text_file_offset = headers_size
    text_virtual_size = len(code_and_data)
    text_raw_size = _align_up(text_virtual_size, FILE_ALIGNMENT)
    text_rva = _align_up(headers_size, SECTION_ALIGNMENT)  # first section starts right after headers

    size_of_image = _align_up(text_rva + text_virtual_size, SECTION_ALIGNMENT)

    # -- DOS header (IMAGE_DOS_HEADER) --
    # e_magic(2s) + 29 reserved/legacy uint16 fields + e_lfanew(uint32)
    dos_header = struct.pack("<2s", b"MZ") + struct.pack("<29H", *([0] * 29)) + struct.pack("<I", e_lfanew)
    assert len(dos_header) == dos_header_size

    pe_signature = b"PE\x00\x00"

    # -- COFF file header (IMAGE_FILE_HEADER) --
    characteristics = (IMAGE_FILE_RELOCS_STRIPPED | IMAGE_FILE_EXECUTABLE_IMAGE
                        | IMAGE_FILE_LARGE_ADDRESS_AWARE)
    coff_header = struct.pack(
        "<HHIIIHH",
        IMAGE_FILE_MACHINE_AMD64,
        num_sections,
        0,              # TimeDateStamp
        0,              # PointerToSymbolTable
        0,              # NumberOfSymbols
        optional_header_size,
        characteristics,
    )
    assert len(coff_header) == coff_header_size

    # -- Optional header (IMAGE_OPTIONAL_HEADER64) --
    entry_rva = text_rva + entry_point_offset
    optional_header = struct.pack(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        PE32PLUS_MAGIC,             # Magic
        0, 0,                       # Major/MinorLinkerVersion
        text_virtual_size,          # SizeOfCode
        0,                          # SizeOfInitializedData
        0,                          # SizeOfUninitializedData
        entry_rva,                  # AddressOfEntryPoint
        text_rva,                   # BaseOfCode
        image_base,                 # ImageBase (uint64)
        SECTION_ALIGNMENT,          # SectionAlignment
        FILE_ALIGNMENT,             # FileAlignment
        0, 0,                       # Major/MinorOperatingSystemVersion
        0, 0,                       # Major/MinorImageVersion
        0, 0,                       # Major/MinorSubsystemVersion
        0,                          # Win32VersionValue
        size_of_image,              # SizeOfImage
        headers_size,               # SizeOfHeaders
        0,                          # CheckSum
        IMAGE_SUBSYSTEM_EFI_APPLICATION,  # Subsystem
        0,                          # DllCharacteristics
        0x100000, 0x1000,          # SizeOfStackReserve, SizeOfStackCommit (uint64)
        0x100000, 0x1000,          # SizeOfHeapReserve, SizeOfHeapCommit (uint64)
        0,                          # LoaderFlags
        16,                         # NumberOfRvaAndSizes
    )
    optional_header += b"\x00" * (8 * 16)  # 16 zeroed IMAGE_DATA_DIRECTORY entries (8 bytes each)
    assert len(optional_header) == optional_header_size, (len(optional_header), optional_header_size)

    # -- Section header (IMAGE_SECTION_HEADER) for ".text" --
    section_characteristics = (IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE
                                | IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE)
    section_header = struct.pack(
        "<8sIIIIIIHHI",
        b".text\x00\x00\x00",
        text_virtual_size,
        text_rva,
        text_raw_size,
        text_file_offset,
        0, 0,   # PointerToRelocations, PointerToLinenumbers
        0, 0,   # NumberOfRelocations, NumberOfLinenumbers
        section_characteristics,
    )
    assert len(section_header) == section_header_size

    headers = dos_header + dos_stub + pe_signature + coff_header + optional_header + section_header
    headers += b"\x00" * (headers_size - len(headers))

    section_data = code_and_data + b"\x00" * (text_raw_size - text_virtual_size)

    image = headers + section_data
    return PEImage(data=image, headers_size=headers_size, text_rva=text_rva,
                    text_file_offset=text_file_offset)
