"""
EFI_GUID encoding, shared by gpt.py (partition type/unique GUIDs) and
protocol.py/boot_services.py (protocol GUIDs for LocateProtocol).

UEFI stores GUIDs in mixed-endian form: the first three fields
little-endian, the last two (clock_seq + node) big-endian, following
Microsoft's original GUID struct layout -- not the same byte order as a
GUID's canonical string form (e.g. "9042A9DE-23DC-4A38-96FB-7ADED080516A").
"""
from __future__ import annotations

import struct
import uuid
from typing import Union


def guid_bytes(value: Union[str, uuid.UUID]) -> bytes:
    """Encode a GUID (canonical string or uuid.UUID) as 16 bytes in the
    mixed-endian order UEFI structures expect."""
    guid = value if isinstance(value, uuid.UUID) else uuid.UUID(value)
    time_low, time_mid, time_hi_version, clock_seq_hi, clock_seq_lo, node = guid.fields
    return (
        struct.pack("<IHH", time_low, time_mid, time_hi_version)
        + bytes([clock_seq_hi, clock_seq_lo])
        + node.to_bytes(6, "big")
    )
