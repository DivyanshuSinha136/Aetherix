/*
 * Aetherix native encoder
 * -----------------------
 * A minimal, self-contained x86 16-bit (real mode) and 32-bit (protected
 * mode) instruction encoder. This emits raw machine code bytes directly —
 * there is no dependency on nasm, gas, ld, or any external toolchain.
 *
 * This is intentionally a *small, well-defined instruction subset*: enough
 * to write bootloaders, mode transitions, and simple kernels (VGA text
 * output, PS/2 keyboard polling, PC speaker, disk I/O via BIOS in real
 * mode). It is not a general-purpose assembler.
 *
 * Exposed as a C ABI shared library and consumed from Python via ctypes.
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
  #define AETH_API __declspec(dllexport)
#else
  #define AETH_API __attribute__((visibility("default")))
#endif

typedef struct {
    uint8_t *data;
    size_t   len;
    size_t   cap;
} enc_buf_t;

static void buf_ensure(enc_buf_t *b, size_t extra) {
    if (b->len + extra <= b->cap) return;
    size_t newcap = b->cap ? b->cap * 2 : 256;
    while (newcap < b->len + extra) newcap *= 2;
    b->data = (uint8_t *)realloc(b->data, newcap);
    b->cap = newcap;
}

static void push8(enc_buf_t *b, uint8_t v) {
    buf_ensure(b, 1);
    b->data[b->len++] = v;
}

static void push16(enc_buf_t *b, uint16_t v) {
    buf_ensure(b, 2);
    b->data[b->len++] = (uint8_t)(v & 0xFF);
    b->data[b->len++] = (uint8_t)((v >> 8) & 0xFF);
}

static void push32(enc_buf_t *b, uint32_t v) {
    buf_ensure(b, 4);
    b->data[b->len++] = (uint8_t)(v & 0xFF);
    b->data[b->len++] = (uint8_t)((v >> 8) & 0xFF);
    b->data[b->len++] = (uint8_t)((v >> 16) & 0xFF);
    b->data[b->len++] = (uint8_t)((v >> 24) & 0xFF);
}

/* ---------------------------------------------------------------------- */
/* Buffer lifecycle                                                        */
/* ---------------------------------------------------------------------- */

AETH_API enc_buf_t *aeth_buf_new(void) {
    enc_buf_t *b = (enc_buf_t *)calloc(1, sizeof(enc_buf_t));
    return b;
}

AETH_API void aeth_buf_free(enc_buf_t *b) {
    if (!b) return;
    free(b->data);
    free(b);
}

AETH_API size_t aeth_buf_len(enc_buf_t *b) { return b->len; }

AETH_API void aeth_buf_copy(enc_buf_t *b, uint8_t *out, size_t out_len) {
    size_t n = out_len < b->len ? out_len : b->len;
    memcpy(out, b->data, n);
}

AETH_API void aeth_buf_reset(enc_buf_t *b) { b->len = 0; }

/* Raw byte / word / string emission -------------------------------------- */

AETH_API void aeth_db(enc_buf_t *b, uint8_t v) { push8(b, v); }
AETH_API void aeth_dw(enc_buf_t *b, uint16_t v) { push16(b, v); }
AETH_API void aeth_dd(enc_buf_t *b, uint32_t v) { push32(b, v); }
AETH_API void aeth_dbytes(enc_buf_t *b, const uint8_t *bytes, size_t n) {
    buf_ensure(b, n);
    memcpy(b->data + b->len, bytes, n);
    b->len += n;
}

/* ---------------------------------------------------------------------- */
/* 16-bit real-mode instructions                                          */
/* Register numbering (16-bit): AX=0 CX=1 DX=2 BX=3 SP=4 BP=5 SI=6 DI=7    */
/* Segment numbering: ES=0 CS=1 SS=2 DS=3 FS=4 GS=5                       */
/* 8-bit register numbering: AL=0 CL=1 DL=2 BL=3 AH=4 CH=5 DH=6 BH=7      */
/* ---------------------------------------------------------------------- */

AETH_API void aeth_cli(enc_buf_t *b) { push8(b, 0xFA); }
AETH_API void aeth_sti(enc_buf_t *b) { push8(b, 0xFB); }
AETH_API void aeth_hlt(enc_buf_t *b) { push8(b, 0xF4); }
AETH_API void aeth_nop(enc_buf_t *b) { push8(b, 0x90); }
AETH_API void aeth_cld(enc_buf_t *b) { push8(b, 0xFC); }
AETH_API void aeth_ret(enc_buf_t *b) { push8(b, 0xC3); }

