"""
Bootable disk image writer.

Produces a flat, raw disk image: boot sector, then kernel sectors, then an
optional AVFS data archive, then padding to the requested total size. This
is the same "flat binary" layout `dd if=image.bin of=/dev/sdX` expects, and
what QEMU (`-drive file=image.bin,format=raw`), VirtualBox, and VMware all
accept as a raw/fixed-size virtual disk.

Sizes offered by `SIZE_PRESETS` are the conventional ones people reach for
first; any explicit byte count also works.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

SECTOR_SIZE = 512

SIZE_PRESETS = {
    "floppy_1_44mb": 1_474_560,
    "floppy_2_88mb": 2_949_120,
    "hdd_10mb": 10 * 1024 * 1024,
    "hdd_64mb": 64 * 1024 * 1024,
    "hdd_128mb": 128 * 1024 * 1024,
}


class DiskImage:
    def __init__(self):
        self._boot_sector: Optional[bytes] = None
        self._kernel: Optional[bytes] = None
        self._fs_archive: Optional[bytes] = None

    def set_boot_sector(self, data: bytes) -> "DiskImage":
        if len(data) != SECTOR_SIZE:
            raise ValueError(f"Boot sector must be exactly {SECTOR_SIZE} bytes, got {len(data)}")
        if data[-2:] != b"\x55\xaa":
            raise ValueError("Boot sector is missing the 0x55 0xAA boot signature in its last 2 bytes")
        self._boot_sector = data
        return self

    def set_kernel(self, data: bytes) -> "DiskImage":
        self._kernel = data
        return self

    def set_filesystem(self, data: bytes) -> "DiskImage":
        self._fs_archive = data
        return self

    def _kernel_sectors(self) -> bytes:
        if not self._kernel:
            return b""
        padded_len = ((len(self._kernel) + SECTOR_SIZE - 1) // SECTOR_SIZE) * SECTOR_SIZE
        return self._kernel + bytes(padded_len - len(self._kernel))

    def build(self, total_size: Union[int, str, None] = "floppy_1_44mb") -> bytes:
        if self._boot_sector is None:
            raise RuntimeError("No boot sector set -- call set_boot_sector() first")

        image = bytearray(self._boot_sector)
        image += self._kernel_sectors()
        if self._fs_archive:
            image += self._fs_archive

        if total_size is not None:
            size_bytes = SIZE_PRESETS[total_size] if isinstance(total_size, str) else total_size
            if len(image) > size_bytes:
                raise ValueError(
                    f"Assembled image is {len(image)} bytes, larger than the "
                    f"requested total size of {size_bytes} bytes. Pick a larger "
                    "preset/size, or shrink the kernel/filesystem contents."
                )
            image += bytes(size_bytes - len(image))

        return bytes(image)

    def write(self, path: Union[str, Path], total_size: Union[int, str, None] = "floppy_1_44mb") -> Path:
        data = self.build(total_size=total_size)
        out = Path(path)
        out.write_bytes(data)
        return out
