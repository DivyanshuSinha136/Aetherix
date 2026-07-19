"""
Low-level ctypes bridge to the native Aetherix encoder core.

End users should not need to touch this module directly — it backs the
high-level, Pythonic builder API in `aetherix.boot`, `aetherix.kernel`,
and `aetherix.drivers`.
"""
from __future__ import annotations

import ctypes
import os
import platform
import subprocess
import sys
from pathlib import Path

_NATIVE_DIR = Path(__file__).resolve().parent.parent / "native"


def _lib_filename() -> str:
    system = platform.system()
    if system == "Windows":
        return "aetherix_native.dll"
    if system == "Darwin":
        return "libaetherix_native.dylib"
    return "libaetherix_native.so"


def _candidate_paths():
    name = _lib_filename()
    yield Path(__file__).resolve().parent / name          # bundled next to package
    yield _NATIVE_DIR / name                                # dev checkout
    yield _NATIVE_DIR / "libaetherix.so"                    # dev quick-build name


def _compile_native() -> Path:
    """Best-effort on-the-fly compile for source/dev installs."""
    out = _NATIVE_DIR / _lib_filename()
    cc = os.environ.get("CC") or ("cl" if platform.system() == "Windows" else "gcc")
    src = _NATIVE_DIR / "encoder.c"
    if not src.exists():
        raise FileNotFoundError(f"Native encoder source not found at {src}")
    if platform.system() == "Windows" and cc == "cl":
        cmd = ["cl", "/LD", "/O2", str(src), f"/Fe:{out}"]
    else:
        cmd = [cc, "-shared", "-fPIC", "-O2", "-o", str(out), str(src)]
    subprocess.run(cmd, check=True, cwd=str(_NATIVE_DIR))
    return out


def _load() -> ctypes.CDLL:
    for candidate in _candidate_paths():
        if candidate.exists():
            return ctypes.CDLL(str(candidate))
    # Not found anywhere -- try compiling from source (dev/editable installs)
    try:
        built = _compile_native()
        return ctypes.CDLL(str(built))
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Could not locate or build the Aetherix native encoder core. "
            "A C compiler (gcc/clang/cl) is required to build from source. "
            f"Underlying error: {exc}"
        ) from exc


_lib = _load()

# ---------------------------------------------------------------------------
# Signature declarations
# ---------------------------------------------------------------------------

_lib.aeth_buf_new.restype = ctypes.c_void_p
_lib.aeth_buf_new.argtypes = []

_lib.aeth_buf_free.restype = None
_lib.aeth_buf_free.argtypes = [ctypes.c_void_p]

_lib.aeth_buf_len.restype = ctypes.c_size_t
_lib.aeth_buf_len.argtypes = [ctypes.c_void_p]

_lib.aeth_buf_copy.restype = None
_lib.aeth_buf_copy.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t]

_lib.aeth_buf_reset.restype = None
_lib.aeth_buf_reset.argtypes = [ctypes.c_void_p]

_SIMPLE_U8 = [ctypes.c_void_p, ctypes.c_uint8]
_SIMPLE_U16 = [ctypes.c_void_p, ctypes.c_uint16]
_SIMPLE_U32 = [ctypes.c_void_p, ctypes.c_uint32]

