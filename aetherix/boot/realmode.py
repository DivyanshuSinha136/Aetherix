"""
Real-mode (16-bit) bootloader builder.

A `BootSector` produces exactly 512 bytes ending in the mandatory 0x55 0xAA
signature that BIOS requires to treat a disk as bootable. The default
`standard()` build:

  1. Sets up segment registers and a stack (CS:IP is 0x0000:0x7C00 when
     BIOS hands off control -- that's where every x86 PC starts booting).
  2. Optionally prints a boot message via BIOS teletype (int 0x10).
  3. Verifies BIOS INT 13h extensions are available, then loads
     `kernel_sectors` sectors (512 bytes each) from disk into memory
     immediately following the boot sector (0x0000:0x7E00), using an LBA
     extended read (int 0x13, ah=0x42) -- this avoids CHS geometry/track-
     boundary issues entirely, unlike a single CHS read call.
  4. Jumps to the loaded code.

For custom bootloaders, use `BootSector()` directly and drive `self.prog`
(an `aetherix.asm.Program`) yourself -- every BIOS call and register is
exposed, nothing is hidden.
"""
from __future__ import annotations

from .. import regs
from ..asm import Program

KERNEL_LOAD_SEGMENT = 0x0000
KERNEL_LOAD_OFFSET = 0x7E00  # immediately after the 512-byte boot sector
BOOT_SECTOR_LOAD_ADDR = 0x7C00  # where BIOS always loads/jumps to the boot sector
BOOT_SECTOR_SIZE = 512
BOOT_SIGNATURE = (0x55, 0xAA)


