"""Register and condition-code constants for the Aetherix assembler layer."""

# 16-bit general purpose registers
AX, CX, DX, BX, SP, BP, SI, DI = range(8)

# 32-bit general purpose registers (same numbering, different width)
EAX, ECX, EDX, EBX, ESP, EBP, ESI, EDI = range(8)

# 64-bit general purpose registers (same numbering 0-7 as EAX etc, reused
# by width depending on Program(bits=64) context) plus R8-R15, which are
# only usable in 64-bit code (they don't exist in 16/32-bit modes).
R8, R9, R10, R11, R12, R13, R14, R15 = range(8, 16)

# 8-bit general purpose registers
AL, CL, DL, BL, AH, CH, DH, BH = range(8)

# Segment registers
ES, CS, SS, DS, FS, GS = range(6)

# Condition codes (short/near jump opcodes, Jcc rel8 form; used for both
# rel8 and translated to the 0F 8x rel32 near form by aeth_jcc_rel32)
JZ = JE = 0x74
JNZ = JNE = 0x75
JC = JB = 0x72
JNC = JAE = 0x73
JBE = 0x76
JA = 0x77
JS = 0x78
JNS = 0x79
JL = 0x7C
JGE = 0x7D
JLE = 0x7E
JG = 0x7F

REG16_NAMES = {AX: "ax", CX: "cx", DX: "dx", BX: "bx", SP: "sp", BP: "bp", SI: "si", DI: "di"}
REG8_NAMES = {AL: "al", CL: "cl", DL: "dl", BL: "bl", AH: "ah", CH: "ch", DH: "dh", BH: "bh"}
SEG_NAMES = {ES: "es", CS: "cs", SS: "ss", DS: "ds", FS: "fs", GS: "gs"}
