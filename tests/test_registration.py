"""Registration tests on synthetic phantoms with exactly known geometry."""

import numpy as np
import pytest

from phantom_qa.registration import (Transform, detect_phantom_rect, fit_affine)


def synth_square(angle_deg=0.0, side_px=900, img=1400, bright=3000, bg=250,
                 noise=15.0, seed=1):
    """Bright rotated square on dark background; returns (image, corner list)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:img, 0:img].astype(float)
    c = img / 2
    a = np.deg2rad(angle_deg)
    lx = (xx - c) * np.cos(a) + (yy - c) * np.sin(a)
    ly = -(xx - c) * np.sin(a) + (yy - c) * np.cos(a)
    inside = (np.abs(lx) <= side_px / 2) & (np.abs(ly) <= side_px / 2)
    im = np.where(inside, bright, bg).astype(float)
    im += rng.normal(0, noise, im.shape)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    local = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]]) * side_px / 2
    corners = local @ R.T + c
    return im, corners


@pytest.mark.parametrize("angle", [0.0, 1.7, -3.2, 12.0])
def test_detect_phantom_rect_accuracy(angle):
    im, truth = synth_square(angle_deg=angle)
    rect = detect_phantom_rect(im)
    got = rect["corners"]
    # match each truth corner to nearest detected corner
    for t in truth:
        d = np.min(np.linalg.norm(got - t, axis=1))
        assert d < 1.0, f"corner error {d:.2f}px at angle {angle}"


def test_fit_affine_roundtrip():
    src = np.array([[-150, 150], [150, 150], [150, -150], [-150, -150]], float)
    A_true = np.array([[7.0, 0.1], [-0.1, -7.0]])
    t_true = np.array([1400.0, 1450.0])
    dst = src @ A_true.T + t_true
    T = fit_affine(src, dst)
    assert np.allclose(T.A, A_true, atol=1e-9)
    assert np.allclose(T.t, t_true, atol=1e-9)
    back = T.px_to_mm(T.mm_to_px([12.5, -33.25]))
    assert np.allclose(back, [12.5, -33.25], atol=1e-9)


def test_transform_properties():
    # 0-deg placement, y-flip only (not mirrored physically)
    T = Transform(A=np.array([[7.0, 0.0], [0.0, -7.0]]), t=np.zeros(2))
    assert not T.mirrored
    assert T.px_per_mm == pytest.approx(7.0)
    # 180-deg placement
    T2 = Transform(A=np.array([[-7.0, 0.0], [0.0, 7.0]]), t=np.zeros(2))
    assert not T2.mirrored
    assert abs(abs(T2.rotation_deg) - 180.0) < 1e-6
    # mirrored placement
    T3 = Transform(A=np.array([[7.0, 0.0], [0.0, 7.0]]), t=np.zeros(2))
    assert T3.mirrored
