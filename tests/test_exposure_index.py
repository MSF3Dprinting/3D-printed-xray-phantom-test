"""How much radiation reached the detector, and when — shown, not judged.

In the first field test an under-exposed scan caused trouble: its numbers
looked wrong, and nothing on the screen said why. The detector had said why all
along. Every detector in use writes the standard exposure index into the file,
and the Carestream and the field Fuji also write the index they were set up to
expect, the deviation of one from the other, and a sensitivity (S) value. None
of it was ever shown.

So the values are now kept with the record, shown next to the acquisition date
on the open analysis, in the printed report, in History and in the exports, and
"not recorded" wherever a detector does not write one — the Philips writes no
target or deviation index. Records stored before this get their values from
their own stored file, once, without anything else about them changing.

Nothing is judged from these values yet. They are there so that an operator
looking at a scan whose numbers are off can rule the exposure in or out at a
glance.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import sqlite3

import numpy as np
import pytest

pydicom = pytest.importorskip("pydicom")

from conftest import SAMPLES, needs_samples  # noqa: E402
from phantom_qa import ingest  # noqa: E402
from phantom_qa.report import build_report, exposure_text  # noqa: E402
from phantom_qa.store import (Store, csv_export, exposure_of,  # noqa: E402
                              wide_csv_export)
from test_store_labels import fake_scan, results_for  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "phantom_qa", "webapp", "static")

#: What the field Fuji wrote on one of its exposures.
FUJI = {"ExposureIndex": "691", "TargetExposureIndex": "876",
        "DeviationIndex": "-1.0", "Sensitivity": "429"}
#: What the Philips writes: the exposure index and nothing else.
PHILIPS = {"ExposureIndex": "251"}

_variant = iter(range(1, 60_000))


def _dicom(tags=None, *, rows=16, cols=16):
    """A minimal DICOM carrying these exposure tags, as the text a detector
    writes. Every call differs in one pixel, so no two are duplicates."""
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    pixels = np.full((rows, cols), 400, dtype=np.uint16)
    pixels[0, 0] = next(_variant) % 4000
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.1.1"
    uid = generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = uid
    ds.SOPInstanceUID = uid
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.1.1"
    ds.Modality = "DX"
    ds.StudyDate = ds.AcquisitionDate = "20260920"
    ds.SeriesTime = ds.AcquisitionTime = "090417"
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.SamplesPerPixel = 1
    ds.Rows, ds.Columns = rows, cols
    ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 12, 11
    ds.PixelRepresentation = 0
    for keyword, value in (tags or {}).items():
        setattr(ds, keyword, value)
    ds.PixelData = pixels.tobytes()
    buf = io.BytesIO()
    pydicom.dcmwrite(buf, ds, enforce_file_format=True)
    return buf.getvalue()


def _meta(tags):
    return ingest.load_dicom_bytes(_dicom(tags), "t.dcm").meta


# ------------------------------------------------------------- extraction

def test_every_exposure_value_the_detector_writes_is_kept_as_a_number():
    """Numbers, not header text, so they can be listed and exported as they
    are; and the index written as "691" stays 691, not 691.0."""
    meta = _meta(FUJI)
    assert meta["ExposureIndex"] == 691 and isinstance(meta["ExposureIndex"], int)
    assert meta["TargetExposureIndex"] == 876
    assert meta["DeviationIndex"] == -1.0
    assert meta["Sensitivity"] == 429
    json.dumps(meta)                      # and they survive being stored


def test_a_value_the_detector_did_not_write_stays_absent():
    """The Philips writes no target or deviation index. Filling one in — with
    zero, or with a value worked out from the others — would put a number on
    the screen that no detector ever produced."""
    meta = _meta(PHILIPS)
    assert meta["ExposureIndex"] == 251
    for absent in ("TargetExposureIndex", "DeviationIndex", "Sensitivity"):
        assert absent not in meta, f"{absent} was invented"


def test_a_blank_or_unreadable_value_is_absent_rather_than_zero():
    assert "DeviationIndex" not in _meta({**PHILIPS, "DeviationIndex": ""})
    for text in ("", "   ", "abc", "nan", "inf", None):
        assert ingest._dicom_number(text) is None, repr(text)
    assert ingest._dicom_number("243.00") == 243.0
    assert ingest._dicom_number(" -0.3 ") == -0.3


def test_the_acquisition_date_is_kept_as_well():
    """It is in the list of dates the acquisition time is read from, but was
    never taken out of the header."""
    assert _meta(PHILIPS)["AcquisitionDate"] == "20260920"


def test_the_header_alone_gives_the_same_values(tmp_path):
    """The backfill reads only the header, which is what makes it affordable
    for every stored record at start-up. Proved by cutting the pixel data off:
    the values still come back."""
    data = _dicom(FUJI, rows=64, cols=64)
    path = tmp_path / "cut.bin"
    path.write_bytes(data[:-4000])
    header = ingest.read_exposure_header(str(path))
    full = ingest.load_dicom_bytes(data, "t.dcm").meta
    assert header == {k: full[k] for k in ingest.EXPOSURE_TAGS}


# ----------------------------------------------------- records stored before

#: The `analyses` table as the release before this one left it: everything up
#: to the finalised stamp, no exposure columns.
_PREVIOUS_RELEASE = """
CREATE TABLE analyses (
  id TEXT PRIMARY KEY, created_at TEXT, source_name TEXT, sha256 TEXT,
  kind TEXT, reduced_precision INTEGER, signature TEXT, meta_json TEXT,
  stage TEXT, reg_json TEXT, geometry_json TEXT, results_json TEXT,
  audit_json TEXT, sid_mm REAL, is_baseline INTEGER DEFAULT 0,
  algo_version TEXT, pdef_version TEXT, status TEXT,
  site TEXT DEFAULT '', phantom TEXT DEFAULT '', operator TEXT DEFAULT '',
  notes TEXT DEFAULT '', acquired_at TEXT DEFAULT '',
  validation_status TEXT DEFAULT '', validated_by TEXT DEFAULT '',
  validation_comment TEXT DEFAULT '', validated_at TEXT DEFAULT '',
  geometry_seq INTEGER DEFAULT 0, layout_source TEXT DEFAULT '',
  quality_json TEXT DEFAULT '', quality_verdict TEXT DEFAULT '',
  finalized_at TEXT DEFAULT '', finalized_by TEXT DEFAULT ''
);
"""

_OLD_META = {"Manufacturer": "Test", "StudyDate": "20260920",
             "SeriesTime": "090417", "KVP": 70.0, "Rows": 16, "Columns": 16}


def _old_row(aid, **extra):
    """A record as the previous release stored it: finished, signed off,
    finalised and the phantom's reference — every protection there is."""
    row = {"id": aid, "created_at": "2026-09-20 09:30:00",
           "source_name": f"{aid}.dcm", "sha256": aid[0] * 64, "kind": "dicom",
           "reduced_precision": 0, "signature": "sig",
           "meta_json": json.dumps(_OLD_META), "stage": "F",
           "results_json": json.dumps(results_for()), "status": "pass",
           "geometry_json": json.dumps({"uniformity": {"squares": []}}),
           "audit_json": json.dumps([{"ts": "2026-09-20 09:40:00",
                                      "stage": "F", "action": "finalized"}]),
           "site": "Goma", "phantom": "MSF-01", "acquired_at":
           "2026-09-20 09:04:17", "validation_status": "validated",
           "validated_by": "Dr A", "validated_at": "2026-09-21 08:00:00",
           "is_baseline": 1, "finalized_at": "2026-09-20 09:40:00",
           "finalized_by": "qa", "quality_verdict": "good",
           "quality_json": json.dumps({"verdict": "good"})}
    row.update(extra)
    return row


