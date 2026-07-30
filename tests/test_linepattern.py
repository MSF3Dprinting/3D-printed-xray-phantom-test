"""Line-pattern linearity extraction on synthetic gratings with known pitch."""

import numpy as np
import pytest

from phantom_qa.analysis.linepairs import _analyze_profile
from phantom_qa.analysis.common import Ctx, segment
from phantom_qa.phantom_def import PhantomDef
from phantom_qa.registration import Transform


def make_ctx(img, px_per_mm=7.0):
    T = Transform(A=np.array([[px_per_mm, 0.0], [0.0, -px_per_mm]]),
                  t=np.array([img.shape[1] / 2.0, img.shape[0] / 2.0]))
    pdef = PhantomDef(
        name="t", version="t", side_mm=100, nominal_side_mm=100, rulers={},
        corner_marks={}, uniformity={}, linepairs={}, lowcontrast={}, wedge={},
        tolerances={})
    return Ctx(pixels=img, T=T, pdef=pdef)


@pytest.mark.parametrize("pitch_mm", [0.9091, 0.625, 0.5])
def test_grating_pitch_recovery(pitch_mm, rng):
    px_per_mm = 7.0
    n = 700
    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    x_mm = (xx - n / 2) / px_per_mm
    img = 2400 + 600 * np.cos(2 * np.pi * x_mm / pitch_mm)
    img += rng.normal(0, 40.0, img.shape)
    ctx = make_ctx(img, px_per_mm)
    seg = segment(ctx, (-8.0, 0.0), (8.0, 0.0), "t")
    res = _analyze_profile(ctx, seg, freq_lp_mm=1.0 / pitch_mm,
                           center_mm=(0.0, 0.0))
    assert res.get("measured_pitch_mm") is not None
    err_pct = 100 * abs(res["measured_pitch_mm"] - pitch_mm) / pitch_mm
    assert err_pct < 1.0, f"pitch error {err_pct:.2f}%"
    assert res["residual_rms_mm"] < 0.05


def test_grating_orientation_robustness(rng):
    """Pattern direction off by 6 degrees from the nominal profile axis —
    the 2-D FFT direction measurement must absorb it."""
    px_per_mm = 7.0
    pitch_mm = 0.7143
    n = 700
    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    a = np.deg2rad(6.0)
    u_mm = ((xx - n / 2) * np.cos(a) + (yy - n / 2) * np.sin(a)) / px_per_mm
    img = 2400 + 600 * np.cos(2 * np.pi * u_mm / pitch_mm)
    img += rng.normal(0, 40.0, img.shape)
    ctx = make_ctx(img, px_per_mm)
    seg = segment(ctx, (-8.0, 0.0), (8.0, 0.0), "t")
    res = _analyze_profile(ctx, seg, freq_lp_mm=1.0 / pitch_mm,
                           center_mm=(0.0, 0.0))
    assert res.get("measured_pitch_mm") is not None
    err_pct = 100 * abs(res["measured_pitch_mm"] - pitch_mm) / pitch_mm
    assert err_pct < 1.5, f"pitch error {err_pct:.2f}%"