class BootSector:
    def __init__(self):
        self.prog = Program(bits=16)
        self._str_counter = 0

    def setup_segments(self, stack_top: int = 0x7C00) -> "BootSector":
        p = self.prog
        p.cli()
        p.xor16(regs.AX, regs.AX)
        p.mov_sreg(regs.DS, regs.AX)
        p.mov_sreg(regs.ES, regs.AX)
        p.mov_sreg(regs.SS, regs.AX)
        p.mov16(regs.SP, stack_top)
        p.sti()
        return self

    def _print_asciiz(self, text: str) -> "BootSector":
        """Print `text` (plus CRLF) using a compact runtime loop over an
        inline null-terminated string, rather than unrolling one BIOS call
        per character. Fixed ~26-byte loop overhead plus 1 byte per
        character -- important in a 512-byte boot sector, where the naive
        unrolled approach costs 8 bytes per character."""
        p = self.prog
        self._str_counter += 1
        tag = self._str_counter
        data_label = f"__str_{tag}"
        loop_label = f"__str_{tag}_loop"
        done_label = f"__str_{tag}_done"
        skip_label = f"__str_{tag}_skip"

        p.push16(regs.SI)
        p.mov16_label(regs.SI, data_label, base=BOOT_SECTOR_LOAD_ADDR)
        p.label(loop_label)
        p.load8_si(regs.AL)
        p.cmp8(regs.AL, 0)
        p.jcc(regs.JZ, done_label)
        p.mov8(regs.AH, 0x0E)
        p.mov8(regs.BH, 0x00)
        p.interrupt(0x10)
        p.inc16(regs.SI)
        p.jmp(loop_label)
        p.label(done_label)
        p.pop16(regs.SI)
        p.jmp(skip_label)
        p.label(data_label)
        p.ascii_cp437(text + "\r\n", zero_terminate=True)
        p.label(skip_label)
        return self

    def print_message(self, text: str) -> "BootSector":
        """Print `text` (plus CRLF) using BIOS teletype output (int 0x10, ah=0x0E)."""
        return self._print_asciiz(text)

    def enable_a20(self) -> "BootSector":
        """Fast A20 gate via port 0x92 (widely supported by real chipsets
        and by every mainstream emulator). Needed for correctness once
        code/data start living above the 1MB boundary."""
        p = self.prog
        p.in_al(0x92)
        p.or_al(0x02)
        p.and_al(0xFE)  # keep bit0 (fast reset) clear -- avoids spurious reboot on some chipsets
        p.out_al(0x92)
        return self

    def load_kernel_chs(self, sectors: int, start_sector: int = 2,
                         head: int = 0, cylinder: int = 0) -> "BootSector":
        """Load `sectors` sectors (512 bytes each) starting at CHS
        (cylinder, head, start_sector) into KERNEL_LOAD_SEGMENT:KERNEL_LOAD_OFFSET,
        via BIOS int 0x13, ah=0x02. Uses DL as-is (BIOS leaves the boot
        drive number there at boot time, and nothing in this method writes
        to DL, so no save/restore is needed).

        CAUTION -- this makes a single int 0x13 call for all `sectors` at
        once. A single CHS read call cannot cross a BIOS track boundary:
        if `start_sector - 1 + sectors` exceeds the disk's sectors-per-track
        (which depends on how the BIOS/emulator geometry-translates the
        disk -- not something you generally control), the call fails and
        this has no retry-with-smaller-chunks logic. That failure mode is
        silent by design of the retry loop (it just spins), which is
        exactly the bug that motivated `load_kernel_lba` -- **use that
        instead unless you specifically need CHS** (e.g. targeting a BIOS
        old enough to lack INT 13h extensions).
        """
        p = self.prog
        p.label("__load_retry")
        p.mov8(regs.AH, 0x00)  # reset disk system (DL untouched by this call)
        p.interrupt(0x13)

        p.mov16(regs.BX, KERNEL_LOAD_OFFSET)
        p.mov_sreg(regs.ES, regs.AX)  # ES was zeroed in setup_segments; harmless if re-run
        p.mov8(regs.AH, 0x02)          # BIOS read sectors
        p.mov8(regs.AL, sectors & 0xFF)
        p.mov8(regs.CH, cylinder & 0xFF)
        p.mov8(regs.CL, start_sector & 0xFF)
        p.mov8(regs.DH, head & 0xFF)   # DL (drive number) is left as-is
        p.interrupt(0x13)
        # Carry flag set on BIOS disk error -- retry (transient read errors
        # are common on real floppy/optical media; this loops until the
        # read succeeds rather than silently continuing with garbage data).
        p.jcc(regs.JC, "__load_retry")
        p.label("__load_done")
        return self

    def load_kernel_lba(self, sectors: int, start_lba: int = 1, chunk_sectors: int = 64) -> "BootSector":
        """Load `sectors` sectors (512 bytes each) starting at logical
        block address `start_lba` (default 1 -- the sector right after the
        boot sector) into memory starting at KERNEL_LOAD_SEGMENT:KERNEL_LOAD_OFFSET,
        using the INT 13h Extensions "extended read" call (ah=0x42) with a
        Disk Address Packet. Unlike CHS reads, this has no track-boundary
        limitation and needs no disk geometry at all -- it's what real
        bootloaders (GRUB included) use whenever possible, and this is the
        method `standard()` uses by default.

        Reads happen in `chunk_sectors`-sized pieces (default 64 = 32KB),
        advancing the destination by *segment* between chunks rather than
        by offset. This matters: a single DAP call's buffer offset is only
        16 bits, so one read of more than about 65 sectors starting at
        KERNEL_LOAD_OFFSET (0x7E00) would need an offset past 0xFFFF,
        silently wrapping within the segment on real hardware/BIOS (or
        being rejected outright, which is what a real SeaBIOS does -- this
        is exactly the bug that caused a build to hang right after the
        boot message on real QEMU despite passing this project's own
        emulation-based tests, which didn't model the wraparound and gave
        false confidence). Each chunk instead always uses buffer offset 0
        and a different segment, which has no such limit.

        Uses DL as-is throughout (BIOS leaves the boot drive number there
        at boot time, and nothing in this method writes to DL).

        Checks that the BIOS supports INT 13h extensions first (ah=0x41);
        every BIOS shipped in the last ~25+ years and every mainstream
        emulator (QEMU/SeaBIOS, VirtualBox, VMware) supports this, but on
        the rare system that doesn't, this prints a clear fatal message and
        halts instead of silently hanging.
        """
        p = self.prog

        # -- Check for INT 13h extensions (LBA support) --
        p.mov8(regs.AH, 0x41)
        p.mov16(regs.BX, 0x55AA)
        p.interrupt(0x13)
        p.jcc(regs.JC, "__no_lba")
        p.jmp("__lba_ok")
        p.label("__no_lba")
        self._print_asciiz("FATAL: BIOS lacks LBA disk support.")
        p.label("__no_lba_halt")
        p.hlt()
        p.jmp("__no_lba_halt")
        p.label("__lba_ok")

        # KERNEL_LOAD_OFFSET is paragraph-aligned (0x7E00 / 16 = 0x7E0
        # exactly), so every chunk can use buffer offset 0 and let the
        # segment alone carry the address -- no offset tracking needed.
        assert KERNEL_LOAD_OFFSET % 16 == 0, "KERNEL_LOAD_OFFSET must be paragraph-aligned"
        initial_segment = KERNEL_LOAD_OFFSET // 16
        paragraphs_per_chunk = (chunk_sectors * 512) // 16

        p.mov16(regs.CX, sectors & 0xFFFF)       # CX = sectors remaining
        p.mov16(regs.DI, start_lba & 0xFFFF)      # DI = current LBA
        p.mov16(regs.BX, initial_segment)          # BX = current destination segment

        p.label("__chunk_loop")
        p.cmp16(regs.CX, 0)
        p.jcc(regs.JNZ, "__chunk_continue")
        p.jmp("__load_done")
        p.label("__chunk_continue")

        p.cmp16(regs.CX, chunk_sectors)
        p.jcc(regs.JAE, "__use_full_chunk")
        p.mov_rr16(regs.BP, regs.CX)                # last, partial chunk: BP = remaining
        p.jmp("__chunk_size_ready")
        p.label("__use_full_chunk")
        p.mov16(regs.BP, chunk_sectors)              # BP = a full chunk
        p.label("__chunk_size_ready")
        # BP holds "sectors in this chunk" from here on. Deliberately not
        # AX: the BIOS call below needs AH=0x42 for the syscall number and
        # returns its own status in AH, so anything held in AX would be
        # corrupted by the call -- exactly the bug that motivated moving
        # this into BP in the first place.

        # Update this chunk's Disk Address Packet fields in place.
        p.store16_label("__dap", regs.BP, extra_offset=2, base=BOOT_SECTOR_LOAD_ADDR)   # sector count
        p.store16_label("__dap", regs.BX, extra_offset=6, base=BOOT_SECTOR_LOAD_ADDR)   # buffer segment
        p.store16_label("__dap", regs.DI, extra_offset=8, base=BOOT_SECTOR_LOAD_ADDR)   # LBA (low word)

        p.label("__chunk_retry")
        p.mov8(regs.AH, 0x00)  # reset disk system
        p.interrupt(0x13)
        p.mov16_label(regs.SI, "__dap", base=BOOT_SECTOR_LOAD_ADDR)
        p.mov8(regs.AH, 0x42)
        p.interrupt(0x13)
        p.jcc(regs.JC, "__chunk_retry")

        # Advance LBA by BP sectors and segment by BP*32 paragraphs
        # (512 bytes/sector == 32 paragraphs/sector), via doubling since
        # there's no 16-bit multiply available here. Uses SI as scratch
        # (its DAP-pointer value from the read above is no longer needed)
        # -- deliberately NOT DX, whose low byte IS DL, the boot drive
        # number; using DX here silently corrupted DL, breaking every
        # chunk after the first.
        p.mov_rr16(regs.SI, regs.BP)
        for _ in range(5):  # x2 five times == x32
            p.add_rr16(regs.SI, regs.SI)
        p.add_rr16(regs.BX, regs.SI)
        p.add_rr16(regs.DI, regs.BP)
        p.sub_rr16(regs.CX, regs.BP)
        p.jmp("__chunk_loop")

        # Disk Address Packet (16 bytes) -- lives inline, never reached as
        # code since the loop above always jumps back unconditionally.
        p.label("__dap")
        p.db(0x10)                     # packet size
        p.db(0x00)                     # reserved
        p.dw(0)                        # sector count -- overwritten before every read
        p.dw(0)                        # buffer offset -- always 0 (see docstring above)
        p.dw(0)                        # buffer segment -- overwritten before every read
        p.dd(start_lba & 0xFFFFFFFF)   # LBA low 32 bits -- overwritten before every read after the first
        p.dd(0)                        # LBA high 32 bits -- always 0

        p.label("__load_done")
        return self

    def jump_to_kernel(self, segment: int = KERNEL_LOAD_SEGMENT,
                        offset: int = KERNEL_LOAD_OFFSET) -> "BootSector":
        self.prog.far_jmp16(segment, offset)
        return self

    def halt_forever(self) -> "BootSector":
        label = "__halt"
        self.prog.label(label)
        self.prog.hlt()
        self.prog.jmp(label)
        return self

    def assemble(self) -> bytes:
        """Assemble and pad/truncate to exactly 512 bytes with the
        mandatory 0x55 0xAA boot signature."""
        code = self.prog.assemble()
        if len(code) > BOOT_SECTOR_SIZE - 2:
            raise ValueError(
                f"Boot sector code is {len(code)} bytes -- must fit in "
                f"{BOOT_SECTOR_SIZE - 2} bytes to leave room for the boot signature. "
                "Move more logic into the kernel/stage2, loaded separately."
            )
        padding = bytes(BOOT_SECTOR_SIZE - 2 - len(code))
        return code + padding + bytes(BOOT_SIGNATURE)

    @classmethod
    def standard(cls, kernel_sectors: int, message: str = "Aetherix booting...") -> "BootSector":
        """A ready-to-use standard bootloader: segment setup, boot message,
        A20 enable, load `kernel_sectors` sectors from disk via LBA
        extended read, jump to kernel. Falls through to a halt loop if
        disk load never returns (defensive)."""
        b = cls()
        b.setup_segments()
        if message:
            b.print_message(message)
        b.enable_a20()
        b.load_kernel_lba(sectors=kernel_sectors)
        b.jump_to_kernel()
        b.halt_forever()
        return b
