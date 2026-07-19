"""
Combines the GPT partition table (gpt.py) and a FAT32 ESP (fat32.py) into
one complete, bootable raw disk image -- the UEFI equivalent of
`aetherix.image.diskimage` (which builds BIOS/MBR-style images).

Layout:

    LBA 0            protective MBR
    LBA 1            primary GPT header
    LBA 2-33         primary partition entry array
    LBA 34..N-35     EFI System Partition (FAT32, contains
                      \\EFI\\BOOT\\BOOTX64.EFI)
    LBA N-34..N-2     backup partition entry array
    LBA N-1          backup GPT header
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

from .fat32 import build_fat32_esp
from .gpt import build_gpt, SECTOR_SIZE, PARTITION_ARRAY_SECTORS


def build_disk_image(efi_data: bytes, total_size: int = None) -> bytes:
    """Builds a complete GPT+FAT32 disk image containing `efi_data` as
    \\EFI\\BOOT\\BOOTX64.EFI. `total_size` defaults to a bit over 64MiB
    (comfortably past the FAT32 cluster-count threshold -- see
    fat32.py); pass a larger value if you plan to add more files to the
    ESP later via a custom build."""
    esp_vol = build_fat32_esp(efi_data)
    esp_sectors = esp_vol.total_sectors

    # Disk = MBR(1) + primary header(1) + primary array(32) + ESP + backup array(32) + backup header(1)
    disk_sectors = 1 + 1 + PARTITION_ARRAY_SECTORS + esp_sectors + PARTITION_ARRAY_SECTORS + 1
    if total_size is not None:
        requested_sectors = total_size // SECTOR_SIZE
        if requested_sectors < disk_sectors:
            raise ValueError(
                f"total_size is too small to fit the ESP ({disk_sectors * SECTOR_SIZE} "
                f"bytes needed, {total_size} given)"
            )
        disk_sectors = requested_sectors

    mbr, primary_header, partition_array, backup_header, layout = build_gpt(disk_sectors, esp_sectors)

    disk = bytearray(disk_sectors * SECTOR_SIZE)
    disk[0:SECTOR_SIZE] = mbr
    disk[1 * SECTOR_SIZE:2 * SECTOR_SIZE] = primary_header
    disk[2 * SECTOR_SIZE:(2 + PARTITION_ARRAY_SECTORS) * SECTOR_SIZE] = partition_array

    esp_off = layout.esp_start_lba * SECTOR_SIZE
    disk[esp_off:esp_off + len(esp_vol.data)] = esp_vol.data

    backup_array_lba = disk_sectors - 1 - PARTITION_ARRAY_SECTORS
    backup_array_off = backup_array_lba * SECTOR_SIZE
    disk[backup_array_off:backup_array_off + len(partition_array)] = partition_array
    disk[(disk_sectors - 1) * SECTOR_SIZE:disk_sectors * SECTOR_SIZE] = backup_header

    return bytes(disk)


def write_disk_image(efi_data: bytes, path: Union[str, Path], total_size: int = None) -> Path:
    data = build_disk_image(efi_data, total_size=total_size)
    out = Path(path)
    out.write_bytes(data)
    return out
