""""n/a" stops hiding "pass".

X-ray field alignment can only be checked when the edge of the radiation field
falls inside the image, and on every exposure so far it did not. That test came
back "n/a", "n/a" ranked above "pass" in the overall verdict, and so all six
original reference scans read "n/a" although every test on them passed.

Ranking "n/a" below "pass" would have been the one-line fix, and the wrong one:
the same word also meant *could not be measured* — no corners found, discs with
no noise, a flat wedge, measuring areas that could not be placed. Ranked below
"pass", a broken exposure would have read "pass".

So the word is split in two:

  "not applicable"  only field alignment with no field edge in the image. It
                    does not lower the verdict; the verdict names it instead
                    ("pass — X-ray field alignment not checked …").
  "not measured"    everything that should have been measured and was not. It
                    counts as a warning, so it can never be read as a pass.

"n/a" survives only in analyses stored before the split. Those are not
recomputed (the operators' decision: nothing already finalised or signed changes
its wording), so they must go on reading exactly as they did.
"""

import functools
import itertools
import json
import os
import re

import pytest

from phantom_qa import pipeline
from phantom_qa.report import build_report
from test_store_labels import results_for
from test_unusable_exposures import SYNTHETIC, _as_scan, _png16, analyse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(REPO, "phantom_qa", "webapp", "static")

FIELD_NOTE = "X-ray field alignment not checked (no field edge found in the image)"

#: Every per-test status the application can hold: the four verdicts, the two
#: new words, and the legacy "n/a" that stored analyses still carry.
ALL_STATUSES = ("pass", "warn", "fail", "error",
                "not applicable", "not measured", "n/a")

#: What the overall verdict may be. It must not grow: History, the exports and
#: every stored analysis already use exactly these.
OVERALL = {"pass", "warn", "fail", "error", "n/a"}

_variant = iter(range(40_000, 49_000))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _record(results, status):
    return {"id": "abc123def456", "created_at": "2026-09-21 10:00:00",
            "acquired_at": "2026-09-20 09:37:26", "source_name": "scan.dcm",
            "sha256": "a" * 64, "signature": "sig", "algo_version": "1.0.0",
            "sid_mm": 1000.0, "site": "Goma", "phantom": "MSF-01",
            "results": results, "meta": {}, "reg": None, "geometry": None,
            "status": status, "is_baseline": 0, "validation_status": ""}


def _legacy(results):
    """The same results as an analysis stored before the split carries them."""
    old = json.loads(json.dumps(results))
    old["geometry"]["field_status"] = "n/a"
    return old


def _stored(client, results, status, phantom="VERDICT"):
    """An analysis with results, written straight into the store."""
    from test_store_labels import fake_scan
    store = client.mod.store
    aid = store.new_analysis(fake_scan(sha=f"{next(_variant):064d}"),
                             b"payload", "sig", "1.0.0", "1.0",
                             labels={"site": "T", "phantom": phantom})
    store.update(aid, results=results, status=status, stage="F")
    return aid


def _listed(client, aid):
    rows = client.get("/api/analyses").json()["analyses"]
    return next(r for r in rows if r["id"] == aid)


def _pdef():
    from phantom_qa.phantom_def import load_default
    return load_default()


@functools.lru_cache(maxsize=None)
def _synthetic_results(kind):
    """Each synthetic exposure measured once for the whole module; the runs
    take seconds and several tests read the same results."""
    return analyse(_as_scan(SYNTHETIC[kind]()), _pdef())[2]


def _as_ctx():
    """A context with no phantom in it, for compute_all's own bookkeeping."""
    import numpy as np
    from phantom_qa.analysis.common import Ctx
    from phantom_qa.registration import Transform
    rng = np.random.default_rng(3)
    return Ctx(pixels=2000 + rng.normal(0, 20, (64, 64)),
               T=Transform(A=np.array([[1.0, 0.0], [0.0, -1.0]]),
                           t=np.array([32.0, 32.0])),
               pdef=_pdef())


# ------------------------------------------------- the verdict on a good scan

def test_a_scan_where_everything_passes_reads_pass():
    """The reference scans' case: every test passes, no field edge in view."""
    results = results_for()
    assert results["geometry"]["field_status"] == "not applicable"
    assert pipeline.overall_status(results) == "pass"


