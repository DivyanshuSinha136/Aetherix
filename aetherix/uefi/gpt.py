"""
GPT (GUID Partition Table) builder.

UEFI firmware discovers bootable media via GPT, not the MBR partition
scheme BIOS uses -- it looks for a partition of type "EFI System
Partition" (a fixed, spec-defined GUID) and mounts it as a FAT
filesystem (see fat32.py) to find \\EFI\\BOOT\\BOOTX64.EFI.

This builds: a protective MBR (required by the GPT spec so BIOS-era
tools don't mistake the disk for unpartitioned), a primary GPT header +
partition entry array right after it, and a backup copy of both at the
end of the disk (also required by spec -- UEFI firmware may refuse a
GPT disk without a valid backup).
"""
from __future__ import annotations

import struct
import uuid
import zlib
from dataclasses import dataclass

from .guid import guid_bytes as _guid_to_bytes

SECTOR_SIZE = 512
GPT_HEADER_SIZE = 92
PARTITION_ENTRY_SIZE = 128
NUM_PARTITION_ENTRIES = 128
PARTITION_ARRAY_SECTORS = (NUM_PARTITION_ENTRIES * PARTITION_ENTRY_SIZE) // SECTOR_SIZE  # 32

EFI_SYSTEM_PARTITION_GUID = uuid.UUID("C12A7328-F81F-11D2-BA4B-00A0C93EC93B")


@dataclass
class GptLayout:
    disk_sectors: int
    esp_start_lba: int
    esp_sectors: int


def build_protective_mbr(disk_sectors: int) -> bytes:
    """A single partition entry of type 0xEE covering the whole disk (or
    as much as an MBR's 32-bit sector count can represent), so tools that
    only understand MBR see one big "GPT protective" partition instead of
    an apparently-blank disk."""
    mbr = bytearray(SECTOR_SIZE)
    covered = min(disk_sectors - 1, 0xFFFFFFFF)
    entry = struct.pack(
        "<BBBBBBBBII",
        0x00,              # status (not bootable)
        0x00, 0x02, 0x00,  # CHS start (dummy -- GPT-aware tools ignore this)
        0xEE,              # partition type: GPT protective
        0xFF, 0xFF, 0xFF,  # CHS end (dummy)
        1,                 # LBA of first sector
        covered,           # number of sectors
    )
    mbr[446:446 + 16] = entry
    mbr[510:512] = b"\x55\xaa"
    return bytes(mbr)


def _build_partition_entry(part_guid: uuid.UUID, first_lba: int, last_lba: int, name: str) -> bytes:
    name_utf16 = name.encode("utf-16-le")
    name_field = name_utf16 + b"\x00" * (72 - len(name_utf16))
    return (
        _guid_to_bytes(EFI_SYSTEM_PARTITION_GUID)
        + _guid_to_bytes(part_guid)
        + struct.pack("<QQQ", first_lba, last_lba, 0)  # attributes = 0
        + name_field
    )


def build_gpt(disk_sectors: int, esp_sectors: int, esp_name: str = "EFI SYSTEM"):
    """Returns (protective_mbr, primary_header, partition_array,
    backup_header, layout) -- all as bytes ready to place at their
    respective LBAs (see `build_disk_image` in diskimage.py)."""
    layout = GptLayout(
        disk_sectors=disk_sectors,
        esp_start_lba=2 + PARTITION_ARRAY_SECTORS,
        esp_sectors=esp_sectors,
    )
    esp_last_lba = layout.esp_start_lba + esp_sectors - 1

    backup_header_lba = disk_sectors - 1
    backup_array_start_lba = backup_header_lba - PARTITION_ARRAY_SECTORS

    disk_guid = uuid.uuid4()
    part_guid = uuid.uuid4()

    entry = _build_partition_entry(part_guid, layout.esp_start_lba, esp_last_lba, esp_name)
    partition_array = entry + b"\x00" * (PARTITION_ENTRY_SIZE * NUM_PARTITION_ENTRIES - len(entry))
    partition_array_crc = zlib.crc32(partition_array) & 0xFFFFFFFF

    first_usable_lba = 2 + PARTITION_ARRAY_SECTORS
    last_usable_lba = backup_array_start_lba - 1

    def _header(my_lba: int, other_lba: int, partition_array_lba: int) -> bytes:
        header = struct.pack(
            "<8sIIIIQQQQ16sQIII",
            b"EFI PART",
            0x00010000,               # Revision 1.0
            GPT_HEADER_SIZE,
            0,                         # HeaderCRC32 -- filled in below
            0,                         # Reserved
            my_lba,
            other_lba,
            first_usable_lba,
            last_usable_lba,
            _guid_to_bytes(disk_guid),
            partition_array_lba,
            NUM_PARTITION_ENTRIES,
            PARTITION_ENTRY_SIZE,
            partition_array_crc,
        )
        header = header + b"\x00" * (SECTOR_SIZE - len(header))
        crc = zlib.crc32(header[:GPT_HEADER_SIZE]) & 0xFFFFFFFF
        header = bytearray(header)
        struct.pack_into("<I", header, 16, crc)
        return bytes(header)

    primary_header = _header(1, backup_header_lba, 2)
    backup_header = _header(backup_header_lba, 1, backup_array_start_lba)

    mbr = build_protective_mbr(disk_sectors)
    return mbr, primary_header, partition_array, backup_header, layout
