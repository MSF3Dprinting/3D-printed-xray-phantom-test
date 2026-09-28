"""Turning stored scan values into pictures — exactly.

This is a QC tool: what the operator looks at is the inspection. The rule, from
the user after the viewer had been switched to JPEG and lost the finest
line-pair bars: "The compression could never lead to lose of visual details!
Never in any part of the app."

So every picture of the scan is made here, and made the same way:

* a stated window (lo..hi) maps stored values to the 256 grey levels a screen
  can show — rounded to the NEAREST level, not truncated, which had made every
  picture half a level too dark and left level 255 almost unused;
* where the screen cannot show every scan pixel, each screen pixel is the
  plain mean of the scan pixels it covers — never resampled with a
  sharpening filter, which draws faint halos at sharp edges that are not in
  the scan;
* the result is encoded losslessly: WebP in its lossless mode, or PNG. Either
  one decodes back to exactly these grey levels.

The single exception, decided by the user, is the comparison report's small
pictures, which stay JPEG as an overall look; they are made in thumbnails.py
and are the only lossy encoding in the application.
"""

from __future__ import annotations

import io

import numpy as np

#: Whether this server's image library can write WebP. The standard wheels
#: can; a build without it falls back to PNG, which is equally exact.
try:
    from PIL import features as _features
    WEBP_AVAILABLE = bool(_features.check("webp"))
except Exception:                                    # pragma: no cover
    WEBP_AVAILABLE = False

#: Media types, by the name used in addresses.
MEDIA_TYPES = {"webp": "image/webp", "png": "image/png"}


def window_to_grey(values: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Stored values -> 8-bit grey levels for the window lo..hi.

    Rounded to the nearest level. Values outside the window are shown as
    black or white, which is what a window means."""
    span = float(hi) - float(lo)
    if not np.isfinite(span) or span <= 0:
        span = 1e-9
    scaled = (np.asarray(values, dtype=np.float64) - float(lo)) * (255.0 / span)
    return np.clip(np.rint(scaled), 0, 255).astype(np.uint8)


def shrink_by_averaging(values: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """The scan reduced to (width, height) by averaging, before any window.

    Averaging happens on the stored values, not on grey levels, so shrinking
    and then windowing gives the same picture as a screen that could show
    every pixel and then blended them. Returns the input unchanged when no
    reduction is needed; never enlarges."""
    arr = np.asarray(values, dtype=np.float32)
    h, w = arr.shape
    tw, th = int(size[0]), int(size[1])
    if tw >= w and th >= h:
        return arr
    from PIL import Image
    img = Image.fromarray(arr, mode="F")
    # BOX averages whole pixels: each output pixel is the plain mean of the
    # input pixels whose centres fall inside it, so every scan pixel counts
    # once and with equal weight. Where the factor is not whole, neighbouring
    # groups differ by one pixel in size (checked on 2026-09-28: 7 -> 3 gives
    # the means of pixels 0-1, 2-4 and 5-6). Horizontal pass first, float32
    # in between — tests/test_picture_guard.py repeats exactly this.
    return np.asarray(img.resize((max(tw, 1), max(th, 1)), Image.BOX),
                      dtype=np.float32)


#: Full detail on zoom comes in square pieces of this many pixels, at the
#: scan's own size (level 0) and at 1/2 and 1/4 of it (levels 1 and 2).
TILE = 256
LEVELS = 3


def block_average(values: np.ndarray, factor: int) -> np.ndarray:
    """The scan at 1/factor of its size, for the zoom levels.

    Each pixel is the mean of exactly the factor x factor block of scan
    pixels it covers, so every level lies on the scan's own pixel grid and a
    level pixel's position times the factor is a scan position. Blocks at the
    right and bottom edge that the scan does not fill are the mean of the
    pixels that are there."""
    arr = np.asarray(values, dtype=np.float64)
    if factor == 1:
        return arr
    h, w = arr.shape
    rows, cols = -(-h // factor), -(-w // factor)
    padded = np.full((rows * factor, cols * factor), np.nan)
    padded[:h, :w] = arr
    return np.nanmean(padded.reshape(rows, factor, cols, factor), axis=(1, 3))


def tile_bounds(shape: tuple[int, int], tx: int, ty: int):
    """(y0, y1, x0, x1) of tile (tx, ty) in a level of this shape, or None
    when there is no such tile. Tiles at the right and bottom edge are
    narrower where the level ends."""
    h, w = shape
    x0, y0 = tx * TILE, ty * TILE
    if tx < 0 or ty < 0 or x0 >= w or y0 >= h:
        return None
    return y0, min(y0 + TILE, h), x0, min(x0 + TILE, w)


def encode_lossless(grey: np.ndarray, fmt: str = "webp") -> tuple[bytes, str]:
    """8-bit grey levels -> (bytes, media type), losslessly.

    ``fmt`` is "webp" or "png". WebP falls back to PNG when this server cannot
    write it; both decode to exactly ``grey``."""
    from PIL import Image
    arr = np.ascontiguousarray(np.asarray(grey, dtype=np.uint8))
    img = Image.fromarray(arr, mode="L")
    buf = io.BytesIO()
    if fmt == "webp" and WEBP_AVAILABLE:
        # method 4: at a 1024 px viewer size, measured 8-15 % smaller than
        # PNG on four real scans for 0.1-0.2 s more work; the top setting
        # saved ~1 % more for 17-35 s. exact keeps every value.
        img.save(buf, format="WEBP", lossless=True, method=4, exact=True)
        return buf.getvalue(), MEDIA_TYPES["webp"]
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), MEDIA_TYPES["png"]


def encode_lossless_rgb(rgb: np.ndarray, fmt: str = "webp") -> tuple[bytes, str]:
    """Colour pictures (charts) -> (bytes, media type), losslessly."""
    from PIL import Image
    arr = np.ascontiguousarray(np.asarray(rgb, dtype=np.uint8))
    img = Image.fromarray(arr, mode="RGB")
    buf = io.BytesIO()
    if fmt == "webp" and WEBP_AVAILABLE:
        img.save(buf, format="WEBP", lossless=True, method=4, exact=True)
        return buf.getvalue(), MEDIA_TYPES["webp"]
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), MEDIA_TYPES["png"]


def decode(data: bytes) -> np.ndarray:
    """Bytes -> array, for checks that a picture is what was sent.

    WebP has no greyscale mode, so a grey picture comes back as three
    identical colour channels. They are collapsed to one only when they
    really are identical — a picture that is not grey still fails a check
    loudly instead of being quietly reduced to its red channel."""
    from PIL import Image
    arr = np.asarray(Image.open(io.BytesIO(data)))
    if (arr.ndim == 3 and arr.shape[2] in (3, 4)
            and np.array_equal(arr[..., 0], arr[..., 1])
            and np.array_equal(arr[..., 0], arr[..., 2])):
        return arr[..., 0]
    return arr


def render(values: np.ndarray, lo: float, hi: float,
           size: tuple[int, int] | None = None, fmt: str = "webp"):
    """Window, average down if asked, encode losslessly -> (bytes, media type).

    The one road from stored values to a picture of the scan."""
    vals = shrink_by_averaging(values, size) if size else values
    return encode_lossless(window_to_grey(vals, lo, hi), fmt)
