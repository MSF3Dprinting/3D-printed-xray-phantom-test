"""The one road from stored scan values to a picture — and that it is exact.

The user's rule after the viewer had been switched to JPEG: "The compression
could never lead to lose of visual details! Never in any part of the app."
Every picture of the scan goes through phantom_qa/imaging.py; these tests pin
that what comes out decodes to exactly the grey levels that went in.
"""

from __future__ import annotations

import numpy as np
import pytest

from phantom_qa import imaging


def _scan(seed=0, shape=(300, 400)):
    """Noisy values with fine structure, like a phantom: worst case for any
    encoder that would cut corners."""
    rng = np.random.default_rng(seed)
    base = rng.normal(2000, 150, shape)
    base[:, ::4] += 300                     # a fine bar pattern
    return base


@pytest.mark.parametrize("fmt", ["webp", "png"])
def test_every_encoded_picture_decodes_to_exactly_what_was_encoded(fmt):
    grey = imaging.window_to_grey(_scan(), 1500, 2800)
    data, media = imaging.encode_lossless(grey, fmt)
    assert np.array_equal(imaging.decode(data), grey)
    assert media in ("image/webp", "image/png")


def test_webp_is_used_when_the_server_can_write_it():
    data, media = imaging.encode_lossless(np.zeros((8, 8), np.uint8), "webp")
    if imaging.WEBP_AVAILABLE:
        assert media == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    else:
        assert media == "image/png"


def test_webp_output_is_the_lossless_kind():
    """Lossy WebP ('VP8 ') would be as bad as JPEG; only 'VP8L' is allowed."""
    if not imaging.WEBP_AVAILABLE:
        pytest.skip("this server cannot write WebP")
    data, _ = imaging.encode_lossless(imaging.window_to_grey(_scan(), 1500, 2800))
    assert data[12:16] == b"VP8L"


def test_a_colour_chart_is_kept_exactly():
    rgb = np.random.default_rng(3).integers(0, 256, (60, 80, 3), dtype=np.uint8)
    for fmt in ("webp", "png"):
        data, _ = imaging.encode_lossless_rgb(rgb, fmt)
        assert np.array_equal(imaging.decode(data)[..., :3], rgb)


def test_grey_levels_are_rounded_to_the_nearest_not_truncated():
    """Truncation made every picture half a level too dark and left 255
    almost unused."""
    vals = np.array([[0.0, 0.49, 0.51, 254.6, 255.0]]) / 255.0
    assert imaging.window_to_grey(vals, 0.0, 1.0).tolist() == [[0, 0, 1, 255, 255]]


def test_values_outside_the_window_are_black_or_white():
    assert imaging.window_to_grey(np.array([[-5.0, 100.0, 500.0]]), 0, 255).tolist() \
        == [[0, 100, 255]]


def test_shrinking_averages_the_stored_values():
    """Each screen pixel shows the mean of the scan pixels it covers — no
    sharpening filter, so no halos that are not in the scan."""
    vals = np.array([[0, 2, 10, 20], [4, 6, 30, 40]], dtype=float)
    out = imaging.shrink_by_averaging(vals, (2, 1))
    assert np.allclose(out, [[3.0, 25.0]])


def test_shrinking_never_enlarges():
    vals = _scan(shape=(50, 60))
    assert imaging.shrink_by_averaging(vals, (600, 500)).shape == (50, 60)


def test_a_degenerate_window_does_not_divide_by_zero():
    out = imaging.window_to_grey(np.array([[1.0, 2.0]]), 5.0, 5.0)
    assert out.dtype == np.uint8


def test_render_is_window_then_average_then_encode():
    vals = _scan(shape=(200, 300))
    data, _ = imaging.render(vals, 1500, 2800, (150, 100), "png")
    expected = imaging.window_to_grey(imaging.shrink_by_averaging(vals, (150, 100)),
                                      1500, 2800)
    assert np.array_equal(imaging.decode(data), expected)


def test_a_zoom_level_is_the_mean_of_exactly_the_block_it_covers():
    """Level pixels sit on the scan's own grid, so a position at level L
    times 2**L is a scan position — the measuring areas stay aligned."""
    vals = np.arange(16, dtype=float).reshape(4, 4)
    assert imaging.block_average(vals, 2).tolist() == [[2.5, 4.5], [10.5, 12.5]]
    assert imaging.block_average(vals, 4).tolist() == [[7.5]]
    assert imaging.block_average(vals, 1) is not None


def test_edge_blocks_the_scan_does_not_fill_average_what_is_there():
    vals = np.arange(15, dtype=float).reshape(3, 5)
    got = imaging.block_average(vals, 2)
    assert got.shape == (2, 3)
    assert got[0, 2] == np.mean([4, 9])          # right edge: one column
    assert got[1, 0] == np.mean([10, 11])        # bottom edge: one row
    assert got[1, 2] == 14.0                     # the corner: one pixel


def test_tiles_cover_a_level_and_stop_at_its_edge():
    t = imaging.TILE
    assert imaging.tile_bounds((600, 300), 0, 0) == (0, t, 0, t)
    assert imaging.tile_bounds((600, 300), 1, 2) == (2 * t, 600, t, 300)
    for tx, ty in ((2, 0), (0, 3), (-1, 0), (0, -1)):
        assert imaging.tile_bounds((600, 300), tx, ty) is None


def test_a_non_grey_picture_is_not_quietly_reduced_to_one_channel():
    rgb = np.zeros((4, 4, 3), np.uint8)
    rgb[..., 1] = 9
    data, _ = imaging.encode_lossless_rgb(rgb, "png")
    assert imaging.decode(data).ndim == 3
