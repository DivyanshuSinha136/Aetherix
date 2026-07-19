from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .drivers.hal import HAL

_EXAMPLE_TEMPLATE = '''"""
{name} -- an Aetherix OS project.
Run: python {filename}
"""
from aetherix import Project, vga, keyboard

with Project("{name}") as os:

    @os.kernel_entry
    def main(prog, drivers):
        drivers.vga.clear(prog)
        drivers.vga.print_string(prog, "Hello from {name}!", row=0, col=0)
        drivers.vga.print_string(prog, "Press any key...", row=2, col=0)
        drivers.keyboard.wait_key_scancode(prog)
        drivers.vga.print_string(prog, "Key received. Halting.", row=4, col=0)
        prog.hlt()

    out = os.build("{out}")
    print(f"Built {{out}} -- boot it in QEMU with:")
    print(f"  qemu-system-i386 -drive file={{out}},format=raw")
'''


def _cmd_new(args):
    name = args.name
    filename = f"{name.lower().replace(' ', '_')}.py"
    out_img = f"{name.lower().replace(' ', '_')}.img"
    path = Path(args.directory) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_EXAMPLE_TEMPLATE.format(name=name, filename=filename, out=out_img))
    print(f"Created {path}")
    print(f"Next: python {path}")


def _cmd_info(_args):
    hal = HAL()
    print("Aetherix hardware support:")
    print("  Implemented:", ", ".join(sorted(hal.implemented())))
    print("  Scaffolded (extension points, not yet implemented):", ", ".join(sorted(hal.scaffolded())))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="aetherix", description="Aetherix OS toolkit CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new", help="Scaffold a new Aetherix project script")
    p_new.add_argument("name", help="Project name, e.g. 'MyOS'")
    p_new.add_argument("-d", "--directory", default=".", help="Directory to create the project in")
    p_new.set_defaults(func=_cmd_new)

    p_info = sub.add_parser("info", help="Show what hardware Aetherix currently supports")
    p_info.set_defaults(func=_cmd_info)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
