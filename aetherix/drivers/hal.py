"""
Hardware Abstraction Layer (HAL) and driver plugin registry.

Aetherix ships a small number of drivers that are genuinely implementable
by writing directly to fixed I/O ports / memory addresses, without an
existing OS, bus enumeration, or a USB/PCI stack underneath you:

    IMPLEMENTED  vga       - VGA text-mode framebuffer (0xB8000)
    IMPLEMENTED  keyboard  - PS/2 controller polling (ports 0x60/0x64):
                             full Set 1 scancode table (letters, digits,
                             punctuation, Tab) plus Shift-aware read_char
    IMPLEMENTED  speaker   - PC speaker via PIT channel 2 + port 0x61
    IMPLEMENTED  power     - restart (soft, jump to kernel start), reboot
                             (hard, keyboard-controller reset pulse), and
                             a QEMU/Bochs-only shutdown + an approximate
                             (non-ACPI) sleep -- see aetherix.drivers.power
                             for exactly what each does and doesn't do
    IMPLEMENTED  terminal  - runtime VGA cursor (putchar/newline/backspace),
                             built on top of vga's register-indirect writes
    IMPLEMENTED  graphics  - VGA Mode 13h (320x200, 256-color) framebuffer:
                             palette upload + image blitting for embedded
                             images (see aetherix.imaging to prepare them).
                             This is legacy VGA graphics, NOT the general
                             `gpu` slot below -- no modern framebuffer/GOP/
                             VBE support, no 3D, no hardware acceleration.

Everything below this line needs real bus/protocol work before it can do
anything (PCI enumeration, a USB host controller driver, a NIC's DMA ring
buffers, ACPI for battery/fan telemetry, etc.). Building genuine drivers
for these is exactly the kind of "further OS development" Aetherix wants
to make approachable -- so they are registered here as named extension
points rather than faked:

    SCAFFOLDED   gpu       - needs a mode-set protocol (VBE/GOP) or a real
                             GPU driver; framebuffer-only linear modes are
                             a realistic next step, 3D is not.
    SCAFFOLDED   usb       - needs a host controller driver (UHCI/EHCI/xHCI)
    SCAFFOLDED   ethernet  - needs a NIC driver (e.g. RTL8139/E1000) + a
                             minimal network stack
    SCAFFOLDED   printer   - legacy parallel port (0x378) raw byte output
                             is feasible; anything modern goes over USB/net
    SCAFFOLDED   fan       - needs ACPI/EC access on real hardware
    SCAFFOLDED   battery   - needs ACPI (or SMBIOS) parsing
    SCAFFOLDED   camera    - needs a USB video class driver (built on `usb`)

Call `HAL.register()` to add a real implementation for a scaffolded slot;
see `CONTRIBUTING.md` for the expected driver-module interface.
"""
from __future__ import annotations

from typing import Callable, Dict

from . import vga as _vga
from . import keyboard as _keyboard
from . import speaker as _speaker
from . import terminal as _terminal
from . import graphics as _graphics
from . import power as _power


class _NotImplementedDriver:
    def __init__(self, name: str, needs: str):
        self._name = name
        self._needs = needs

    def __getattr__(self, item):
        raise NotImplementedError(
            f"Driver '{self._name}' is not implemented yet in Aetherix. "
            f"It needs: {self._needs}. This is a registered HAL extension "
            f"point -- see aetherix.drivers.hal.HAL.register()."
        )


class HAL:
    """Registry of hardware drivers available to a Program/Kernel build."""

    def __init__(self):
        self._drivers: Dict[str, object] = {
            "vga": _vga,
            "keyboard": _keyboard,
            "speaker": _speaker,
            "terminal": _terminal,
            "graphics": _graphics,
            "power": _power,
            "gpu": _NotImplementedDriver("gpu", "VBE/GOP mode-set support"),
            "usb": _NotImplementedDriver("usb", "a UHCI/EHCI/xHCI host controller driver"),
            "ethernet": _NotImplementedDriver("ethernet", "a NIC driver + minimal network stack"),
            "printer": _NotImplementedDriver("printer", "legacy parallel port (0x378) output routine"),
            "fan": _NotImplementedDriver("fan", "ACPI/EC access"),
            "battery": _NotImplementedDriver("battery", "ACPI or SMBIOS parsing"),
            "camera": _NotImplementedDriver("camera", "a USB video class driver"),
        }

    def register(self, name: str, driver_module: object) -> None:
        """Register (or replace) a driver implementation by name."""
        self._drivers[name] = driver_module

    def implemented(self):
        return [n for n, d in self._drivers.items() if not isinstance(d, _NotImplementedDriver)]

    def scaffolded(self):
        return [n for n, d in self._drivers.items() if isinstance(d, _NotImplementedDriver)]

    def __getattr__(self, name: str):
        try:
            return self._drivers[name]
        except KeyError:
            raise AttributeError(name)
