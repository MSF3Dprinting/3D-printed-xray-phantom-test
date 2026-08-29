"""Two dates, kept apart.

A scanner's clock is not this server's clock. It can be unset, reset, or simply
wrong, and nothing in DICOM records a timezone. With one date column that
silently fell back to the upload time, a scan taken in March and one taken by a
detector reporting 1980 looked exactly alike — and on a large dataset there was
no way back to the real order. So the acquisition time and the upload time are
now stored, transported, exported and displayed separately, and a scan whose
acquisition time cannot be trusted says so.
"""

from __future__ import annotations

import hashlib
import io

import pytest
from fastapi.testclient import TestClient

from phantom_qa.store import (Store, acquired_or_uploaded, acquisition_flag,
                              csv_export, wide_csv_export)
from test_authorization import _build_app, _login
from test_store_labels import fake_scan, results_for


def _png(seed: int = 0) -> bytes:
    import numpy as np
    from PIL import Image
    arr = np.full((24, 24), 100 + (seed % 50), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path))


def _add(store, meta, seed=0, results=True, phantom="MSF-01"):
    payload = _png(seed)
    aid = store.new_analysis(
        fake_scan(meta, sha=hashlib.sha256(payload).hexdigest()), payload,
        "sig", "1.0.0", "1.0", labels={"site": "Goma", "phantom": phantom})
    if results:
        store.update(aid, results=results_for(), status="pass")
    return aid


# ------------------------------------------------- an unknown date stays unknown

def test_a_scan_with_no_header_date_records_no_acquisition_time(store):
    """It used to record the upload clock, which made the two dates identical
    and the distinction unrecoverable afterwards."""
    aid = _add(store, {})
    rec = store.get(aid)
    assert rec["acquired_at"] == ""
    assert rec["created_at"], "the upload time must still be recorded"


def test_a_plain_image_never_gets_a_fabricated_acquisition_date(store):
    aid = _add(store, {"Columns": 24, "Rows": 24})
    assert store.get(aid)["acquired_at"] == ""


def test_a_header_date_is_kept(store):
    aid = _add(store, {"StudyDate": "20260323", "SeriesTime": "115041"})
    assert store.get(aid)["acquired_at"] == "2026-03-23 11:50:41"


# ----------------------------------------------------------- the trust flag

@pytest.mark.parametrize("rec,expected", [
    ({"acquired_at": "", "created_at": "2026-08-01 10:00:00"}, "missing"),
    ({"acquired_at": None, "created_at": "2026-08-01 10:00:00"}, "missing"),
    # a detector whose clock was never set
    ({"acquired_at": "1980-01-01 00:00:00",
      "created_at": "2026-08-01 10:00:00"}, "implausible"),
    ({"acquired_at": "1970-01-01 00:00:00",
      "created_at": "2026-08-01 10:00:00"}, "implausible"),
    # a scan cannot have been taken after it was uploaded
    ({"acquired_at": "2027-01-01 10:00:00",
      "created_at": "2026-08-01 10:00:00"}, "implausible"),
    ({"acquired_at": "not a date", "created_at": "2026-08-01 10:00:00"},
     "implausible"),
    # normal
    ({"acquired_at": "2026-07-27 09:37:26",
      "created_at": "2026-08-01 10:00:00"}, ""),
    # a few hours "ahead" is a timezone, not an error — DICOM times are
    # scanner-local and nothing records the offset
    ({"acquired_at": "2026-08-01 18:00:00",
      "created_at": "2026-08-01 10:00:00"}, ""),
])
def test_acquisition_flag(rec, expected):
    assert acquisition_flag(rec) == expected


def test_the_fallback_for_ordering_is_explicit(store):
    assert acquired_or_uploaded({"acquired_at": "", "created_at": "X"}) == "X"
    assert acquired_or_uploaded({"acquired_at": "A", "created_at": "X"}) == "A"


# ------------------------------------------------------------ list and order

def test_the_listing_carries_both_dates_and_the_flag(store):
    _add(store, {"StudyDate": "20260323", "SeriesTime": "115041"})
    row = store.list_all()[0]
    assert row["acquired_at"] and row["created_at"]
    assert row["acquired_flag"] == ""


def test_a_dateless_scan_is_flagged_in_the_listing(store):
    _add(store, {})
    assert store.list_all()[0]["acquired_flag"] == "missing"


