"""The analysis time budget.

Asked for directly after the first field test: "If the analysis is not done
within certain time, it should rather provide failed result than get stuck."

Worth being honest about what this does and does not address. The "stuck at
Computing…" screen was never a slow analysis — the server had finished in a
tenth of a second and the results page threw while drawing (see
test_unusable_exposures.py and test_frontend_integrity.py). This is a backstop
against a genuinely pathological scan: every image measured so far finishes
inside ten seconds, so an analysis approaching two minutes means something is
wrong that nobody has seen yet, and the operator should be told rather than
left holding an open request.

The budget is cooperative — checked between the five tests, not inside them.
Interrupting a numpy call part-way needs a separate process, which would change
how this is deployed. No single test has taken longer than about four seconds
across 38 scans, so the budget is spent between checks rather than inside one.
"""

import json
import time

import pytest

from phantom_qa import pipeline
from phantom_qa.pipeline import Deadline
from test_unusable_exposures import SYNTHETIC, _as_scan, _png16


@pytest.fixture(scope="module")
def ctx_for_a_scan(pdef):
    scan = _as_scan(SYNTHETIC["saturated"]())
    reg = pipeline.run_stage_a(scan, pdef)
    return pipeline.build_ctx(scan, pdef, reg,
                              {"scan_meta": {}, "sid_mm": 1000.0})


# ------------------------------------------------------------- the deadline

def test_no_budget_means_no_deadline():
    """The command line and the tests run unbounded, as they always did."""
    for value in (0, None, 0.0, ""):
        assert Deadline(value).expired() is False
    assert Deadline(0).seconds == 0.0


def test_a_budget_expires_once_it_is_spent():
    d = Deadline(0.05)
    assert not d.expired()
    time.sleep(0.06)
    assert d.expired()
    assert d.elapsed() >= 0.06


def test_the_note_says_what_was_lost_and_what_to_do():
    d = Deadline(120)
    note = d.note("the wedge test")
    assert "120 s" in note and "the wedge test" in note
    assert "stored" in note, "an operator must be told the scan is not lost"
    assert "PHANTOMQA_ANALYSIS_TIMEOUT_S" in note, \
        "whoever has to raise the limit needs to know its name"


# -------------------------------------------------- what an expired budget does

def test_an_expired_budget_leaves_a_failure_not_a_hang(ctx_for_a_scan):
    spent = Deadline(0.0001)
    time.sleep(0.01)
    geometry = pipeline.propose_all(ctx_for_a_scan, deadline=spent)
    results = pipeline.compute_all(ctx_for_a_scan, geometry, deadline=spent)

    assert pipeline.overall_status(results) in ("fail", "error"), \
        "the whole point is that a timed-out analysis reads as a failure"
    for test in pipeline.TESTS:
        assert results[test]["timed_out"] is True
        assert "time limit" in results[test]["error"]


def test_a_timed_out_result_can_still_be_stored_and_sent(ctx_for_a_scan):
    spent = Deadline(0.0001)
    time.sleep(0.01)
    results = pipeline.compute_all(
        ctx_for_a_scan, pipeline.propose_all(ctx_for_a_scan, deadline=spent),
        deadline=spent)
    json.dumps(results, allow_nan=False)


def test_a_generous_budget_changes_nothing(ctx_for_a_scan):
    """The budget must be invisible on every scan that is not pathological."""
    plain = pipeline.compute_all(ctx_for_a_scan,
                                 pipeline.propose_all(ctx_for_a_scan))
    budgeted = pipeline.compute_all(
        ctx_for_a_scan, pipeline.propose_all(ctx_for_a_scan,
                                             deadline=Deadline(600)),
        deadline=Deadline(600))
    assert pipeline.overall_status(plain) == pipeline.overall_status(budgeted)
    for test in pipeline.TESTS:
        assert not (budgeted[test] or {}).get("timed_out")
        assert (plain[test] or {}).get("status") == (budgeted[test] or {}).get("status")


