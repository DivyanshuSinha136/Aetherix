"""
AVFS -- Aetherix Volume File System.

A deliberately tiny, read-only, build-time file archive: you embed files
(images, text, data, or further AVFS-aware programs) into the disk image
at build time, and the archive's layout is simple enough to parse from
16-bit or 32-bit kernel code without a directory tree, permissions, or
free-space management.

This is an *embedded initrd-style archive*, not a general read/write
filesystem -- Aetherix does not yet include kernel-side code that parses
AVFS (that's a natural place to build your own "file manager" component,
per the HAL/driver extension model). What's here is the storage format
and the Python-side builder for it.

Layout:
    offset 0:  magic        4 bytes   b"AVFS"
    offset 4:  version      1 byte    currently 1
    offset 5:  file_count   1 byte    up to 255 files
    offset 6:  entries      file_count * 40 bytes:
                   name      32 bytes  UTF-8, NUL-padded
                   offset    4 bytes   little-endian, from start of archive
                   size      4 bytes   little-endian, bytes
    offset 6 + file_count*40: raw file data, back-to-back in entry order
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Union

MAGIC = b"AVFS"
VERSION = 1
ENTRY_SIZE = 40
NAME_FIELD_SIZE = 32
MAX_FILES = 255


@dataclass
class _PendingFile:
    name: str
    data: bytes


class Archive:
    def __init__(self):
        self._files: List[_PendingFile] = []

    def add_file(self, name: str, data: Union[bytes, str]) -> "Archive":
        if len(self._files) >= MAX_FILES:
            raise ValueError(f"AVFS archives support at most {MAX_FILES} files")
        name_bytes = name.encode("utf-8")
        if len(name_bytes) > NAME_FIELD_SIZE:
            raise ValueError(
                f"File name '{name}' is {len(name_bytes)} UTF-8 bytes; "
                f"AVFS names are limited to {NAME_FIELD_SIZE} bytes"
            )
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._files.append(_PendingFile(name=name, data=data))
        return self

    def add_path(self, path: Union[str, Path], name: str = None) -> "Archive":
        p = Path(path)
        return self.add_file(name or p.name, p.read_bytes())

    def build(self) -> bytes:
        header = struct.pack("<4sBB", MAGIC, VERSION, len(self._files))
        table_size = len(self._files) * ENTRY_SIZE
        data_start = 6 + table_size

        entries = b""
        blobs = b""
        cursor = data_start
        for f in self._files:
            name_bytes = f.name.encode("utf-8")
            name_field = name_bytes + b"\x00" * (NAME_FIELD_SIZE - len(name_bytes))
            entries += struct.pack("<32sII", name_field, cursor, len(f.data))
            blobs += f.data
            cursor += len(f.data)

        return header + entries + blobs

    def __len__(self) -> int:
        return len(self._files)
