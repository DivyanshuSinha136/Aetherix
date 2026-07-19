"""
UTF-8 <-> VGA text-mode (CP437) conversion.

Real VGA/BIOS text-mode hardware displays one glyph per byte from a fixed,
built-in 256-glyph ROM font -- code page 437 (the original IBM PC font) --
on essentially every PC-compatible system and every mainstream emulator.
There's no dynamic font loading in text mode: just that one baked-in
glyph set. A byte value doesn't mean "this Unicode codepoint"; it means
"glyph number N in the ROM font", and the mapping from Unicode to glyph
number is CP437, not identity.

Aetherix accepts UTF-8 Python strings everywhere text is involved --
source code, file names, file contents, and messages you ask it to print
-- and converts to the right representation for where that text is going:

  - File names/contents (AVFS): stored as their real UTF-8 bytes, since
    that's just data on disk, not something VGA hardware renders.
  - Anything printed to the screen (BIOS teletype, VGA text buffer): each
    Unicode codepoint is mapped to its corresponding CP437 byte -- e.g.
    'é' (U+00E9) becomes byte 0x82, *not* its raw codepoint value 0xE9
    (which is a different, unrelated glyph in CP437). Codepoints with no
    CP437 glyph (most of Unicode -- CJK, emoji, etc.) are replaced with
    '?' by default, since there is no way to display a glyph the ROM font
    doesn't contain without switching to graphics mode and rendering your
    own bitmap font (out of scope here -- see HAL.scaffolded()'s 'gpu'
    entry).

Python's standard library already ships the CP437 codec (the same
"IBM437"/"cp437" codec used for many legacy file formats and verified
here to match the real VGA ROM font mapping), so this module is a thin,
VGA-specific wrapper around `str.encode("cp437", ...)` rather than a
hand-maintained mapping table.
"""
from __future__ import annotations

VGA_CODEC = "cp437"
REPLACEMENT_BYTE = 0x3F  # '?'


def to_vga_bytes(text: str, errors: str = "replace") -> bytes:
    """Encode `text` to the single-byte-per-glyph representation real VGA
    text-mode hardware expects (code page 437).

    `errors` follows the same semantics as `str.encode`:
      - 'replace' (default): unmappable characters become '?' (0x3F)
      - 'strict': raises UnicodeEncodeError on an unmappable character
      - 'ignore': unmappable characters are dropped entirely
    """
    return text.encode(VGA_CODEC, errors=errors)


def from_vga_bytes(data: bytes) -> str:
    """Decode raw VGA text-mode bytes (e.g. read back from an emulator's
    memory, or from a screen dump) into the Unicode text they represent."""
    return data.decode(VGA_CODEC)


def displayable(text: str) -> bool:
    """True if every character in `text` has a CP437 glyph and can be
    printed to a real VGA screen without falling back to '?'."""
    try:
        text.encode(VGA_CODEC, errors="strict")
        return True
    except UnicodeEncodeError:
        return False


def undisplayable_chars(text: str) -> str:
    """Return the distinct characters in `text` that have no CP437 glyph
    (and would print as '?'), in order of first appearance. Empty string
    if everything in `text` is displayable."""
    seen = []
    for ch in text:
        if ch not in seen and not displayable(ch):
            seen.append(ch)
    return "".join(seen)