AETH_API void aeth_mov_r16_imm16(enc_buf_t *b, uint8_t reg, uint16_t imm) {
    push8(b, 0xB8 + (reg & 7));
    push16(b, imm);
}

AETH_API void aeth_mov_r8_imm8(enc_buf_t *b, uint8_t reg, uint8_t imm) {
    push8(b, 0xB0 + (reg & 7));
    push8(b, imm);
}

AETH_API void aeth_mov_sreg_r16(enc_buf_t *b, uint8_t sreg, uint8_t r16) {
    push8(b, 0x8E);
    push8(b, 0xC0 | ((sreg & 7) << 3) | (r16 & 7));
}

AETH_API void aeth_mov_r16_sreg(enc_buf_t *b, uint8_t r16, uint8_t sreg) {
    push8(b, 0x8C);
    push8(b, 0xC0 | ((sreg & 7) << 3) | (r16 & 7));
}

AETH_API void aeth_mov_r16_r16(enc_buf_t *b, uint8_t dst, uint8_t src) {
    push8(b, 0x89);
    push8(b, 0xC0 | ((src & 7) << 3) | (dst & 7));
}

AETH_API void aeth_mov_r8_r8(enc_buf_t *b, uint8_t dst, uint8_t src) {
    push8(b, 0x88);
    push8(b, 0xC0 | ((src & 7) << 3) | (dst & 7));
}

AETH_API void aeth_xor_r16_r16(enc_buf_t *b, uint8_t dst, uint8_t src) {
    push8(b, 0x31);
    push8(b, 0xC0 | ((src & 7) << 3) | (dst & 7));
}

AETH_API void aeth_push_r16(enc_buf_t *b, uint8_t reg) { push8(b, 0x50 + (reg & 7)); }
AETH_API void aeth_pop_r16(enc_buf_t *b, uint8_t reg)  { push8(b, 0x58 + (reg & 7)); }

AETH_API void aeth_int(enc_buf_t *b, uint8_t imm) { push8(b, 0xCD); push8(b, imm); }

/* 16-bit displacement-only memory addressing: [imm16] (mod=00, rm=110) */
AETH_API void aeth_mov_mem16_r16(enc_buf_t *b, uint16_t disp, uint8_t r16) {
    push8(b, 0x89);
    push8(b, 0x06 | ((r16 & 7) << 3));
    push16(b, disp);
}
AETH_API void aeth_mov_r16_mem16(enc_buf_t *b, uint8_t r16, uint16_t disp) {
    push8(b, 0x8B);
    push8(b, 0x06 | ((r16 & 7) << 3));
    push16(b, disp);
}

AETH_API void aeth_cmp_r8_imm8(enc_buf_t *b, uint8_t reg, uint8_t imm) {
    push8(b, 0x80);
    push8(b, 0xF8 | (reg & 7));
    push8(b, imm);
}
AETH_API void aeth_cmp_r16_imm16(enc_buf_t *b, uint8_t reg, uint16_t imm) {
    push8(b, 0x81);
    push8(b, 0xF8 | (reg & 7));
    push16(b, imm);
}

/* test al, imm8 (A8 ib) -- only defined for AL (reg 0); ZF set per AND result */
AETH_API void aeth_test_al_imm8(enc_buf_t *b, uint8_t imm) {
    push8(b, 0xA8);
    push8(b, imm);
}

/* or al, imm8 (0C ib) -- for setting individual bits without clobbering others */
AETH_API void aeth_or_al_imm8(enc_buf_t *b, uint8_t imm) {
    push8(b, 0x0C);
    push8(b, imm);
}

/* and al, imm8 (24 ib) */
AETH_API void aeth_and_al_imm8(enc_buf_t *b, uint8_t imm) {
    push8(b, 0x24);
    push8(b, imm);
}

/* Relative jumps: caller supplies already-computed relative displacement. */
AETH_API void aeth_jmp_rel8(enc_buf_t *b, int8_t rel)  { push8(b, 0xEB); push8(b, (uint8_t)rel); }
AETH_API void aeth_jmp_rel16(enc_buf_t *b, int16_t rel) { push8(b, 0xE9); push16(b, (uint16_t)rel); }

