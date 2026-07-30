"""Field-edge detection must find real collimation steps and reject gradients."""

import numpy as np
import pytest

from phantom_qa.analysis.common import Ctx
from phantom_qa.analysis.geometry import _field_edge
from phantom_qa.phantom_def import load_default
from phantom_qa.registration import Transform


def make_ctx(img, px_per_mm, pdef):
    """Phantom centered in the image, canonical orientation (y-up -> y-down)."""
    T = Transform(A=np.array([[px_per_mm, 0.0], [0.0, -px_per_mm]]),
                  t=np.array([img.shape[1] / 2.0, img.shape[0] / 2.0]))
    return Ctx(pixels=img, T=T, pdef=pdef)


def synth_scan(pdef, px_per_mm=3.0, margin_mm=90.0, field_edge_mm=None,
               gradient=False, seed=3):
    """Phantom square + optional collimation step / scatter gradient outside it.

    field_edge_mm: distance outside the phantom edge where the beam is blocked.
    gradient:      instead add a smooth ramp (the scatter-only case).
    """
    rng = np.random.default_rng(seed)
    side_px = pdef.side_mm * px_per_mm
    n = int(side_px + 2 * margin_mm * px_per_mm)
    img = np.full((n, n), 250.0)                      # direct exposure (dark)
    c = n / 2.0
    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    dist_out = np.maximum(np.abs(xx - c), np.abs(yy - c)) - side_px / 2
    img[dist_out <= 0] = 2400.0                       # phantom face (bright)
    if field_edge_mm is not None:
        blocked = dist_out > field_edge_mm * px_per_mm
        img[blocked] = 3900.0                         # unexposed = high value
    if gradient:
        out = dist_out > 0
        img[out] += 220.0 * np.clip(dist_out[out] / (margin_mm * px_per_mm), 0, 1)
    img += rng.normal(0, 8.0, img.shape)
    return img


@pytest.fixture(scope="module")
def pdef_():
    return load_default()


@pytest.mark.parametrize("edge_mm", [25.0, 40.0, 60.0])
def test_real_collimation_edge_detected(pdef_, edge_mm):
    px_per_mm = 3.0
    img = synth_scan(pdef_, px_per_mm, margin_mm=90.0, field_edge_mm=edge_mm)
    ctx = make_ctx(img, px_per_mm, pdef_)
    for side in ("top", "right", "bottom", "left"):
        f = _field_edge(ctx, side)
        assert f["detected"], f"{side}: {f.get('reason')}"
        assert abs(f["offset_from_edge_mm"] - edge_mm) < 2.0, \
            f"{side}: got {f['offset_from_edge_mm']:.1f}, expected {edge_mm}"


def test_scatter_gradient_rejected(pdef_):
    """A smooth ramp outside the phantom must NOT be reported as a field edge."""
    px_per_mm = 3.0
    img = synth_scan(pdef_, px_per_mm, margin_mm=90.0, gradient=True)
    ctx = make_ctx(img, px_per_mm, pdef_)
    for side in ("top", "right", "bottom", "left"):
        f = _field_edge(ctx, side)
        assert not f["detected"], \
            f"{side}: gradient wrongly reported at {f.get('offset_from_edge_mm')}"
        assert "reason" in f


def test_short_probe_rejected(pdef_):
    """Phantom nearly filling the detector -> alignment is not measurable."""
    px_per_mm = 3.0
    img = synth_scan(pdef_, px_per_mm, margin_mm=12.0)
    ctx = make_ctx(img, px_per_mm, pdef_)
    for side in ("top", "right", "bottom", "left"):
        f = _field_edge(ctx, side)
        assert not f["detected"]
        assert "reason" in f
