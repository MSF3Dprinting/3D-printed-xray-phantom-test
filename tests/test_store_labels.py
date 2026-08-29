"""Labels, filtering, schema migration, wide export and comparison report."""

import json
import sqlite3
import types

import pytest

from phantom_qa.comparison_report import build_comparison_report
from phantom_qa.store import Store, _acquired_at, csv_export, wide_csv_export


def fake_scan(meta=None, sha="a" * 64, name="scan.dcm"):
    return types.SimpleNamespace(
        source_name=name, sha256=sha, kind="dicom", reduced_precision=False,
        meta=meta if meta is not None else {"StudyDate": "20260727",
                                            "SeriesTime": "093726.000000"})


def results_for(sd=100.0, cnr=-0.8, snr=47.0, wedge_mean=3200.0):
    return {
        "linepairs": {"status": "pass", "rows": [
            {"id": "G2.0", "freq_lp_mm": 2.0, "mean": 2000.0, "std": sd,
             "status": "pass",
             "linearity": {"measured_pitch_mm": 0.504, "pitch_dev_pct": 0.8,
                           "residual_rms_mm": 0.02, "used_lines": 30,
                           "measured_freq_lp_mm": 1.98}}]},
        "lowcontrast": {"status": "pass", "rows": [
            {"id": "L8", "level": 8, "obj_mean": 2400.0, "obj_std": 40.0,
             "bg_mean": 2440.0, "bg_std": 40.0, "cnr": cnr,
             "abs_cnr": abs(cnr)}]},
        "uniformity": {"status": "pass", "snr_avg": snr, "mean_avg": 2000.0,
                       "max_abs_dsnr_pct": 2.0, "tolerance_pct": 20.0,
                       "rows": [{"id": "C", "mean": 2000.0, "std": 42.0,
                                 "snr": snr, "dsnr_pct": 1.0, "dmean_pct": 0.5,
                                 "status": "pass"}]},
        "wedge": {"status": "pass", "monotonic": True,
                  "dynamic_range_ratio": 4.1, "span": 2700.0, "r2_min": 0.85,
                  "fit": {"slope": -450.0, "intercept": 4000.0, "r2": 0.92,
                          "residuals_pct_of_span": [0.0]},
                  "rows": [{"step": 1, "mean": wedge_mean, "std": 20.0,
                            "n": 100, "saturated": False}]},
        "geometry": {"dimension_status": "pass", "field_status": "n/a",
                     "rulers": {}, "dimensions": {}, "field_alignment": {},
                     "central_line_separations": {}, "scale": {}},
    }


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path))


def add(store, site, phantom, sha, results=None, acquired=("20260727", "0900")):
    meta = {"StudyDate": acquired[0], "SeriesTime": acquired[1] + "00.000"}
    aid = store.new_analysis(fake_scan(meta, sha), b"x" * 16, "sig-A",
                             "1.0.0", "1.0",
                             labels={"site": site, "phantom": phantom,
                                     "operator": "op"})
    if results is not None:
        store.update(aid, results=results, status="pass")
    return aid


# ------------------------------------------------------------------- labels

def test_labels_are_stored_and_returned(store):
    aid = add(store, "Goma", "MSF-01", "a" * 64)
    rec = store.get(aid)
    assert rec["site"] == "Goma"
    assert rec["phantom"] == "MSF-01"
    assert rec["operator"] == "op"


def test_labels_are_trimmed(store):
    aid = store.new_analysis(fake_scan(), b"x", "sig", "1", "1",
                             labels={"site": "  Goma  ", "phantom": " P1 "})
    rec = store.get(aid)
    assert rec["site"] == "Goma" and rec["phantom"] == "P1"


def test_missing_labels_default_to_empty(store):
    aid = store.new_analysis(fake_scan(), b"x", "sig", "1", "1")
    rec = store.get(aid)
    assert rec["site"] == "" and rec["phantom"] == ""


def test_label_listing_counts(store):
    add(store, "Goma", "MSF-01", "a" * 64)
    add(store, "Goma", "MSF-02", "b" * 64)
    add(store, "Bunia", "MSF-01", "c" * 64)
    lab = store.labels()
    assert {x["value"]: x["count"] for x in lab["site"]} == {"Goma": 2, "Bunia": 1}
    assert {x["value"]: x["count"] for x in lab["phantom"]} == {"MSF-01": 2,
                                                               "MSF-02": 1}


def test_set_labels_updates_existing(store):
    aid = add(store, "", "", "a" * 64)
    store.set_labels(aid, {"site": "Goma", "phantom": "MSF-09"})
    rec = store.get(aid)
    assert rec["site"] == "Goma" and rec["phantom"] == "MSF-09"