def test_the_pass_says_what_it_did_not_check():
    """A bare "pass" would claim field alignment was fine. It was not looked
    at, and a reader of a signed report has to be told so."""
    assert pipeline.verdict_notes(results_for()) == [FIELD_NOTE]


def test_the_printed_report_carries_the_verdict_and_its_note():
    html = build_report(_record(results_for(), "pass"))
    overall = re.search(r"<b>Overall</b></span>(.*?)</div>", html, re.S)
    assert overall, "the report does not state the overall verdict"
    assert ">pass</span>" in overall.group(1)
    assert FIELD_NOTE in overall.group(1)
    # and the field-alignment cell itself says why, in the neutral colour
    assert ">not applicable</span>" in html


def test_the_geometry_module_calls_a_missing_field_edge_not_applicable():
    """Measured on real pixels, not asserted on a hand-built result: none of
    the synthetic exposures shows a field edge, and none of them may call that
    "n/a" or "not measured"."""
    for kind in sorted(SYNTHETIC):
        g = _synthetic_results(kind)["geometry"]
        if not any(f.get("detected") for f in g["field_alignment"].values()):
            assert g["field_status"] == "not applicable", kind
            assert g["field_reasons"], f"{kind}: no reason given"


# -------------------------------------- an unmeasurable exposure never passes

def test_nothing_writes_the_old_word_any_more():
    """Every fresh result uses the two new words; "n/a" is legacy only."""
    for kind in sorted(SYNTHETIC):
        results = _synthetic_results(kind)
        for name in pipeline.TESTS:
            for key in ("status", "field_status", "dimension_status"):
                assert results[name].get(key) != "n/a", f"{kind} {name}.{key}"


def test_a_test_that_never_ran_is_not_measured():
    """Measuring areas that could not be placed apply to every exposure of the
    phantom; they were simply missing. That is "not measured"."""
    out = pipeline.compute_all(
        _as_ctx(), {"lowcontrast": {"_error": "ValueError: block not found"}})
    assert out["lowcontrast"]["status"] == "not measured"
    assert out["lowcontrast"]["error"] == "ValueError: block not found"
    # a test that was not proposed at all says so in words
    assert out["wedge"]["status"] == "not measured"
    assert "measuring areas" in out["wedge"]["error"]


@pytest.mark.parametrize("test", ["lowcontrast", "uniformity", "wedge",
                                  "linepairs"])
def test_one_unmeasured_test_is_enough_to_stop_a_pass(test):
    """The defect a naive re-ranking would have introduced."""
    results = results_for()
    results[test]["status"] = "not measured"
    assert pipeline.overall_status(results) == "warn"


def test_unmeasured_dimensions_stop_a_pass():
    results = results_for()
    results["geometry"]["dimension_status"] = "not measured"
    assert pipeline.overall_status(results) == "warn"


@pytest.mark.parametrize("kind", ["saturated", "flat"])
def test_an_unmeasurable_exposure_through_the_app_never_reads_pass(kind, client):
    """The whole chain, upload to verdict, on images that hold nothing.

    The synthetic exposures also fail their wedge, which alone would keep them
    off "pass". So the verdict is checked a second time as if every test that
    did produce an answer had passed: the tests that could not measure must
    hold it down on their own."""
    pixels = SYNTHETIC[kind]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses",
                     files={"file": (f"{kind}.png", _png16(pixels))},
                     data={"site": "T", "phantom": f"NM-{kind}"})
    assert up.status_code == 200, up.text
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.post(f"/api/analyses/{aid}/confirm",
                json={"stage": "C", "save_profile": False})
    computed = client.post(f"/api/analyses/{aid}/compute",
                           json={"sid_mm": 1000.0})
    assert computed.status_code == 200, computed.text
    body = computed.json()
    assert body["overall"] in OVERALL - {"pass", "n/a"}
    assert _listed(client, aid)["status"] == body["overall"]

    results = body["results"]
    unmeasured = [t for t in pipeline.TESTS
                  if results[t].get("status") == "not measured"]
    assert unmeasured, "a saturated exposure should leave something unmeasured"
    for t in unmeasured:
        assert results[t].get("reasons") or results[t].get("error"), \
            f"{t} was not measured and does not say why"

    kinder = json.loads(json.dumps(results))
    for t in pipeline.TESTS:
        for key in ("status", "field_status", "dimension_status"):
            if kinder[t].get(key) not in (None, "not measured",
                                          "not applicable"):
                kinder[t][key] = "pass"
    assert pipeline.overall_status(kinder) == "warn"