def test_a_test_that_timed_out_is_not_reported_as_a_measurement(ctx_for_a_scan):
    """"error", not "fail".

    The detector did not fail the wedge test; the software never ran it. Both
    rank the same in the overall verdict — so the analysis still comes out a
    failure, which is what was asked for — but nobody reads the row as a
    measurement of the detector."""
    spent = Deadline(0.0001)
    time.sleep(0.01)
    results = pipeline.compute_all(
        ctx_for_a_scan, pipeline.propose_all(ctx_for_a_scan), deadline=spent)
    for test in pipeline.TESTS:
        assert results[test]["status"] == "error"
        assert not results[test].get("rows"), \
            "a test that never ran must not present rows of numbers"


# ------------------------------------------------------------ through the app

def test_the_budget_is_configurable_and_reaches_the_browser(tmp_path,
                                                            monkeypatch):
    """The page has to wait a little longer than the server, so it must be told.

    Guessing would mean either cutting off an analysis the server was about to
    bound itself, or waiting forever when it never answers."""
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login

    mod = _build_app(tmp_path, monkeypatch, PHANTOMQA_ANALYSIS_TIMEOUT_S="45")
    client = TestClient(mod.app)
    client.headers.update({"X-CSRF-Token": _login(client)})
    assert mod.cfg.analysis_timeout_s == 45
    assert client.get("/api/auth").json()["analysis_timeout_s"] == 45


def test_an_analysis_inside_the_budget_is_untouched(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login

    mod = _build_app(tmp_path, monkeypatch, PHANTOMQA_ANALYSIS_TIMEOUT_S="600")
    client = TestClient(mod.app)
    client.headers.update({"X-CSRF-Token": _login(client)})
    up = client.post("/api/analyses",
                     files={"file": ("s.png", _png16(SYNTHETIC["saturated"]()))},
                     data={"site": "T", "phantom": "TIMEOUT-OK"})
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    computed = client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    assert computed.status_code == 200
    assert not [t for t in pipeline.TESTS
                if (computed.json()["results"].get(t) or {}).get("timed_out")]


def test_a_timed_out_analysis_is_recorded_and_reportable(tmp_path, monkeypatch):
    """The end an operator actually meets: a stored failure they can open.

    The budget starts when the request does, so it cannot be spent in advance —
    an analysis has to genuinely overrun. The first test is made slow, which is
    what a pathological scan would do, and everything after it then falls past
    the deadline."""
    from fastapi.testclient import TestClient
    from phantom_qa.analysis import geometry as geometry_module
    from test_authorization import _build_app, _login

    mod = _build_app(tmp_path, monkeypatch, PHANTOMQA_ANALYSIS_TIMEOUT_S="1")
    client = TestClient(mod.app)
    client.headers.update({"X-CSRF-Token": _login(client)})
    up = client.post("/api/analyses",
                     files={"file": ("s.png", _png16(SYNTHETIC["saturated"]()))},
                     data={"site": "T", "phantom": "TIMEOUT-HIT"})
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.post(f"/api/analyses/{aid}/confirm",
                json={"stage": "C", "save_profile": False})

    original = geometry_module.compute

    def slow(*args, **kwargs):
        time.sleep(1.2)                     # longer than the whole budget
        return original(*args, **kwargs)

    monkeypatch.setattr(geometry_module, "compute", slow)
    computed = client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    assert computed.status_code == 200, computed.text
    assert computed.json()["overall"] in ("fail", "error")

    record = client.get(f"/api/analyses/{aid}").json()
    assert record["status"] in ("fail", "error")
    assert [t for t in pipeline.TESTS
            if (record["results"].get(t) or {}).get("timed_out")], \
        "the record must say the analysis ran out of time"
    # and everything downstream still works on it
    assert client.get(f"/api/analyses/{aid}/report.html").status_code == 200
    assert client.get(f"/api/analyses/{aid}/export.csv").status_code == 200


def test_the_uploads_heavy_work_leaves_the_event_loop_free(tmp_path,
                                                           monkeypatch):
    """Upload is the one async endpoint, so its CPU work must be offloaded.

    Decoding a DICOM and registering it inline blocked the worker's event loop
    for seconds, stalling every other operator that worker was serving."""
    import inspect
    from phantom_qa.webapp import main
    source = inspect.getsource(main.upload)
    assert "run_in_threadpool(\n            ingest.load_any_bytes" in source \
        or "run_in_threadpool(ingest.load_any_bytes" in source, \
        "the DICOM decode still runs on the event loop"
    assert "run_in_threadpool(_do_register" in source, \
        "registration still runs on the event loop"