def test_the_listing_can_be_ordered_by_either_date(store):
    """The point of the upload column on a large dataset: when the scanner
    clock is wrong, upload order is the only order that is real."""
    # A detector claiming December, uploaded first; one claiming January,
    # uploaded second. The two orders must disagree.
    first = _add(store, {"StudyDate": "20261231"}, seed=1)
    second = _add(store, {"StudyDate": "20260101"}, seed=2)
    store.update(first, created_at="2026-08-01 09:00:00")
    store.update(second, created_at="2026-08-01 10:00:00")

    by_acq = [r["id"] for r in store.list_all(order_by="acquired")]
    assert by_acq == [first, second], "acquisition order ignores the header date"

    by_up = [r["id"] for r in store.list_all(order_by="uploaded")]
    assert by_up == [second, first], (
        "upload order must follow created_at, newest first")


def test_ordering_never_collapses_when_a_date_is_missing(store):
    a = _add(store, {}, seed=1)
    b = _add(store, {"StudyDate": "20260101"}, seed=2)
    ids = {r["id"] for r in store.list_all(order_by="acquired")}
    assert ids == {a, b}, "a scan without a header date fell out of the listing"


# ---------------------------------------------------------------- exports

def test_the_long_csv_carries_both_dates_and_the_flag(store):
    _add(store, {})
    recs = [store.get(i["id"]) for i in store.list_all()]
    text = csv_export(recs)
    header = text.splitlines()[0].split(",")
    assert "acquired_at" in header and "created_at" in header
    assert "acquired_flag" in header
    assert "missing" in text.splitlines()[1]


def test_the_wide_csv_carries_both_dates(store):
    _add(store, {"StudyDate": "20260323"}, seed=1)
    recs = [store.get(i["id"]) for i in store.list_all()]
    text = wide_csv_export(recs)
    assert "# acquired_at" in text
    assert "# created_at" in text, "the wide export never showed the upload date"
    assert "# acquired_flag" in text


def test_the_wide_csv_column_count_is_unchanged(store):
    """Extra label ROWS are safe; an extra fixed column would shift every
    consumer's parsing."""
    _add(store, {"StudyDate": "20260323"}, seed=1)
    _add(store, {"StudyDate": "20260324"}, seed=2)
    recs = [store.get(i["id"]) for i in store.list_all()]
    header = wide_csv_export(recs).splitlines()[0].split(",")
    assert len(header) == 6            # test, object, metric, unit + 2 analyses


# ------------------------------------------------------------------- HTTP

@pytest.fixture()
def mod(tmp_path, monkeypatch):
    return _build_app(tmp_path, monkeypatch)


@pytest.fixture()
def client(mod):
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    return c


def test_the_api_reports_both_dates_and_the_flag(client, mod):
    aid = _add(mod.store, {})
    row = client.get("/api/analyses").json()["analyses"][0]
    assert row["created_at"] and row["acquired_at"] == ""
    assert row["acquired_flag"] == "missing"

    rec = client.get(f"/api/analyses/{aid}").json()
    assert rec["acquired_flag"] == "missing"
    assert rec["created_at"]


def test_the_list_order_is_selectable_over_http(client, mod):
    _add(mod.store, {"StudyDate": "20261231"}, seed=1)
    _add(mod.store, {"StudyDate": "20260101"}, seed=2)
    r = client.get("/api/analyses?order=uploaded").json()
    assert r["order"] == "uploaded"
    stamps = [a["created_at"] for a in r["analyses"]]
    assert stamps == sorted(stamps, reverse=True)


def test_the_trend_payload_does_not_collapse_the_two_dates(client, mod):
    """It used to send `acquired_at or created_at`, so the chart could not tell
    a real acquisition date from a substituted upload time."""
    _add(mod.store, {})
    a = client.get("/api/trends").json()["analyses"][0]
    assert a["acquired_at"] == ""
    assert a["created_at"]
    assert a["acquired_flag"] == "missing"


def test_the_printable_report_shows_both_dates(client, mod):
    aid = _add(mod.store, {})
    html = client.get(f"/api/analyses/{aid}/report.html").text
    assert "Uploaded" in html
    assert "not recorded by the scanner" in html


def test_the_comparison_report_shows_both_dates(client, mod):
    _add(mod.store, {"StudyDate": "20260323"}, seed=1)
    _add(mod.store, {"StudyDate": "20260324"}, seed=2)
    html = client.get("/api/comparison_report.html?site=Goma").text
    assert "<th>uploaded</th>" in html
    assert "<th>acquired</th>" in html