/* cc: 0x74=JZ/JE 0x75=JNZ/JNE 0x72=JC/JB 0x73=JNC/JAE 0x76=JBE 0x77=JA */
AETH_API void aeth_jcc_rel8(enc_buf_t *b, uint8_t cc, int8_t rel) {
    push8(b, cc);
    push8(b, (uint8_t)rel);
}

AETH_API void aeth_in_al_imm8(enc_buf_t *b, uint8_t port) { push8(b, 0xE4); push8(b, port); }
AETH_API void aeth_out_imm8_al(enc_buf_t *b, uint8_t port) { push8(b, 0xE6); push8(b, port); }
AETH_API void aeth_in_al_dx(enc_buf_t *b) { push8(b, 0xEC); }
AETH_API void aeth_out_dx_al(enc_buf_t *b) { push8(b, 0xEE); }

/* Far jump ptr16:16 -- used to enter protected-mode code segment */
AETH_API void aeth_far_jmp16(enc_buf_t *b, uint16_t seg, uint16_t off) {
    push8(b, 0xEA);
    push16(b, off);
    push16(b, seg);
}

/* lgdt [imm16] -- memory operand is disp16-only addressing, 0F 01 /2 */
AETH_API void aeth_lgdt_mem16(enc_buf_t *b, uint16_t disp) {
    push8(b, 0x0F);
    push8(b, 0x01);
    push8(b, 0x16);
    push16(b, disp);
}

/* mov eax, cr0 / mov cr0, eax -- 32-bit control register access */
AETH_API void aeth_mov_eax_cr0(enc_buf_t *b) { push8(b, 0x0F); push8(b, 0x20); push8(b, 0xC0); }
AETH_API void aeth_mov_cr0_eax(enc_buf_t *b) { push8(b, 0x0F); push8(b, 0x22); push8(b, 0xC0); }

/* or eax, imm32 (66 0D id) -- sets PE bit when entering protected mode */
AETH_API void aeth_or_eax_imm32(enc_buf_t *b, uint32_t imm) {
    push8(b, 0x66);
    push8(b, 0x0D);
    push32(b, imm);
}

/* ---------------------------------------------------------------------- */
/* 32-bit protected-mode instructions                                     */
/* Register numbering (32-bit): EAX=0 ECX=1 EDX=2 EBX=3 ESP=4 EBP=5 ESI=6 EDI=7 */
/* ---------------------------------------------------------------------- */

AETH_API void aeth_mov_r32_imm32(enc_buf_t *b, uint8_t reg, uint32_t imm) {
    push8(b, 0xB8 + (reg & 7));
    push32(b, imm);
}

AETH_API void aeth_mov_r32_r32(enc_buf_t *b, uint8_t dst, uint8_t src) {
    push8(b, 0x89);
    push8(b, 0xC0 | ((src & 7) << 3) | (dst & 7));
}

/* disp32-only memory addressing (mod=00, rm=101) -- valid in 32-bit code */
AETH_API void aeth_mov_mem32_r32(enc_buf_t *b, uint32_t disp, uint8_t r32) {
    push8(b, 0x89);
    push8(b, 0x05 | ((r32 & 7) << 3));
    push32(b, disp);
}
AETH_API void aeth_mov_r32_mem32(enc_buf_t *b, uint8_t r32, uint32_t disp) {
    push8(b, 0x8B);
    push8(b, 0x05 | ((r32 & 7) << 3));
    push32(b, disp);
}
/* byte store/load at disp32 (for VGA text-mode cell writes: char + attr) */
AETH_API void aeth_mov_mem8_imm8(enc_buf_t *b, uint32_t disp, uint8_t imm) {
    push8(b, 0xC6);
    push8(b, 0x05);
    push32(b, disp);
    push8(b, imm);
}
AETH_API void aeth_mov_mem8_r8(enc_buf_t *b, uint32_t disp, uint8_t r8) {
    push8(b, 0x88);
    push8(b, 0x05 | ((r8 & 7) << 3));
    push32(b, disp);
}
AETH_API void aeth_mov_r8_mem8(enc_buf_t *b, uint8_t r8, uint32_t disp) {
    push8(b, 0x8A);
    push8(b, 0x05 | ((r8 & 7) << 3));
    push32(b, disp);
}