def test_forgotten_labels_can_be_added_afterwards(store):
    """The scan is uploaded without a site, so it is invisible to grouped
    trends; adding the label later must fully recover it."""
    aid = add(store, "", "", "a" * 64, results_for())
    assert store.list_all(site="Goma") == []
    assert store.labels()["site"] == []

    store.set_labels(aid, {"site": "Goma", "phantom": "MSF-01"})

    assert [r["id"] for r in store.list_all(site="Goma")] == [aid]
    assert [r["id"] for r in store.list_all(phantom="MSF-01")] == [aid]
    assert store.labels()["site"] == [{"value": "Goma", "count": 1}]
    # and it now carries into the exports
    recs = [store.get(aid)]
    assert "Goma" in csv_export(recs)
    assert "Goma" in build_comparison_report(recs)


def test_partial_label_edit_leaves_other_fields_alone(store):
    aid = add(store, "Goma", "MSF-01", "a" * 64)
    store.set_labels(aid, {"phantom": "MSF-07"})
    rec = store.get(aid)
    assert rec["phantom"] == "MSF-07"
    assert rec["site"] == "Goma", "site must survive a phantom-only edit"
    assert rec["operator"] == "op"


def test_labels_can_be_cleared(store):
    aid = add(store, "Goma", "MSF-01", "a" * 64)
    store.set_labels(aid, {"site": "", "phantom": ""})
    rec = store.get(aid)
    assert rec["site"] == "" and rec["phantom"] == ""


# ----------------------------------------------------------------- filtering

def test_filter_by_site_and_phantom(store):
    add(store, "Goma", "MSF-01", "a" * 64)
    add(store, "Goma", "MSF-02", "b" * 64)
    add(store, "Bunia", "MSF-01", "c" * 64)
    assert len(store.list_all(site="Goma")) == 2
    assert len(store.list_all(phantom="MSF-01")) == 2
    assert len(store.list_all(site="Goma", phantom="MSF-01")) == 1
    assert len(store.list_all()) == 3


def test_completed_only_filter(store):
    add(store, "Goma", "MSF-01", "a" * 64)                       # no results
    add(store, "Goma", "MSF-01", "b" * 64, results=results_for())
    assert len(store.list_all(site="Goma")) == 2
    assert len(store.list_all(site="Goma", completed_only=True)) == 1


def test_listing_is_ordered_by_acquisition_time(store):
    add(store, "S", "P", "a" * 64, results_for(), acquired=("20260701", "1000"))
    add(store, "S", "P", "b" * 64, results_for(), acquired=("20260801", "1000"))
    rows = store.list_all(site="S")
    assert rows[0]["acquired_at"].startswith("2026-08-01")   # newest first


# --------------------------------------------------------------- acquired_at

@pytest.mark.parametrize("meta,expected", [
    ({"StudyDate": "20260727", "SeriesTime": "093726.000000"},
     "2026-07-27 09:37:26"),
    ({"StudyDate": "20260727"}, "2026-07-27 00:00:00"),
    # a valid DICOM TM may carry only HHMM — reading it as midnight would lose
    # real information and leave same-day scans unorderable
    ({"StudyDate": "20260727", "AcquisitionTime": "1415"},
     "2026-07-27 14:15:00"),
    ({}, ""),
    ({"StudyDate": "bad"}, ""),
    # the date falls back through the header's other date tags
    ({"AcquisitionDate": "20260727", "SeriesTime": "093726"},
     "2026-07-27 09:37:26"),
    ({"ContentDate": "20260727"}, "2026-07-27 00:00:00"),
    # pydicom hands back a MultiValue for a multi-valued tag; str() on the list
    # used to yield "['20260727']" and silently produce no date at all
    ({"StudyDate": ["20260727"], "SeriesTime": ["093726.0"]},
     "2026-07-27 09:37:26"),
])
def test_acquired_at_parsing(meta, expected):
    assert _acquired_at(meta) == expected


def test_acquired_at_used_for_ordering_not_upload_time(store):
    aid = add(store, "S", "P", "a" * 64, acquired=("20260101", "0800"))
    rec = store.get(aid)
    assert rec["acquired_at"].startswith("2026-01-01")
    assert not rec["created_at"].startswith("2026-01-01")


# --------------------------------------------------------------- migration