def test_a_test_that_could_not_measure_says_what_and_why():
    """A "not measured" chip with nothing under it leaves the operator unable
    to tell a bad exposure from a misplaced measuring area."""
    results = _synthetic_results("saturated")
    reasons = {"status": "reasons", "dimension_status": "dimension_reasons",
               "field_status": "field_reasons"}
    seen = 0
    for name in pipeline.TESTS:
        for key, rkey in reasons.items():
            if results[name].get(key) == "not measured":
                seen += 1
                why = results[name].get(rkey) or [results[name].get("error")]
                assert any(why), f"{name}.{key} is not measured without a reason"
    assert seen


# -------------------------------------------------------- the old records

def test_a_legacy_record_reproduces_its_old_verdict():
    """Stored analyses are not recomputed, so re-reading one must give back
    the verdict it was stored with — "n/a" keeps its old rank."""
    old = _legacy(results_for())
    assert pipeline.overall_status(old) == "n/a"
    old["wedge"]["status"] = "warn"
    assert pipeline.overall_status(old) == "warn"
    old["lowcontrast"]["status"] = "fail"
    assert pipeline.overall_status(old) == "fail"


def test_a_legacy_record_gets_no_new_wording():
    """No note is invented for a record that never said "not applicable"."""
    assert pipeline.verdict_notes(_legacy(results_for())) == []


def test_a_legacy_record_reads_as_it_always_did(client):
    old = _legacy(results_for())
    aid = _stored(client, old, "n/a", phantom="OLD")
    row = _listed(client, aid)
    assert row["status"] == "n/a"
    assert row["verdict_notes"] == []
    # opening it rewrites nothing
    assert client.get(f"/api/analyses/{aid}").json()["status"] == "n/a"
    assert client.mod.store.get(aid)["results"]["geometry"]["field_status"] \
        == "n/a"
    html = client.get(f"/api/analyses/{aid}/report.html").text
    assert ">n/a</span>" in html
    assert FIELD_NOTE not in html


# ------------------------------------------------ the vocabulary, exhaustively

def _combinations():
    slots = [("geometry", "dimension_status"), ("geometry", "field_status"),
             ("lowcontrast", "status"), ("wedge", "status")]
    for combo in itertools.product(ALL_STATUSES, repeat=len(slots)):
        results = results_for()
        for (test, key), s in zip(slots, combo):
            results[test][key] = s
        yield combo, results


def test_the_overall_verdict_never_uses_the_new_words():
    for combo, results in _combinations():
        assert pipeline.overall_status(results) in OVERALL, combo


def test_not_applicable_never_changes_the_verdict():
    for combo, results in _combinations():
        without = json.loads(json.dumps(results))
        for name in pipeline.TESTS:
            for key in ("status", "field_status", "dimension_status"):
                if without[name].get(key) == "not applicable":
                    del without[name][key]
        if all(s == "not applicable" for s in combo):
            continue           # nothing left to judge; see the next test
        assert (pipeline.overall_status(results)
                == pipeline.overall_status(without)), combo


def test_not_measured_is_never_better_than_a_warning():
    for combo, results in _combinations():
        if "not measured" in combo:
            assert pipeline.overall_status(results) in {"warn", "fail",
                                                        "error"}, combo


def test_nothing_judged_is_not_a_pass():
    """An analysis in which every test was not applicable — or which holds no
    test at all — has no verdict, and "pass" would be one."""
    nothing = {t: {"status": "not applicable"} for t in pipeline.TESTS}
    assert pipeline.overall_status(nothing) == "n/a"
    assert pipeline.overall_status({}) == "n/a"


def test_an_unknown_status_is_not_ranked_as_a_pass():
    results = results_for()
    results["uniformity"]["status"] = "something new"
    assert pipeline.overall_status(results) == "warn"


# ------------------------------------------------ every status can be shown

def _chip_class(status: str) -> str:
    """The class app.js gives a status chip: its letters only."""
    return re.sub(r"[^a-z]", "", status)


