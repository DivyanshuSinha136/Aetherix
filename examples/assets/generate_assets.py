"""
Generates placeholder art for examples/graphics_os.py using Pillow, so the
example is runnable with no external image files. Swap these PNGs for your
own artwork any time -- graphics_os.py just calls aetherix.imaging.load_image
on whatever's in this directory.

Run: python examples/assets/generate_assets.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent


def make_loading_frames(n=6, size=(320, 200)):
    """A simple progress-bar boot animation, frame by frame."""
    frames = []
    for i in range(n):
        img = Image.new("RGB", size, (10, 12, 30))
        draw = ImageDraw.Draw(img)
        draw.text((110, 70), "Aetherix", fill=(120, 200, 255))
        bar_w, bar_h = 200, 14
        bx, by = (size[0] - bar_w) // 2, 110
        draw.rectangle([bx, by, bx + bar_w, by + bar_h], outline=(80, 90, 120))
        fill_w = int(bar_w * (i + 1) / n)
        draw.rectangle([bx, by, bx + fill_w, by + bar_h], fill=(90, 200, 140))
        draw.text((bx, by + 20), f"Loading... {int(100 * (i + 1) / n)}%", fill=(180, 190, 210))
        frames.append(img)
        img.save(OUT_DIR / f"loading_{i}.png")
    return frames


def make_background(size=(320, 200)):
    """A simple vertical gradient desktop background."""
    img = Image.new("RGB", size)
    px = img.load()
    top = (20, 30, 60)
    bottom = (70, 40, 90)
    for y in range(size[1]):
        t = y / (size[1] - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size[0]):
            px[x, y] = (r, g, b)
    img.save(OUT_DIR / "background.png")
    return img


def make_logo(size=(64, 64)):
    """A simple circular logo icon to overlay on the home screen."""
    img = Image.new("RGB", size, (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, size[0] - 2, size[1] - 2], fill=(90, 170, 255), outline=(230, 240, 255))
    draw.text((size[0] // 2 - 4, size[1] // 2 - 6), "A", fill=(15, 20, 40))
    img.save(OUT_DIR / "logo.png")
    return img


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    make_loading_frames()
    make_background()
    make_logo()
    print(f"Generated placeholder assets in {OUT_DIR}")