AETH_API void aeth_add_r32_imm32(enc_buf_t *b, uint8_t reg, uint32_t imm) {
    push8(b, 0x81);
    push8(b, 0xC0 | (reg & 7));
    push32(b, imm);
}
AETH_API void aeth_sub_r32_imm32(enc_buf_t *b, uint8_t reg, uint32_t imm) {
    push8(b, 0x81);
    push8(b, 0xE8 | (reg & 7));
    push32(b, imm);
}
AETH_API void aeth_cmp_r32_imm32(enc_buf_t *b, uint8_t reg, uint32_t imm) {
    push8(b, 0x81);
    push8(b, 0xF8 | (reg & 7));
    push32(b, imm);
}
AETH_API void aeth_inc_r32(enc_buf_t *b, uint8_t reg) { push8(b, 0x40 + (reg & 7)); }

AETH_API void aeth_jmp_rel32(enc_buf_t *b, int32_t rel) { push8(b, 0xE9); push32(b, (uint32_t)rel); }
AETH_API void aeth_jcc_rel32(enc_buf_t *b, uint8_t cc, int32_t rel) {
    /* cc here is the short-form condition (0x74 etc); near form is 0F 8x */
    push8(b, 0x0F);
    push8(b, 0x80 | (cc & 0x0F));
    push32(b, (uint32_t)rel);
}
AETH_API void aeth_call_rel32(enc_buf_t *b, int32_t rel) { push8(b, 0xE8); push32(b, (uint32_t)rel); }

/* mov r8, [si] -- register-indirect load, no displacement (mod=00, rm=100) */
AETH_API void aeth_mov_r8_mem_si(enc_buf_t *b, uint8_t r8) {
    push8(b, 0x8A);
    push8(b, 0x04 | ((r8 & 7) << 3));
}

/* inc r16 (single-byte form, 0x40 + reg) */
AETH_API void aeth_inc_r16(enc_buf_t *b, uint8_t reg) {
    push8(b, 0x40 + (reg & 7));
}

AETH_API void aeth_hlt32(enc_buf_t *b) { push8(b, 0xF4); }

/* ---------------------------------------------------------------------- */
/* 32-bit register-indirect addressing: [reg] and [reg+disp8]             */
/* base register must be EAX/ECX/EDX/EBX/ESI/EDI (mod=00, rm=reg direct   */
/* addressing) -- ESP/EBP need a SIB byte or disp32-forced encoding and   */
/* are not supported by these helpers.                                    */
/* ---------------------------------------------------------------------- */

static void check_valid_base(uint8_t base) {
    (void)base; /* ESP(4)/EBP(5) as base intentionally unsupported here */
}

AETH_API void aeth_mov_r8_mem_reg32(enc_buf_t *b, uint8_t r8, uint8_t base) {
    check_valid_base(base);
    push8(b, 0x8A);
    push8(b, ((r8 & 7) << 3) | (base & 7));
}

AETH_API void aeth_mov_mem_reg32_r8(enc_buf_t *b, uint8_t base, uint8_t r8) {
    check_valid_base(base);
    push8(b, 0x88);
    push8(b, ((r8 & 7) << 3) | (base & 7));
}

AETH_API void aeth_mov_mem_reg32_imm8(enc_buf_t *b, uint8_t base, uint8_t imm) {
    check_valid_base(base);
    push8(b, 0xC6);
    push8(b, (0 << 3) | (base & 7));
    push8(b, imm);
}

AETH_API void aeth_mov_mem_reg32_disp8_r8(enc_buf_t *b, uint8_t base, int8_t disp, uint8_t r8) {
    check_valid_base(base);
    push8(b, 0x88);
    push8(b, 0x40 | ((r8 & 7) << 3) | (base & 7));
    push8(b, (uint8_t)disp);
}

AETH_API void aeth_mov_mem_reg32_disp8_imm8(enc_buf_t *b, uint8_t base, int8_t disp, uint8_t imm) {
    check_valid_base(base);
    push8(b, 0xC6);
    push8(b, 0x40 | (0 << 3) | (base & 7));
    push8(b, (uint8_t)disp);
    push8(b, imm);
}

AETH_API void aeth_add_r32_r32(enc_buf_t *b, uint8_t dst, uint8_t src) {
    push8(b, 0x01);
    push8(b, 0xC0 | ((src & 7) << 3) | (dst & 7));
}

AETH_API void aeth_sub_r32_r32(enc_buf_t *b, uint8_t dst, uint8_t src) {
    push8(b, 0x29);
    push8(b, 0xC0 | ((src & 7) << 3) | (dst & 7));
}