_FUNC_SIGS = {
    "aeth_db": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth_dw": [ctypes.c_void_p, ctypes.c_uint16],
    "aeth_dd": [ctypes.c_void_p, ctypes.c_uint32],
    "aeth_cli": [ctypes.c_void_p],
    "aeth_sti": [ctypes.c_void_p],
    "aeth_hlt": [ctypes.c_void_p],
    "aeth_nop": [ctypes.c_void_p],
    "aeth_cld": [ctypes.c_void_p],
    "aeth_ret": [ctypes.c_void_p],
    "aeth_mov_r16_imm16": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint16],
    "aeth_mov_r8_imm8": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_mov_sreg_r16": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_mov_r16_sreg": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_mov_r16_r16": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_mov_r8_r8": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_xor_r16_r16": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_push_r16": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth_pop_r16": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth_int": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth_mov_mem16_r16": [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint8],
    "aeth_mov_r16_mem16": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint16],
    "aeth_cmp_r8_imm8": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_cmp_r16_imm16": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint16],
    "aeth_test_al_imm8": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth_or_al_imm8": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth_and_al_imm8": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth_jmp_rel8": [ctypes.c_void_p, ctypes.c_int8],
    "aeth_jmp_rel16": [ctypes.c_void_p, ctypes.c_int16],
    "aeth_jcc_rel8": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_int8],
    "aeth_in_al_imm8": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth_out_imm8_al": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth_in_al_dx": [ctypes.c_void_p],
    "aeth_out_dx_al": [ctypes.c_void_p],
    "aeth_far_jmp16": [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16],
    "aeth_lgdt_mem16": [ctypes.c_void_p, ctypes.c_uint16],
    "aeth_mov_eax_cr0": [ctypes.c_void_p],
    "aeth_mov_cr0_eax": [ctypes.c_void_p],
    "aeth_or_eax_imm32": [ctypes.c_void_p, ctypes.c_uint32],
    "aeth_mov_r32_imm32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32],
    "aeth_mov_r32_r32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_mov_mem32_r32": [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8],
    "aeth_mov_r32_mem32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32],
    "aeth_mov_mem8_imm8": [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8],
    "aeth_mov_mem8_r8": [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8],
    "aeth_mov_r8_mem8": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32],
    "aeth_add_r32_imm32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32],
    "aeth_sub_r32_imm32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32],
    "aeth_cmp_r32_imm32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32],
    "aeth_inc_r32": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth_jmp_rel32": [ctypes.c_void_p, ctypes.c_int32],
    "aeth_jcc_rel32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_int32],
    "aeth_call_rel32": [ctypes.c_void_p, ctypes.c_int32],
    "aeth_hlt32": [ctypes.c_void_p],
    "aeth_mov_r8_mem_si": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth_inc_r16": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth_mov_r8_mem_reg32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_mov_mem_reg32_r8": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_mov_mem_reg32_imm8": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_mov_mem_reg32_disp8_r8": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_int8, ctypes.c_uint8],
    "aeth_mov_mem_reg32_disp8_imm8": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_int8, ctypes.c_uint8],
    "aeth_add_r32_r32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_sub_r32_r32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth_out_dx_ax16": [ctypes.c_void_p],
    "aeth64_push_r64": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth64_pop_r64": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth64_ret": [ctypes.c_void_p],
    "aeth64_nop": [ctypes.c_void_p],
    "aeth64_hlt": [ctypes.c_void_p],
    "aeth64_mov_r64_imm64": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint64],
    "aeth64_mov_r32_imm32_zx": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32],
    "aeth64_mov_r64_r64": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth64_mov_mem_r64": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_int32, ctypes.c_uint8],
    "aeth64_mov_r64_mem": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_int32],
    "aeth64_movzx_r64_mem16": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_int32],
    "aeth64_lea_r64_rip": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_int32],
    "aeth64_call_r64": [ctypes.c_void_p, ctypes.c_uint8],
    "aeth64_add_r64_imm32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32],
    "aeth64_sub_r64_imm32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32],
    "aeth64_cmp_r64_imm32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint32],
    "aeth64_cmp_r64_r64": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth64_xor_r64_r64": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8],
    "aeth64_jmp_rel32": [ctypes.c_void_p, ctypes.c_int32],
    "aeth64_jcc_rel32": [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_int32],
    "aeth_dbytes": [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t],
}

for _name, _argtypes in _FUNC_SIGS.items():
    _fn = getattr(_lib, _name)
    _fn.argtypes = _argtypes
    _fn.restype = None


class NativeBuffer:
    """Thin Pythonic wrapper around a native growable byte buffer."""

    def __init__(self):
        self._ptr = _lib.aeth_buf_new()
        if not self._ptr:
            raise MemoryError("Failed to allocate native encoder buffer")

    def __del__(self):
        try:
            if getattr(self, "_ptr", None):
                _lib.aeth_buf_free(self._ptr)
        except Exception:
            pass

    def __len__(self) -> int:
        return _lib.aeth_buf_len(self._ptr)

    def to_bytes(self) -> bytes:
        n = len(self)
        arr = (ctypes.c_uint8 * n)()
        _lib.aeth_buf_copy(self._ptr, arr, n)
        return bytes(arr)

    def reset(self):
        _lib.aeth_buf_reset(self._ptr)

    def call(self, fn_name: str, *args):
        getattr(_lib, fn_name)(self._ptr, *args)

    def dbytes(self, data: bytes):
        arr = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        _lib.aeth_dbytes(self._ptr, arr, len(data))
