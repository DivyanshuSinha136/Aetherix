"""
Build-time image preparation for `aetherix.drivers.graphics`.

VGA Mode 13h needs pixel data as palette *indices* (one byte per pixel)
plus a separate 256-entry color table, at a fixed 320x200 resolution. This
module converts an ordinary image file (anything Pillow can open --
PNG/JPEG/BMP/GIF/etc.) into that form at build time, so your kernel only
ever has to embed and display already-prepared bytes.

Requires Pillow (`pip install Pillow`) -- a build-time-only dependency.
Nothing in the OS you build depends on Pillow; only the Python script that
prepares images for it does.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Union

try:
    from PIL import Image
    _HAVE_PIL = True
except ImportError:
    _HAVE_PIL = False

from .drivers.graphics import GFX_WIDTH, GFX_HEIGHT


def _require_pil():
    if not _HAVE_PIL:
        raise ImportError(
            "aetherix.imaging needs Pillow to load/convert images: pip install Pillow"
        )


@dataclass
class ImageAsset:
    """A build-time-prepared image, ready to embed with
    `aetherix.drivers.graphics.show_image`/`blit`/`play_frames`."""
    width: int
    height: int
    palette_bytes: bytes  # entry_count * 3 bytes, R,G,B, each 0-63 (VGA DAC precision)
    pixel_bytes: bytes    # width * height bytes, palette indices

    @property
    def size_bytes(self) -> int:
        return len(self.palette_bytes) + len(self.pixel_bytes)


def _quantize_frame(im, width: int, height: int, fit: bool) -> ImageAsset:
    im = im.convert("RGB")
    if fit:
        im = im.resize((width, height), Image.LANCZOS)
    elif im.size != (width, height):
        raise ValueError(
            f"image is {im.size}, expected exactly ({width}, {height}) -- "
            "pass fit=True to resize automatically"
        )

    quantized = im.quantize(colors=256, method=Image.MEDIANCUT)
    pixel_bytes = quantized.tobytes()

    raw_palette = quantized.getpalette()  # flat [r,g,b, r,g,b, ...] 0-255, len up to 768
    raw_palette = raw_palette[:256 * 3] + [0] * max(0, 256 * 3 - len(raw_palette))
    # VGA DAC registers are 6 bits per channel (0-63), not 8 (0-255).
    palette_bytes = bytes(v >> 2 for v in raw_palette)

    return ImageAsset(width=width, height=height,
                       palette_bytes=palette_bytes, pixel_bytes=pixel_bytes)


def load_image(path: Union[str, Path], width: int = GFX_WIDTH, height: int = GFX_HEIGHT,
               fit: bool = True) -> ImageAsset:
    """Load a single image file and prepare it for `graphics.show_image`.
    Resizes to (width, height) if `fit` is True (default); otherwise the
    source image must already be exactly that size. Quantizes to a
    256-color palette (fine for photos/logos; sharp text/line art may show
    some banding -- reduce `width`/`height` or simplify the source image
    if that matters for your use case)."""
    _require_pil()
    with Image.open(path) as im:
        return _quantize_frame(im, width, height, fit)


def load_images_shared_palette(specs, width: int = GFX_WIDTH, height: int = GFX_HEIGHT,
                                fit: bool = True) -> List[ImageAsset]:
    """Prepare multiple images that will be on screen *at the same time*
    (e.g. a background plus a logo), quantized to one shared 256-color
    palette instead of independently.

    VGA Mode 13h has a single 256-color palette for the whole screen, not
    one per image. If you prepare two images independently (each getting
    its own best-fit palette) and blit both, only the most-recently-
    uploaded palette is actually active in hardware -- the earlier image
    ends up displayed through the wrong color table. Whenever more than
    one image will be visible on screen simultaneously, use this instead
    of calling `load_image` on each separately.

    `specs` is a list of image paths, or `(path, width, height, fit)`
    tuples to override the defaults per image (e.g. a small logo at its
    native size while a background gets resized to fill the screen).
    """
    _require_pil()
    parsed = []
    for spec in specs:
        if isinstance(spec, (tuple, list)):
            path, w, h, f = (list(spec) + [width, height, fit])[:4]
        else:
            path, w, h, f = spec, width, height, fit
        parsed.append((path, w, h, f))

    images = []
    for path, w, h, f in parsed:
        with Image.open(path) as im:
            im = im.convert("RGB")
            if f:
                im = im.resize((w, h), Image.LANCZOS)
            elif im.size != (w, h):
                raise ValueError(
                    f"{path} is {im.size}, expected exactly ({w}, {h}) -- pass fit=True to resize"
                )
            images.append(im)

    # Derive one shared palette from all images pasted onto a single canvas.
    total_w = sum(im.width for im in images)
    max_h = max(im.height for im in images)
    combo = Image.new("RGB", (total_w, max_h))
    x_off = 0
    for im in images:
        combo.paste(im, (x_off, 0))
        x_off += im.width
    combo_quant = combo.quantize(colors=256, method=Image.MEDIANCUT)

    raw_palette = combo_quant.getpalette()
    raw_palette = raw_palette[:256 * 3] + [0] * max(0, 256 * 3 - len(raw_palette))
    palette_bytes = bytes(v >> 2 for v in raw_palette)

    assets = []
    for im in images:
        remapped = im.quantize(palette=combo_quant, dither=Image.FLOYDSTEINBERG)
        assets.append(ImageAsset(width=im.width, height=im.height,
                                  palette_bytes=palette_bytes, pixel_bytes=remapped.tobytes()))
    return assets


def load_animation(path: Union[str, Path], width: int = GFX_WIDTH, height: int = GFX_HEIGHT,
                    fit: bool = True, max_frames: int = None) -> List[ImageAsset]:
    """Load an animated image (GIF, animated WEBP/PNG) into a list of
    `ImageAsset` frames for `graphics.play_frames`. Each frame is
    independently quantized (frames are not guaranteed to share a
    palette) -- this keeps color quality high per-frame at the cost of a
    palette reload between frames, which `play_frames` already does.
    `max_frames` truncates a long animation (each frame embeds its own
    full palette + pixel data, so a 50-frame animation at full screen size
    is ~3.5MB -- pick a disk image size preset large enough, or trim
    frames/resolution)."""
    _require_pil()
    frames = []
    with Image.open(path) as im:
        n_frames = getattr(im, "n_frames", 1)
        if max_frames is not None:
            n_frames = min(n_frames, max_frames)
        for i in range(n_frames):
            im.seek(i)
            frames.append(_quantize_frame(im, width, height, fit))
    return frames