/* out dx, ax -- 16-bit-sized port write via operand-size override (0x66).
 * Only correct when emitted inside a 32-bit code segment (there, the
 * default operand size is 32-bit, so 0x66 narrows this specific
 * instruction to 16 bits: AX, not EAX). In a 16-bit code segment the
 * override would instead widen a bare "out dx,ax" to 32-bit -- do not use
 * this helper there. */
AETH_API void aeth_out_dx_ax16(enc_buf_t *b) {
    push8(b, 0x66);
    push8(b, 0xEF);
}

/* ---------------------------------------------------------------------- */
/* 64-bit long mode (UEFI applications run here from the moment firmware  */
/* hands off control -- no 16/32-bit stages at all). Supports the full    */
/* register range RAX-R15 (0-15): registers 8-15 need REX.R (extends the  */
/* ModRM "reg" field) and/or REX.B (extends "rm"/opcode-embedded fields)  */
/* on top of REX.W (64-bit operand size). Memory operands always use the  */
/* disp8-or-disp32 ModRM form (never the "no displacement" mod=00 form),  */
/* which sidesteps the RSP/RBP-as-base special cases that form has in     */
/* both 32-bit and 64-bit addressing -- costs one extra byte when disp is */
/* exactly 0, in exchange for working uniformly for any of the 16 base    */
/* registers with no exceptions.                                          */
/* ---------------------------------------------------------------------- */

#define REX_W 0x48
#define REX_R 0x04
#define REX_X 0x02
#define REX_B 0x01

/* Builds a REX prefix byte. Pass 1 for w to force 64-bit operand size;
 * reg_field/rm_field are the two register operand numbers (0-15) going
 * into the ModRM reg/rm slots (or the opcode-embedded register for
 * push/pop/mov-imm-style opcodes, which only ever need REX.B). Pass -1
 * for whichever slot doesn't apply to a given instruction. */
static uint8_t make_rex(int w, int reg_field, int rm_field) {
    uint8_t rex = w ? REX_W : 0x40;
    if (reg_field >= 8) rex |= REX_R;
    if (rm_field >= 8) rex |= REX_B;
    return rex;
}

/* Only emit a REX prefix when something actually needs it (W, or a
 * register >= 8) -- matches what a real assembler emits and keeps the
 * common (registers 0-7) case exactly as small as before. Instructions
 * below call make_rex() directly when W is always required, or inline
 * a plain 0x41 (REX.B) for the few opcode-embedded-register forms that
 * only ever need that one bit. */

AETH_API void aeth64_push_r64(enc_buf_t *b, uint8_t reg) {
    if (reg >= 8) push8(b, 0x41);  /* REX.B */
    push8(b, 0x50 + (reg & 7));
}
AETH_API void aeth64_pop_r64(enc_buf_t *b, uint8_t reg) {
    if (reg >= 8) push8(b, 0x41);
    push8(b, 0x58 + (reg & 7));
}
AETH_API void aeth64_ret(enc_buf_t *b) { push8(b, 0xC3); }
AETH_API void aeth64_nop(enc_buf_t *b) { push8(b, 0x90); }
AETH_API void aeth64_hlt(enc_buf_t *b) { push8(b, 0xF4); }

AETH_API void aeth64_mov_r64_imm64(enc_buf_t *b, uint8_t reg, uint64_t imm) {
    push8(b, make_rex(1, -1, reg));
    push8(b, 0xB8 + (reg & 7));
    for (int i = 0; i < 8; i++) push8(b, (uint8_t)(imm >> (8 * i)));
}

/* mov r32, imm32 -- zero-extends into the full 64-bit register per
 * x86-64 semantics. Cheaper than movabs for small constants. REX.B only
 * (no REX.W) when reg is R8D-R15D. */
AETH_API void aeth64_mov_r32_imm32_zx(enc_buf_t *b, uint8_t reg, uint32_t imm) {
    if (reg >= 8) push8(b, 0x41);
    push8(b, 0xB8 + (reg & 7));
    push32(b, imm);
}

AETH_API void aeth64_mov_r64_r64(enc_buf_t *b, uint8_t dst, uint8_t src) {
    push8(b, make_rex(1, src, dst));
    push8(b, 0x89);
    push8(b, 0xC0 | ((src & 7) << 3) | (dst & 7));
}