def test_migrates_a_database_without_label_columns(tmp_path):
    """An existing pre-labels database must gain the columns, not lose rows."""
    db = tmp_path / "data" / "phantom_qa.sqlite3"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE analyses (
          id TEXT PRIMARY KEY, created_at TEXT, source_name TEXT, sha256 TEXT,
          kind TEXT, reduced_precision INTEGER, signature TEXT, meta_json TEXT,
          stage TEXT, reg_json TEXT, geometry_json TEXT, results_json TEXT,
          audit_json TEXT, sid_mm REAL, is_baseline INTEGER DEFAULT 0,
          algo_version TEXT, pdef_version TEXT, status TEXT);
    """)
    con.execute("INSERT INTO analyses (id, created_at, source_name, sha256,"
                " meta_json, results_json) VALUES (?,?,?,?,?,?)",
                ("old1", "2026-01-01 00:00:00", "legacy.dcm", "f" * 64,
                 json.dumps({}), json.dumps(results_for())))
    con.commit()
    con.close()

    s = Store(str(tmp_path))                       # triggers migration
    rec = s.get("old1")
    assert rec is not None, "existing row must survive the migration"
    assert rec["site"] == "" and rec["phantom"] == ""
    s.set_labels("old1", {"site": "Legacy", "phantom": "P0"})
    assert s.get("old1")["site"] == "Legacy"
    assert len(s.list_all(site="Legacy")) == 1


# ------------------------------------------------------------------ exports

def test_long_csv_includes_labels(store):
    add(store, "Goma", "MSF-01", "a" * 64, results_for())
    recs = [store.get(r["id"]) for r in store.list_all()]
    out = csv_export(recs)
    header = out.splitlines()[0]
    assert "site" in header and "phantom" in header and "acquired_at" in header
    assert "Goma" in out and "MSF-01" in out


def test_wide_csv_has_one_column_per_analysis(store):
    add(store, "Goma", "MSF-01", "a" * 64, results_for(sd=100),
        acquired=("20260701", "0900"))
    add(store, "Goma", "MSF-01", "b" * 64, results_for(sd=120),
        acquired=("20260801", "0900"))
    recs = [store.get(r["id"]) for r in store.list_all()]
    out = wide_csv_export(recs)
    lines = out.splitlines()
    assert lines[0].startswith("test,object,metric,unit,")
    assert len(lines[0].split(",")) == 6, "4 fixed columns + 2 analyses"
    sd_line = [ln for ln in lines if ln.startswith("linepairs,G2.0,sd,")][0]
    cells = sd_line.split(",")
    assert [float(x) for x in cells[-2:]] == [100.0, 120.0], \
        "oldest first, then newest"


def test_wide_csv_is_chronological_regardless_of_input_order(store):
    add(store, "S", "P", "b" * 64, results_for(sd=120), acquired=("20260801", "0900"))
    add(store, "S", "P", "a" * 64, results_for(sd=100), acquired=("20260701", "0900"))
    recs = [store.get(r["id"]) for r in store.list_all()]   # newest first
    out = wide_csv_export(recs)
    sd_line = [ln for ln in out.splitlines()
               if ln.startswith("linepairs,G2.0,sd,")][0]
    assert [float(x) for x in sd_line.split(",")[-2:]] == [100.0, 120.0]


# -------------------------------------------------------- comparison report

def test_comparison_report_contains_every_pattern(store):
    for i, sd in enumerate((100, 110, 130)):
        add(store, "Goma", "MSF-01", chr(97 + i) * 64, results_for(sd=sd),
            acquired=(f"2026070{i+1}", "0900"))
    recs = [store.get(r["id"]) for r in store.list_all()]
    html_out = build_comparison_report(recs, filters={"site": "Goma"})
    for section in ("Line patterns", "Low contrast", "Uniformity",
                    "Attenuation wedge", "Where the differences are",
                    "What is being compared"):
        assert section in html_out, f"missing section: {section}"
    assert "Goma" in html_out
    assert "MSF-01" in html_out


def test_comparison_report_is_mostly_plots(store):
    """Long numeric tables were the complaint; the page must lead with images
    and keep the numbers behind a collapsed <details>."""
    for i in range(4):
        add(store, "Goma", f"MSF-0{i}", chr(97 + i) * 64,
            results_for(sd=100 + 10 * i))
    recs = [store.get(r["id"]) for r in store.list_all()]
    out = build_comparison_report(recs)
    n_images = out.count("data:image/png;base64,")
    assert n_images >= 6, f"expected several plots, got {n_images}"
    assert out.count("<details>") >= 4, "numeric tables should be collapsible"
    # every metric table lives inside a <details>
    assert out.index("data:image/png;base64,") < out.index("<details>")


def test_comparison_report_scales_to_many_phantoms(store):
    """Ten different phantoms, one scan each — no time series at all."""
    for i in range(10):
        add(store, "Site-A" if i < 5 else "Site-B", f"MSF-{i:02d}",
            f"{i:064d}", results_for(sd=90 + 4 * i, cnr=-0.5 - 0.02 * i))
    recs = [store.get(r["id"]) for r in store.list_all()]
    out = build_comparison_report(recs)
    assert "10" in out and "phantom(s)" in out
    for i in range(10):
        assert f"MSF-{i:02d}" in out, f"MSF-{i:02d} missing from the report"
    assert "Site-A" in out and "Site-B" in out


def test_comparison_report_uses_median_not_first_entry(store):
    """With many phantoms there is no meaningful 'first', so the reference is
    the median of the selection."""
    add(store, "S", "P1", "a" * 64, results_for(sd=100))
    add(store, "S", "P2", "b" * 64, results_for(sd=100))
    add(store, "S", "P3", "c" * 64, results_for(sd=300))
    recs = [store.get(r["id"]) for r in store.list_all()]
    out = build_comparison_report(recs)
    assert "median of this selection" in out
    assert "Where the differences are" in out


def test_percentage_metrics_do_not_explode(store):
    """Metrics that are already percentages (pitch deviation, ΔSNR) sit around
    zero. Ranking them by 'range / median' produced 13 000 % artefacts, so they
    must be handled in percentage points instead."""
    from phantom_qa.comparison_report import _collect, _variability_chart

    def res(pitch_dev):
        r = results_for()
        r["linepairs"]["rows"][0]["linearity"]["pitch_dev_pct"] = pitch_dev
        return r

    # median pitch_dev is ~0, spread is 0.4 pp — must not become a huge number
    for i, dev in enumerate((-0.2, 0.0, 0.2)):
        add(store, "S", f"P{i}", chr(97 + i) * 64, res(dev))
    recs = [store.get(r["id"]) for r in store.list_all()]
    ordered, keys, maps = _collect(recs)
    _, rows = _variability_chart(keys, maps)
    ranked = {name: val for name, val in rows}
    pd_key = next(k for k in ranked if "pitch_dev" in k)
    assert ranked[pd_key] < 5.0, \
        f"pitch_dev spread reported as {ranked[pd_key]} (should be ~0.4 pp)"
    assert all(v < 1000 for v in ranked.values()), \
        f"implausible variability values: {ranked}"


def test_heatmap_drops_metrics_that_do_not_vary(store):
    """A metric identical everywhere must not be painted in full colour."""
    from phantom_qa.comparison_report import _collect, _deviation_heatmap
    for i in range(3):
        add(store, "S", f"P{i}", chr(97 + i) * 64, results_for())  # identical
    recs = [store.get(r["id"]) for r in store.list_all()]
    ordered, keys, maps = _collect(recs)
    labels = ["a", "b", "c"]
    assert _deviation_heatmap(ordered, labels, keys, maps, "uniformity") is None


def test_every_entity_label_carries_site_and_phantom(store):
    from phantom_qa.comparison_report import _entity_labels, _order
    add(store, "Goma", "MSF-01", "a" * 64, results_for())
    add(store, "Bunia", "MSF-02", "b" * 64, results_for())
    recs = _order([store.get(r["id"]) for r in store.list_all()])
    labels = _entity_labels(recs)
    assert any("Goma" in l and "MSF-01" in l for l in labels)
    assert any("Bunia" in l and "MSF-02" in l for l in labels)


def test_repeated_phantom_labels_are_disambiguated(store):
    from phantom_qa.comparison_report import _entity_labels, _order
    add(store, "Goma", "MSF-01", "a" * 64, results_for(),
        acquired=("20260701", "0900"))
    add(store, "Goma", "MSF-01", "b" * 64, results_for(),
        acquired=("20260801", "0900"))
    recs = _order([store.get(r["id"]) for r in store.list_all()])
    labels = _entity_labels(recs)
    assert len(set(labels)) == 2, f"labels collided: {labels}"
    assert all("MSF-01" in l for l in labels)
    assert any("2026-07-01" in l for l in labels)


def test_unlabelled_analyses_still_get_a_usable_label(store):
    from phantom_qa.comparison_report import _entity_labels, _order
    add(store, "", "", "a" * 64, results_for())
    recs = _order([store.get(r["id"]) for r in store.list_all()])
    labels = _entity_labels(recs)
    assert labels and labels[0].startswith("(unlabelled")


def test_comparison_report_handles_empty_selection():
    out = build_comparison_report([])
    assert "No completed analyses" in out


def test_comparison_report_skips_analyses_without_results(store):
    done = add(store, "S", "P", "a" * 64, results_for())
    draft = add(store, "S", "P", "b" * 64)            # draft, no results
    recs = [store.get(r["id"]) for r in store.list_all()]
    out = build_comparison_report(recs)
    assert "<b>1</b>analyses" in out, "only the completed analysis counts"
    assert done[:8] in out
    assert draft[:8] not in out
