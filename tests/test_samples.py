"""Integration tests on the two reference DICOM scans (skipped when absent)."""

import numpy as np
import pytest

from conftest import needs_samples

from phantom_qa import pipeline


@pytest.fixture(scope="module")
def analyzed(sample_scans, pdef):
    out = []
    for scan in sample_scans:
        reg = pipeline.run_stage_a(scan, pdef)
        ctx = pipeline.build_ctx(scan, pdef, reg, {"scan_meta": scan.meta})
        geom = pipeline.propose_all(ctx)
        res = pipeline.compute_all(ctx, geom)
        out.append((scan, reg, geom, res))
    return out


@needs_samples
def test_registration_orientation(analyzed):
    for scan, reg, geom, res in analyzed:
        assert abs(abs(reg.transform.rotation_deg) - 180) < 2.0
        assert not reg.transform.mirrored
        assert reg.residual_rms_mm < 1.0
        assert 0.13 < reg.transform.mm_per_px < 0.16


@needs_samples
def test_scale_and_dimensions(analyzed):
    for scan, reg, geom, res in analyzed:
        g = res["geometry"]
        assert abs(g["scale"]["pitch_measured_mm"] - 5.0) < 0.05
        assert 298.5 < g["dimensions"]["mean_side_mm"] < 300.5
        assert abs(g["central_line_separations"]["vertical_mm"] - 260) < 1.0
        assert abs(g["central_line_separations"]["horizontal_mm"] - 260) < 1.0
        # all four rulers detected with sane linearity
        for side, r in g["rulers"].items():
            assert r["detected"], f"ruler {side} not detected"
            assert r["linearity_rms_mm"] < 0.25


@needs_samples
def test_linepair_frequencies(analyzed):
    for scan, reg, geom, res in analyzed:
        for row in res["linepairs"]["rows"]:
            lin = row["linearity"]
            assert lin.get("measured_pitch_mm") is not None, row["id"]
            nominal = 1.0 / row["freq_lp_mm"]
            dev = abs(lin["measured_pitch_mm"] - nominal) / nominal
            assert dev < 0.04, f"{row['id']} pitch dev {dev:.3f}"
            assert lin["used_lines"] >= 10
            assert lin["residual_rms_mm"] < 0.08


@needs_samples
def test_wedge_monotonic(analyzed):
    for scan, reg, geom, res in analyzed:
        w = res["wedge"]
        assert w["monotonic"]
        assert w["fit"]["r2"] > 0.85
        means = [r["mean"] for r in w["rows"]]
        assert means[0] > means[-1]          # S1 (top) most attenuating
        assert not any(r["saturated"] for r in w["rows"])


@needs_samples
def test_uniformity(analyzed):
    for scan, reg, geom, res in analyzed:
        u = res["uniformity"]
        assert len(u["rows"]) == 5
        assert u["max_abs_dsnr_pct"] < 10.0
        assert u["status"] == "pass"


@needs_samples
def test_lowcontrast_ordering(analyzed):
    for scan, reg, geom, res in analyzed:
        rows = {r["level"]: r for r in res["lowcontrast"]["rows"]}
        # strongest circle must clearly beat the mid one, mid beats weakest
        assert rows[8]["abs_cnr"] > rows[5]["abs_cnr"] > rows[2]["abs_cnr"] - 0.15
        assert rows[8]["abs_cnr"] > 0.5


@needs_samples
def test_cross_scan_consistency(analyzed):
    """The two exposures of the same phantom must agree on geometry."""
    if len(analyzed) < 2:
        pytest.skip("need both scans")
    g0 = analyzed[0][3]["geometry"]
    g1 = analyzed[1][3]["geometry"]
    assert abs(g0["dimensions"]["mean_side_mm"]
               - g1["dimensions"]["mean_side_mm"]) < 1.0
    lp0 = {r["id"]: r for r in analyzed[0][3]["linepairs"]["rows"]}
    lp1 = {r["id"]: r for r in analyzed[1][3]["linepairs"]["rows"]}
    for gid in lp0:
        p0 = lp0[gid]["linearity"]["measured_pitch_mm"]
        p1 = lp1[gid]["linearity"]["measured_pitch_mm"]
        assert abs(p0 - p1) / p0 < 0.01, f"{gid}: {p0} vs {p1}"


@needs_samples
def test_rotation_invariance(sample_scans, pdef):
    """Rotating the input image 90 deg must not change measured geometry."""
    from phantom_qa.ingest import ScanData
    scan = sample_scans[0]
    rot = ScanData(pixels=np.ascontiguousarray(np.rot90(scan.pixels)),
                   meta=scan.meta, sha256=scan.sha256,
                   source_name=scan.source_name, kind=scan.kind,
                   reduced_precision=scan.reduced_precision,
                   spacing_candidates=scan.spacing_candidates)
    reg = pipeline.run_stage_a(rot, pdef)
    ctx = pipeline.build_ctx(rot, pdef, reg, {"scan_meta": rot.meta})
    geom = pipeline.propose_all(ctx)
    res = pipeline.compute_all(ctx, geom)
    g = res["geometry"]
    assert abs(g["scale"]["pitch_measured_mm"] - 5.0) < 0.05
    assert 298.5 < g["dimensions"]["mean_side_mm"] < 300.5
    for row in res["linepairs"]["rows"]:
        lin = row["linearity"]
        nominal = 1.0 / row["freq_lp_mm"]
        assert lin.get("measured_pitch_mm") is not None
        assert abs(lin["measured_pitch_mm"] - nominal) / nominal < 0.04