def test_every_status_has_a_chip_style_in_the_app():
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        js = f.read()
    assert '(s || "na").replace(/[^a-z]/g, "")' in js, \
        "the chip class is no longer the status's letters"
    with open(os.path.join(STATIC, "style.css"), encoding="utf-8") as f:
        css = f.read()
    for status in ALL_STATUSES:
        cls = _chip_class(status)
        assert re.search(rf"\.chip\.{cls}\b[^{{]*\{{", css), \
            f'"{status}" has no chip style (.chip.{cls})'


def test_the_two_words_are_told_apart_by_colour():
    with open(os.path.join(STATIC, "style.css"), encoding="utf-8") as f:
        css = f.read()
    assert re.search(r"\.chip\.notmeasured\s*\{\s*background:\s*var\(--warn\)",
                     css), '"not measured" should look like a warning'
    assert re.search(r"\.chip\.notapplicable\s*\{\s*background:\s*#667", css), \
        '"not applicable" should be the neutral grey'


def test_every_status_renders_in_both_reports():
    from phantom_qa import comparison_report, report
    for status in ALL_STATUSES:
        assert status in report._STATUS_COLOR, f"report: {status}"
        assert status in comparison_report._STATUS_COLOR, f"comparison: {status}"
        chip = report._chip(status)
        assert f">{status}</span>" in chip
    assert report._STATUS_COLOR["not measured"] == report._STATUS_COLOR["warn"]
    assert report._STATUS_COLOR["not applicable"] == report._STATUS_COLOR["n/a"]


@pytest.mark.parametrize("status", ["not measured", "not applicable"])
def test_a_report_with_the_new_words_prints(status):
    results = results_for()
    results["uniformity"]["status"] = status
    html = build_report(_record(results, pipeline.overall_status(results)))
    assert f">{status}</span>" in html


def test_the_comparison_report_accepts_the_new_words(client):
    ids = [_stored(client, results_for(), "pass", phantom="CMP-A")]
    unmeasured = results_for()
    unmeasured["lowcontrast"]["status"] = "not measured"
    ids.append(_stored(client, unmeasured, "warn", phantom="CMP-B"))
    answer = client.get("/api/comparison_report.html?ids=" + ",".join(ids))
    assert answer.status_code == 200, answer.text[:300]


# -------------------------------------------------- History and the results page

def test_history_carries_the_note_beside_the_verdict(client):
    aid = _stored(client, results_for(), "pass")
    row = _listed(client, aid)
    assert row["status"] == "pass"
    assert row["verdict_notes"] == [FIELD_NOTE]


def test_history_never_ships_the_results_to_say_so(client):
    """The note is built from a few status words picked out inside SQLite;
    the results (about 130 kB per analysis) stay on the server, and the note
    itself costs a few dozen bytes a row on a 512 kbit/s link."""
    aid = _stored(client, results_for(), "pass")
    row = _listed(client, aid)
    assert "results" not in row and "results_json" not in row
    assert len(json.dumps(row["verdict_notes"])) < 100


def test_history_survives_a_damaged_results_blob(client):
    """A blob that is not JSON must cost its note, never the whole listing."""
    aid = _stored(client, results_for(), "pass", phantom="DAMAGED")
    with client.mod.store._conn() as c:
        c.execute("UPDATE analyses SET results_json=? WHERE id=?",
                  ("{not json", aid))
    answer = client.get("/api/analyses")
    assert answer.status_code == 200, answer.text[:300]
    row = next(r for r in answer.json()["analyses"] if r["id"] == aid)
    assert row["verdict_notes"] == []


def test_history_and_the_results_page_show_the_note():
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        js = f.read()
    assert "r.verdict_notes" in js, "step E does not print the verdict's note"
    assert "a.verdict_notes" in js, "History does not print the verdict's note"
    # a test that does not apply is not something to attend to
    assert 'const needsLook = (s) => s !== "pass" && s !== "not applicable";' \
        in js


def test_the_step_e_answer_carries_the_note(client):
    """Checked on the real endpoint, so the page has something to print."""
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": "NOTE"})
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.post(f"/api/analyses/{aid}/confirm",
                json={"stage": "C", "save_profile": False})
    body = client.post(f"/api/analyses/{aid}/compute",
                       json={"sid_mm": 1000.0}).json()
    assert body["verdict_notes"] == pipeline.verdict_notes(body["results"])
    assert FIELD_NOTE in body["verdict_notes"]