def _legacy(root, rows, files):
    """A database from the previous release, with its stored uploads."""
    uploads = os.path.join(root, "data", "uploads")
    os.makedirs(uploads, exist_ok=True)
    con = sqlite3.connect(os.path.join(root, "data", "phantom_qa.sqlite3"))
    con.executescript(_PREVIOUS_RELEASE)
    for r in rows:
        con.execute(f"INSERT INTO analyses ({', '.join(r)}) VALUES "
                    f"({', '.join('?' for _ in r)})", list(r.values()))
    con.commit()
    con.close()
    for aid, data in files.items():
        with open(os.path.join(uploads, f"{aid}.bin"), "wb") as f:
            f.write(data)


def _raw_rows(root):
    con = sqlite3.connect(os.path.join(root, "data", "phantom_qa.sqlite3"))
    con.row_factory = sqlite3.Row
    try:
        return {r["id"]: dict(r) for r in con.execute("SELECT * FROM analyses")}
    finally:
        con.close()


class _Capture(logging.Handler):
    """The application's loggers do not propagate once logging is set up, so
    caplog cannot be relied on to see them."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture()
def store_log():
    handler = _Capture()
    logger = logging.getLogger("phantomqa.store")
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old_level)


def test_an_old_record_gains_its_exposure_values_from_its_own_file(tmp_path):
    root = str(tmp_path)
    _legacy(root, [_old_row("fuji01"), _old_row("phil01")],
            {"fuji01": _dicom(FUJI), "phil01": _dicom(PHILIPS)})

    store = Store(root)                   # the upgrade happens here

    fuji = store.get("fuji01")
    assert {k: fuji["meta"][k] for k in ingest.EXPOSURE_TAGS} == {
        "ExposureIndex": 691, "TargetExposureIndex": 876,
        "DeviationIndex": -1.0, "Sensitivity": 429}
    assert exposure_of(fuji) == {"exposure_index": 691,
                                 "target_exposure_index": 876,
                                 "deviation_index": -1.0, "sensitivity": 429}
    phil = store.get("phil01")
    assert phil["meta"]["ExposureIndex"] == 251
    assert "DeviationIndex" not in phil["meta"]
    assert phil["deviation_index"] is None and phil["exposure_index"] == 251


def test_the_backfill_changes_nothing_but_the_header_and_the_exposure_columns(
        tmp_path):
    """The record is finalised, signed off and a reference, so it is locked
    against every edit. This is not an edit to it: it transcribes more of the
    header of the file the record was made from. Results, status, geometry,
    validation and every protection must come through byte for byte, and the
    header keys it already had must be untouched."""
    root = str(tmp_path)
    _legacy(root, [_old_row("fuji01")], {"fuji01": _dicom(FUJI)})
    before = _raw_rows(root)["fuji01"]

    store = Store(root)

    after = _raw_rows(root)["fuji01"]
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"meta_json"}, f"the backfill also changed {changed}"
    old, new = json.loads(before["meta_json"]), json.loads(after["meta_json"])
    assert {k: new[k] for k in old} == old, "an existing header key changed"
    assert set(new) - set(old) == set(ingest.EXPOSURE_TAGS)
    assert store.protection(store.get("fuji01")) == [
        "finalized", "signed_off", "baseline"]


def test_running_it_again_changes_nothing(tmp_path):
    root = str(tmp_path)
    _legacy(root, [_old_row("fuji01"), _old_row("phil01")],
            {"fuji01": _dicom(FUJI), "phil01": _dicom(PHILIPS)})
    store = Store(root)
    first = _raw_rows(root)

    with store._conn() as c:
        again = store._backfill_exposure(c)
    Store(root)                           # and the upgrade does not re-run

    assert again["updated"] == 0
    assert _raw_rows(root) == first


def test_a_value_already_recorded_is_never_replaced(tmp_path):
    """What the record already holds was read when it was analysed; a file
    that says otherwise now is the file that changed, not the record."""
    root = str(tmp_path)
    meta = {**_OLD_META, "ExposureIndex": 999}
    _legacy(root, [_old_row("fuji01", meta_json=json.dumps(meta))],
            {"fuji01": _dicom(FUJI)})
    store = Store(root)
    rec = store.get("fuji01")
    assert rec["meta"]["ExposureIndex"] == 999
    assert "DeviationIndex" not in rec["meta"]
    assert rec["exposure_index"] == 999


def test_a_missing_or_unreadable_file_is_skipped_and_said(tmp_path, store_log):
    """The database must open whatever state the uploads folder is in; the
    records affected simply show "not recorded", as they did before."""
    root = str(tmp_path)
    _legacy(root, [_old_row("gone01"), _old_row("junk01"),
                   _old_row("fuji01"),
                   _old_row("png001", kind="image", reduced_precision=1)],
            {"junk01": b"not a dicom at all" * 50, "fuji01": _dicom(FUJI),
             "png001": b"\x89PNG\r\n\x1a\n" + b"\0" * 64})
    before = _raw_rows(root)

    store = Store(root)

    after = _raw_rows(root)
    for aid in ("gone01", "junk01", "png001"):
        assert after[aid] == {**before[aid], "exposure_index": None,
                              "target_exposure_index": None,
                              "deviation_index": None, "sensitivity": None}
    assert store.get("fuji01")["exposure_index"] == 691, \
        "one bad file stopped the others from being read"
    said = "\n".join(store_log.lines)
    assert "gone01" in said and "junk01" in said
    assert "png001" not in said, "a plain image has no header to read"


@needs_samples
def test_a_stored_philips_scan_is_filled_in_from_its_real_header(tmp_path):
    root = str(tmp_path)
    _legacy(root, [_old_row("phil01")], {})
    shutil.copyfile(SAMPLES[0],
                    os.path.join(root, "data", "uploads", "phil01.bin"))
    rec = Store(root).get("phil01")
    assert rec["meta"]["ExposureIndex"] == 251
    assert rec["exposure_index"] == 251
    assert rec["deviation_index"] is None


# -------------------------------------------------------- what is shown

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _upload(client, tags, phantom="EXPOSURE"):
    up = client.post("/api/analyses",
                     files={"file": ("scan.dcm", _dicom(tags))},
                     data={"site": "T", "phantom": phantom})
    assert up.status_code == 200, up.text
    return up.json()["analyses"][0]["id"]


def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def _function(app_js, name):
    start = app_js.index(f"function {name}(")
    rest = app_js[start:]
    end = rest.find("\nfunction ", 10)
    return rest if end < 0 else rest[:end]


def test_the_open_analysis_carries_what_the_identity_bar_shows(client):
    """The bar reads the stored header the page already receives, so showing
    the values costs no extra bytes on the wire."""
    aid = _upload(client, FUJI)
    rec = client.get(f"/api/analyses/{aid}").json()
    assert rec["meta"]["ExposureIndex"] == 691
    assert rec["meta"]["TargetExposureIndex"] == 876
    assert rec["meta"]["DeviationIndex"] == -1.0
    assert rec["acquired_at"].startswith("2026-09-20 09:04")

    app_js = _read("app.js")
    assert "${exposureRow(r)}" in _function(app_js, "renderIdentityBar")
    row = _function(app_js, "exposureRow")
    for needle in ("m.ExposureIndex", "m.TargetExposureIndex",
                   "m.DeviationIndex, true", "Acquired", "not recorded"):
        assert needle in row, f"the identity bar lost {needle!r}"


def test_the_page_says_not_recorded_and_shows_the_sign():
    """The page cannot be run here, so its formatter is checked for the three
    things that matter: nothing is shown as a number unless it is one, the
    deviation index carries its sign with a real minus, and halves round the
    same way as in the printed report."""
    fn = _function(_read("app.js"), "exposureText")
    assert '"not recorded"' in fn and "isFinite(v)" in fn
    assert '"+"' in fn and '"−"' in fn
    assert "Math.floor(Math.abs(v)" in fn and "+ 0.5)" in fn
    assert "toFixed" not in fn, "toFixed rounds halves differently from the report"


@pytest.mark.parametrize("value,signed,shown", [
    (691, False, "691"), (141.46, False, "141"), (142.5, False, "143"),
    (-1.0, True, "−1.0"), (-4.75, True, "−4.8"),
    (0.31, True, "+0.3"), (-0.04, True, "0.0"), (0, True, "0.0"),
    (None, True, "not recorded"), (None, False, "not recorded"),
    (float("nan"), True, "not recorded"), (True, False, "not recorded"),
])
def test_how_a_value_is_written(value, signed, shown):
    """The sign is the point of a deviation index — minus is under-exposed —
    so it is always shown; zero is neither plus nor minus."""
    assert exposure_text(value, signed=signed) == shown


def test_history_shows_two_numbers_per_row_and_no_more(client):
    """The listing is fetched whole over slow links, so it carries the two
    values the row shows and nothing else of the exposure record."""
    fuji = _upload(client, FUJI)
    phil = _upload(client, PHILIPS)
    rows = {r["id"]: r for r in client.get("/api/analyses").json()["analyses"]}
    assert (rows[fuji]["exposure_index"], rows[fuji]["deviation_index"]) \
        == (691, -1.0)
    assert (rows[phil]["exposure_index"], rows[phil]["deviation_index"]) \
        == (251, None)
    for extra in ("target_exposure_index", "sensitivity", "meta"):
        assert extra not in rows[fuji], f"the listing now carries {extra}"
    added = len(json.dumps({k: rows[fuji][k] for k in
                            ("exposure_index", "deviation_index")}))
    assert added <= 60, f"{added} bytes more per row"


def test_history_has_a_column_for_them_that_says_not_recorded():
    app_js, index_html = _read("app.js"), _read("index.html")
    assert "${exposureCell(a)}" in app_js
    assert ">exposure</th>" in index_html
    cell = _function(app_js, "exposureCell")
    assert "a.exposure_index" in cell and "a.deviation_index" in cell
    assert "not recorded" in cell and "EI " in cell and "DI " in cell


def test_the_report_shows_the_values_next_to_the_date(client):
    aid = _upload(client, FUJI)
    html = client.get(f"/api/analyses/{aid}/report.html").text
    ident = html[html.index("<h2>Identification</h2>"):]
    ident = ident[:ident.index("</section>")]
    for label, shown in (("Exposure index", "691"),
                         ("Target exposure index", "876"),
                         ("Deviation index", "−1.0"),
                         ("Sensitivity (S value)", "429")):
        assert re.search(rf"{re.escape(label)}</span><span class=\"idv\">"
                         rf"{re.escape(shown)}<", ident), (label, shown)
    assert "Acquired" in ident


def test_the_report_says_not_recorded_for_what_the_philips_does_not_write(
        client):
    aid = _upload(client, PHILIPS)
    html = client.get(f"/api/analyses/{aid}/report.html").text
    ident = html[html.index("<h2>Identification</h2>"):]
    assert re.search(r'Deviation index</span><span class="idv">not recorded<',
                     ident)
    assert re.search(r'Exposure index</span><span class="idv">251<', ident)


def test_a_plain_image_reports_nothing_recorded():
    store_rec = {"id": "x", "created_at": "2026-09-20 09:00:00",
                 "source_name": "x.png", "meta": {"Rows": 4, "Columns": 4},
                 "results": {}}
    html = build_report(store_rec)
    assert html.count('<span class="idv">not recorded</span>') == 4
    assert "deviation index of 0" not in html, \
        "the explanation is only for a record that has something to explain"


# ------------------------------------------------------------------ exports

_LONG_HEADER_BEFORE = [
    "analysis_id", "site", "phantom", "operator", "acquired_at",
    "acquired_flag", "created_at", "source", "signature", "is_baseline",
    "validation", "validated_by", "validated_at", "test", "object", "metric",
    "value", "unit", "status"]
_EXPOSURE_COLUMNS = ["exposure_index", "target_exposure_index",
                     "deviation_index", "sensitivity"]


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path))
    fuji = s.new_analysis(fake_scan(meta={**_OLD_META, "ExposureIndex": 691,
                                          "TargetExposureIndex": 876,
                                          "DeviationIndex": -1.0,
                                          "Sensitivity": 429},
                                    sha="f" * 64), b"x", "sig", "1", "1")
    phil = s.new_analysis(fake_scan(meta={**_OLD_META, "ExposureIndex": 251},
                                    sha="p" * 64), b"y", "sig", "1", "1")
    for aid in (fuji, phil):
        s.update(aid, results=results_for(), status="pass")
    s.ids = {"fuji": fuji, "phil": phil}
    return s


def test_the_long_csv_keeps_its_columns_and_adds_the_exposure_ones_last(store):
    """A spreadsheet or script built on the earlier layout must still find
    every column where it was."""
    import csv
    recs = [store.get(i) for i in store.ids.values()]
    lines = list(csv.reader(io.StringIO(csv_export(recs))))
    assert lines[0] == _LONG_HEADER_BEFORE + _EXPOSURE_COLUMNS
    by_id = {}
    for line in lines[1:]:
        by_id.setdefault(line[0], line[-4:])
    assert by_id[store.ids["fuji"]] == ["691.0", "876.0", "-1.0", "429.0"]
    assert by_id[store.ids["phil"]] == ["251.0", "", "", ""], \
        "an empty cell, as for every other missing value in the export"


def test_the_wide_csv_adds_its_exposure_rows_after_everything_else(store):
    """Rows rather than columns, as for every other label in this layout, and
    after the last metric so that no existing row moves."""
    recs = store.get_slim(list(store.ids.values()))   # what the export reads
    lines = wide_csv_export(recs).splitlines()
    assert [ln.split(",")[2] for ln in lines[-4:]] == \
        [f"# {c}" for c in _EXPOSURE_COLUMNS]
    assert not lines[-5].split(",")[2].startswith("#"), \
        "the exposure rows must follow the metric rows, not the labels"
    assert len(lines[0].split(",")) == 4 + 2, "no fixed column was added"
    assert sorted(lines[-4].split(",")[4:]) == ["251.0", "691.0"]
    assert lines[-2].split(",")[4:].count("") == 1, \
        "the Philips has no deviation index and its cell stays empty"


def test_the_exports_over_http_carry_the_values(client):
    store = client.mod.store
    aid = _upload(client, FUJI)
    store.update(aid, results=results_for(), status="pass")
    one = client.get(f"/api/analyses/{aid}/export.csv").text.splitlines()
    assert one[0].endswith(",".join(_EXPOSURE_COLUMNS))
    assert one[1].endswith("691.0,876.0,-1.0,429.0")
    many = client.get(f"/api/export.csv?ids={aid}").text.splitlines()
    assert many[1].endswith("691.0,876.0,-1.0,429.0")


# ------------------------------------------------------------ not judged yet

def test_nothing_is_judged_from_the_exposure_values_yet():
    """Shown, not used. No measurement, verdict or quality gate reads them,
    so they cannot move a number in the reference benchmark. When a threshold
    is wanted it must be a deliberate change to this test."""
    readers = []
    for folder in ("analysis",):
        base = os.path.join(ROOT, "phantom_qa", folder)
        for name in os.listdir(base):
            if name.endswith(".py"):
                readers.append(os.path.join(base, name))
    readers += [os.path.join(ROOT, "phantom_qa", n)
                for n in ("quality.py", "pipeline.py", "registration.py")]
    for path in readers:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for word in ("ExposureIndex", "DeviationIndex", "exposure_index",
                     "deviation_index"):
            assert word not in text, f"{os.path.basename(path)} reads {word}"


# ------------------------------------------------------ the real samples

@needs_samples
def test_the_original_philips_scans_read_as_they_were_written():
    """The two reference DICOMs from the first HQ drop: the Philips writes an
    exposure index and nothing else."""
    first = ingest.load_path(SAMPLES[0])[0].meta
    second = ingest.load_path(SAMPLES[1])[0].meta
    assert first["ExposureIndex"] == 251
    assert second["ExposureIndex"] == 255
    for meta in (first, second):
        for absent in ("TargetExposureIndex", "DeviationIndex", "Sensitivity"):
            assert absent not in meta
    assert ingest.read_exposure_header(SAMPLES[0]) == {"ExposureIndex": 251}
