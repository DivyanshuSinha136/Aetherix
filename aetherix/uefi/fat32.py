"""
FAT32 filesystem builder for the EFI System Partition (ESP).

UEFI firmware finds a bootable application by mounting the ESP (a GPT
partition of a specific type -- see gpt.py) as a FAT filesystem and
looking for \\EFI\\BOOT\\BOOTX64.EFI. This builds a minimal but genuinely
correct FAT32 volume containing exactly that one file.

Uses 512-byte clusters (1 sector/cluster) specifically so a small ESP
still has comfortably more than 65525 clusters -- the threshold
Microsoft's own formatting utilities use to decide "this should be
FAT32" (fewer clusters and strict tools may instead assume FAT16, which
uses a different on-disk layout entirely).

Every name this needs (EFI, BOOT, BOOTX64.EFI) happens to fit the
classic 8.3 short-filename format, so this writes only short directory
entries -- no VFAT long-filename entries, which would need their own
(more complex) directory entry format.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import List

SECTOR_SIZE = 512
SECTORS_PER_CLUSTER = 1
RESERVED_SECTORS = 32
NUM_FATS = 2
ROOT_CLUSTER = 2
FSINFO_SECTOR = 1
BACKUP_BOOT_SECTOR = 6

FAT32_EOC = 0x0FFFFFFF
FAT32_MIN_CLUSTERS = 65525  # below this, tools should treat it as FAT16 instead

ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20


def _fat_date_time(dt=None):
    dt = dt or time.gmtime()
    date = ((max(dt.tm_year, 1980) - 1980) << 9) | (dt.tm_mon << 5) | dt.tm_mday
    tm = (dt.tm_hour << 11) | (dt.tm_min << 5) | (dt.tm_sec // 2)
    return date & 0xFFFF, tm & 0xFFFF


def _dir_entry(name: str, ext: str, attrs: int, cluster: int, size: int) -> bytes:
    name_field = name.upper().ljust(8)[:8].encode("ascii")
    ext_field = ext.upper().ljust(3)[:3].encode("ascii")
    date, tm = _fat_date_time()
    return struct.pack(
        "<8s3sBBBHHHHHHHI",
        name_field, ext_field,
        attrs,
        0,          # reserved (NT case flags)
        0,          # CreateTimeTenth
        tm, date,   # CreateTime, CreateDate
        date,       # LastAccessDate
        (cluster >> 16) & 0xFFFF,  # FirstClusterHigh
        tm, date,   # WriteTime, WriteDate
        cluster & 0xFFFF,          # FirstClusterLow
        size,
    )


@dataclass
class Fat32Volume:
    data: bytes
    total_sectors: int


def build_fat32_esp(file_data: bytes, file_name: str = "BOOTX64.EFI",
                     total_sectors: int = None, volume_label: str = "EFI SYSTEM") -> Fat32Volume:
    """Builds a complete FAT32 volume (boot sector, FSInfo, two FAT
    copies, root directory, EFI/ and EFI/BOOT/ subdirectories, and the
    file itself) containing \\EFI\\BOOT\\<file_name> = file_data.

    `total_sectors` defaults to just enough to comfortably clear the
    FAT32-vs-FAT16 cluster-count threshold (a bit over 64MiB) -- pass a
    larger value if you plan to add more files later.
    """
    if total_sectors is None:
        total_sectors = FAT32_MIN_CLUSTERS + 4096  # headroom past the FAT32 threshold
        total_sectors += total_sectors % 2  # keep it even (2-sector alignment nicety)

    # -- Lay out clusters --
    # Cluster 2: root dir (contains "EFI")
    # Cluster 3: EFI dir (contains "BOOT", ".", "..")
    # Cluster 4: BOOT dir (contains "BOOTX64.EFI", ".", "..")
    # Cluster 5+: file data
    root_cluster = 2
    efi_dir_cluster = 3
    boot_dir_cluster = 4
    file_start_cluster = 5
    file_clusters_needed = max(1, (len(file_data) + SECTOR_SIZE - 1) // SECTOR_SIZE)
    file_end_cluster = file_start_cluster + file_clusters_needed - 1
    highest_used_cluster = file_end_cluster

    total_clusters_available = (total_sectors - RESERVED_SECTORS) // SECTORS_PER_CLUSTER  # rough, refined below
    if highest_used_cluster + 1 > total_clusters_available:
        raise ValueError(
            f"file needs {file_clusters_needed} clusters but the volume "
            f"(total_sectors={total_sectors}) is too small; increase total_sectors"
        )

    # FAT size (sectors per FAT), computed from data-area cluster count.
    data_sectors_guess = total_sectors - RESERVED_SECTORS
    clusters_guess = data_sectors_guess // SECTORS_PER_CLUSTER
    fat_size_sectors = ((clusters_guess + 2) * 4 + SECTOR_SIZE - 1) // SECTOR_SIZE
    # Refine: data area shrinks once the FATs are accounted for.
    for _ in range(4):
        data_sectors = total_sectors - RESERVED_SECTORS - NUM_FATS * fat_size_sectors
        clusters = data_sectors // SECTORS_PER_CLUSTER
        new_fat_size = ((clusters + 2) * 4 + SECTOR_SIZE - 1) // SECTOR_SIZE
        if new_fat_size == fat_size_sectors:
            break
        fat_size_sectors = new_fat_size

    if clusters < FAT32_MIN_CLUSTERS:
        raise ValueError(
            f"volume only has {clusters} clusters; needs >= {FAT32_MIN_CLUSTERS} "
            "to be unambiguously recognized as FAT32 -- increase total_sectors"
        )

    volume_id = 0x12345678

    # -- Boot sector (BPB) --
    boot_sector = bytearray(SECTOR_SIZE)
    boot_sector[0:3] = b"\xEB\x58\x90"
    boot_sector[3:11] = b"MSWIN4.1"
    struct.pack_into("<H", boot_sector, 11, SECTOR_SIZE)     # BytesPerSector
    boot_sector[13] = SECTORS_PER_CLUSTER
    struct.pack_into("<H", boot_sector, 14, RESERVED_SECTORS)
    boot_sector[16] = NUM_FATS
    struct.pack_into("<H", boot_sector, 17, 0)               # RootEntryCount (0 for FAT32)
    struct.pack_into("<H", boot_sector, 19, 0)               # TotalSectors16 (0, use 32-bit field)
    boot_sector[21] = 0xF8                                    # Media: fixed disk
    struct.pack_into("<H", boot_sector, 22, 0)               # FATSize16 (0 for FAT32)
    struct.pack_into("<H", boot_sector, 24, 32)              # SectorsPerTrack
    struct.pack_into("<H", boot_sector, 26, 64)              # NumHeads
    struct.pack_into("<I", boot_sector, 28, 0)               # HiddenSectors (partition-relative image)
    struct.pack_into("<I", boot_sector, 32, total_sectors)   # TotalSectors32
    struct.pack_into("<I", boot_sector, 36, fat_size_sectors)  # FATSize32
    struct.pack_into("<H", boot_sector, 40, 0)               # ExtFlags
    struct.pack_into("<H", boot_sector, 42, 0)               # FSVersion
    struct.pack_into("<I", boot_sector, 44, root_cluster)
    struct.pack_into("<H", boot_sector, 48, FSINFO_SECTOR)
    struct.pack_into("<H", boot_sector, 50, BACKUP_BOOT_SECTOR)
    boot_sector[64] = 0x80                                    # DriveNumber
    boot_sector[66] = 0x29                                    # BootSignature (extended)
    struct.pack_into("<I", boot_sector, 67, volume_id)
    boot_sector[71:82] = volume_label.upper().ljust(11)[:11].encode("ascii")
    boot_sector[82:90] = b"FAT32   "
    boot_sector[510:512] = b"\x55\xaa"

    # -- FSInfo sector --
    fsinfo = bytearray(SECTOR_SIZE)
    struct.pack_into("<I", fsinfo, 0, 0x41615252)
    struct.pack_into("<I", fsinfo, 484, 0x61417272)
    struct.pack_into("<I", fsinfo, 488, 0xFFFFFFFF)  # free cluster count: unknown
    struct.pack_into("<I", fsinfo, 492, 0xFFFFFFFF)  # next free cluster: unknown
    struct.pack_into("<I", fsinfo, 508, 0xAA550000)

    # -- FAT table --
    fat = bytearray(fat_size_sectors * SECTOR_SIZE)

    def set_fat_entry(cluster: int, value: int):
        struct.pack_into("<I", fat, cluster * 4, value & 0x0FFFFFFF)

    set_fat_entry(0, 0x0FFFFFF8)
    set_fat_entry(1, 0x0FFFFFFF)
    set_fat_entry(root_cluster, FAT32_EOC)
    set_fat_entry(efi_dir_cluster, FAT32_EOC)
    set_fat_entry(boot_dir_cluster, FAT32_EOC)
    for i in range(file_clusters_needed):
        c = file_start_cluster + i
        set_fat_entry(c, FAT32_EOC if i == file_clusters_needed - 1 else c + 1)

    # -- Directory clusters --
    root_dir = _dir_entry("EFI", "", ATTR_DIRECTORY, efi_dir_cluster, 0)
    root_dir += b"\x00" * (SECTOR_SIZE - len(root_dir))

    efi_dir = (
        _dir_entry(".", "", ATTR_DIRECTORY, efi_dir_cluster, 0)
        + _dir_entry("..", "", ATTR_DIRECTORY, 0, 0)  # ".." to root -> conventionally cluster 0
        + _dir_entry("BOOT", "", ATTR_DIRECTORY, boot_dir_cluster, 0)
    )
    efi_dir += b"\x00" * (SECTOR_SIZE - len(efi_dir))

    file_short_name, _, file_short_ext = file_name.upper().partition(".")
    boot_dir = (
        _dir_entry(".", "", ATTR_DIRECTORY, boot_dir_cluster, 0)
        + _dir_entry("..", "", ATTR_DIRECTORY, efi_dir_cluster, 0)
        + _dir_entry(file_short_name, file_short_ext, ATTR_ARCHIVE, file_start_cluster, len(file_data))
    )
    boot_dir += b"\x00" * (SECTOR_SIZE - len(boot_dir))

    file_padded = file_data + b"\x00" * (file_clusters_needed * SECTOR_SIZE - len(file_data))

    # -- Assemble the volume --
    out = bytearray(total_sectors * SECTOR_SIZE)
    out[0:SECTOR_SIZE] = boot_sector
    out[FSINFO_SECTOR * SECTOR_SIZE:(FSINFO_SECTOR + 1) * SECTOR_SIZE] = fsinfo
    out[BACKUP_BOOT_SECTOR * SECTOR_SIZE:(BACKUP_BOOT_SECTOR + 1) * SECTOR_SIZE] = boot_sector

    fat1_off = RESERVED_SECTORS * SECTOR_SIZE
    fat2_off = fat1_off + fat_size_sectors * SECTOR_SIZE
    out[fat1_off:fat1_off + len(fat)] = fat
    out[fat2_off:fat2_off + len(fat)] = fat

    data_area_off = fat2_off + fat_size_sectors * SECTOR_SIZE

    def cluster_offset(cluster: int) -> int:
        return data_area_off + (cluster - 2) * SECTORS_PER_CLUSTER * SECTOR_SIZE

    out[cluster_offset(root_cluster):cluster_offset(root_cluster) + SECTOR_SIZE] = root_dir
    out[cluster_offset(efi_dir_cluster):cluster_offset(efi_dir_cluster) + SECTOR_SIZE] = efi_dir
    out[cluster_offset(boot_dir_cluster):cluster_offset(boot_dir_cluster) + SECTOR_SIZE] = boot_dir
    file_off = cluster_offset(file_start_cluster)
    out[file_off:file_off + len(file_padded)] = file_padded

    return Fat32Volume(data=bytes(out), total_sectors=total_sectors)