static void emit_modrm_disp(enc_buf_t *b, uint8_t reg_field, uint8_t base, int32_t disp) {
    uint8_t base_low3 = base & 7;
    int needs_sib = (base_low3 == 4);  /* RSP and R12 both encode to 100 here --
                                         * mandatory SIB byte, regardless of mod. */
    if (disp >= -128 && disp <= 127) {
        push8(b, 0x40 | ((reg_field & 7) << 3) | base_low3);
        if (needs_sib) push8(b, 0x24);  /* SIB: no index, base = whatever's in ModRM/REX.B */
        push8(b, (uint8_t)disp);
    } else {
        push8(b, 0x80 | ((reg_field & 7) << 3) | base_low3);
        if (needs_sib) push8(b, 0x24);
        push32(b, (uint32_t)disp);
    }
}

AETH_API void aeth64_mov_mem_r64(enc_buf_t *b, uint8_t base, int32_t disp, uint8_t src) {
    push8(b, make_rex(1, src, base));
    push8(b, 0x89);
    emit_modrm_disp(b, src, base, disp);
}

AETH_API void aeth64_mov_r64_mem(enc_buf_t *b, uint8_t dst, uint8_t base, int32_t disp) {
    push8(b, make_rex(1, dst, base));
    push8(b, 0x8B);
    emit_modrm_disp(b, dst, base, disp);
}

/* movzx r64, word ptr [base+disp] -- zero-extends a 16-bit memory value.
 * Used to read individual fields out of small fixed-size structs (e.g.
 * EFI_INPUT_KEY's ScanCode/UnicodeChar) without needing a shift
 * instruction, which this encoder doesn't have. */
AETH_API void aeth64_movzx_r64_mem16(enc_buf_t *b, uint8_t dst, uint8_t base, int32_t disp) {
    push8(b, make_rex(1, dst, base));
    push8(b, 0x0F);
    push8(b, 0xB7);
    emit_modrm_disp(b, dst, base, disp);
}

/* lea r64, [rip + disp32] -- position-independent addressing of embedded
 * data, since a PE image can be loaded at any base address. */
AETH_API void aeth64_lea_r64_rip(enc_buf_t *b, uint8_t dst, int32_t disp) {
    push8(b, make_rex(1, dst, -1));
    push8(b, 0x8D);
    push8(b, 0x05 | ((dst & 7) << 3));  /* mod=00, rm=101 => RIP-relative */
    push32(b, (uint32_t)disp);
}

AETH_API void aeth64_call_r64(enc_buf_t *b, uint8_t reg) {
    /* Near CALL r/m64 defaults to 64-bit operands in long mode -- no
     * REX.W needed. REX.B is needed for R8-R15. */
    if (reg >= 8) push8(b, 0x41);
    push8(b, 0xFF);
    push8(b, 0xD0 | (reg & 7));
}

AETH_API void aeth64_add_r64_imm32(enc_buf_t *b, uint8_t reg, uint32_t imm) {
    push8(b, make_rex(1, -1, reg));
    push8(b, 0x81);
    push8(b, 0xC0 | (reg & 7));
    push32(b, imm);
}

AETH_API void aeth64_sub_r64_imm32(enc_buf_t *b, uint8_t reg, uint32_t imm) {
    push8(b, make_rex(1, -1, reg));
    push8(b, 0x81);
    push8(b, 0xE8 | (reg & 7));
    push32(b, imm);
}

AETH_API void aeth64_cmp_r64_imm32(enc_buf_t *b, uint8_t reg, uint32_t imm) {
    push8(b, make_rex(1, -1, reg));
    push8(b, 0x81);
    push8(b, 0xF8 | (reg & 7));
    push32(b, imm);
}

AETH_API void aeth64_cmp_r64_r64(enc_buf_t *b, uint8_t a, uint8_t rhs) {
    push8(b, make_rex(1, rhs, a));
    push8(b, 0x39);
    push8(b, 0xC0 | ((rhs & 7) << 3) | (a & 7));
}

AETH_API void aeth64_xor_r64_r64(enc_buf_t *b, uint8_t dst, uint8_t src) {
    push8(b, make_rex(1, src, dst));
    push8(b, 0x31);
    push8(b, 0xC0 | ((src & 7) << 3) | (dst & 7));
}

AETH_API void aeth64_jmp_rel32(enc_buf_t *b, int32_t rel) {
    push8(b, 0xE9);
    push32(b, (uint32_t)rel);
}

AETH_API void aeth64_jcc_rel32(enc_buf_t *b, uint8_t cc, int32_t rel) {
    push8(b, 0x0F);
    push8(b, 0x80 | (cc & 0x0F));
    push32(b, (uint32_t)rel);
}
