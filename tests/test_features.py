"""Unit tests for sub-pixel measurement primitives on synthetic ground truth."""

import numpy as np
import pytest

from phantom_qa.features import (centroid_peak, edge_position, find_peaks_1d,
                                 fit_line_tls, linear_fit_r2,
                                 roi_stats_circle, roi_stats_rect,
                                 sample_profile)


def test_edge_position_subpixel(rng):
    # error-function edge at exactly x = 50.3
    x = np.arange(120, dtype=float)
    from scipy.special import erf
    y = 100 + 900 * 0.5 * (1 + erf((x - 50.3) / 2.5))
    y += rng.normal(0, 3.0, x.size)
    e = edge_position(y, rising=True, smooth_sigma=2.0)
    assert abs(e - 50.3) < 0.3


def test_find_peaks_and_centroid():
    x = np.arange(300, dtype=float)
    y = np.zeros_like(x)
    truth = [40.5, 90.2, 140.8, 190.1, 240.6]
    for t in truth:
        y += 1000 * np.exp(-0.5 * ((x - t) / 2.0) ** 2)
    pk = find_peaks_1d(y, min_distance=10, threshold_rel=0.3)
    assert len(pk) == len(truth)
    for p, t in zip(pk, truth):
        c = centroid_peak(y, p, half=4)
        assert abs(c - t) < 0.15


def test_sample_profile_line():
    img = np.tile(np.arange(100, dtype=float), (50, 1))   # value == x
    ts, vals = sample_profile(img, (10, 25), (90, 25), 81)
    assert np.allclose(vals, np.linspace(10, 90, 81), atol=0.01)


def test_roi_stats_rect_exact(rng):
    img = np.full((200, 200), 7.0)
    img[80:120, 60:140] = 21.0
    s = roi_stats_rect(img, center=(99.5, 99.5), size=(60, 30))
    assert s["mean"] == pytest.approx(21.0)
    assert s["std"] == pytest.approx(0.0, abs=1e-9)
    # n approximately area (60*30)
    assert abs(s["n"] - 1800) < 130


def test_roi_stats_circle_center_value():
    img = np.zeros((100, 100))
    img[40:60, 40:60] = 5.0
    s = roi_stats_circle(img, center=(49.5, 49.5), radius=8)
    assert s["mean"] == pytest.approx(5.0)


def test_fit_line_tls():
    pts = np.array([[0, 1.0], [1, 3.0], [2, 5.0], [3, 7.0]])
    c, u = fit_line_tls(pts)
    slope = u[1] / u[0]
    assert slope == pytest.approx(2.0, abs=1e-9)


def test_linear_fit_r2_perfect():
    x = np.arange(7, dtype=float)
    y = 3.5 * x - 2.0
    f = linear_fit_r2(x, y)
    assert f["a"] == pytest.approx(3.5)
    assert f["b"] == pytest.approx(-2.0)
    assert f["r2"] == pytest.approx(1.0)


def test_linear_fit_r2_noisy(rng):
    x = np.arange(50, dtype=float)
    y = 2.0 * x + rng.normal(0, 20.0, x.size)
    f = linear_fit_r2(x, y)
    assert 0.6 < f["r2"] < 1.0
