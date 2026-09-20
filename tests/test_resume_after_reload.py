"""Surviving a page reload without losing the analysis.

Reported from the field: "After reload of window we had to load the scan
again." That re-upload is expensive on a field link — 7.5 MB, minutes — and it
is also the *cause* of the "different scans identified as same" reports, since
the server then correctly refuses a byte-identical duplicate. The audit log
shows the whole chain: `compute=fail` recorded at 04:22:47, the client silent
for two and a half hours, the same file re-uploaded and refused at 06:57:13.

The design deliberately restores **identity, not activity**:

* the browser remembers only WHICH analysis was open, and words to describe it;
* the reloaded page lands where it always did and fetches nothing;
* a banner offers to continue, and nothing happens until it is pressed;
* resuming never opens step E, because drawing step E starts a measurement.

That last point is the one that makes reloading safe to keep. If a record could
resume straight into a measurement, an analysis that misbehaved would re-enter
the state it misbehaved in on every reload — and reloading is the operator's
way out. The Python side of that rule is `openingStage()`, checked here against
app.js, and the server-side listing the banner's fallback uses.
"""

import os
import re

import pytest

from test_unusable_exposures import SYNTHETIC, _png16

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "phantom_qa", "webapp", "static")


@pytest.fixture(scope="module")
def app_js():
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        return f.read()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


_variant = iter(range(1, 10_000))


def _start_one(client, phantom, finish=False):
    """Upload and take an analysis as far as the caller wants.

    Each call needs a genuinely different image: identical bytes are refused as
    a duplicate, which is exactly the behaviour resuming exists to spare
    operators. One pixel is enough to make it a different exposure."""
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses",
                     files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": phantom, "operator": "Stacy"})
    assert up.status_code == 200, up.text
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.post(f"/api/analyses/{aid}/confirm",
                json={"stage": "C", "save_profile": False})
    if finish:
        client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    return aid


# ------------------------------------------------- resuming never starts work

def test_resuming_never_opens_the_step_that_starts_measuring(app_js):
    """The rule that keeps reload usable as an escape.

    Step E POSTs /compute the moment it is drawn, so opening a record that had
    reached E would begin the work again unasked — and on a record that hangs,
    every reload would do it again."""
    assert "function openingStage(" in app_js
    body = app_js[app_js.index("function openingStage("):]
    body = body[:body.index("\n}")]
    assert '=== "E" ? "D"' in body.replace(" ", "") or 'stage === "E" ? "D"' in body, \
        "a record interrupted at step E must open at D, not at E"
    assert "setStage(openingStage(rec))" in app_js, \
        "openAnalysis must go through the rule, not choose its own stage"


def test_the_banner_fetches_nothing_until_it_is_pressed(app_js):
    """Drawn from what was remembered locally; the server is not touched.

    An analysis that misbehaved must not be re-entered merely because the page
    was reloaded."""
    banner = app_js[app_js.index("function resumeBanner("):]
    banner = banner[:banner.index("\nfunction wireResumeBanner")]
    assert "api(" not in banner and "fetch(" not in banner, \
        "the banner must render from local memory alone"
    assert "rememberedAnalysis()" in banner
    for shown in ("source_name", "phantom", "operator", "stage"):
        assert shown in banner, f"the banner must identify itself by {shown}"


def test_only_unfinished_work_is_remembered(app_js):
    """A finished analysis belongs in History, not in a 'continue' banner."""
    body = app_js[app_js.index("function rememberOpenAnalysis("):]
    body = body[:body.index("\nfunction forgetOpenAnalysis")]
    assert "rec.results" in body and "forgetOpenAnalysis()" in body
    assert "validation_status" in body, \
        "a signed-off analysis must not be offered as unfinished work"


def test_the_note_is_dropped_when_the_work_ends(app_js):
    """Finalise, delete and starting a new analysis all clear it."""
    assert "forgetOpenAnalysis();\n      status(\"Finalized.\")" in app_js \
        or "forgetOpenAnalysis()" in app_js[app_js.index("btn-finalize"):
                                            app_js.index("btn-validate-f")], \
        "finalising must stop offering the analysis as unfinished"
    assert "remembered.aid === aid) forgetOpenAnalysis()" in app_js, \
        "deleting a record must not leave a banner pointing at it"
    assert "forgetOpenAnalysis();" in app_js[app_js.index("function clearAnalysisState"):
                                             app_js.index("function rememberIdentity")
                                             if "function rememberIdentity" in app_js
                                             else app_js.index("function clearAnalysisState") + 900]


def test_hiding_the_banner_destroys_nothing(app_js):
    """It is a note in one browser, and two operators may share a machine."""
    wire = app_js[app_js.index("function wireResumeBanner("):]
    wire = wire[:wire.index("\n/* Everything started")]
    assert "btn-forget-resume" in wire
    assert "delete" not in wire.lower().replace("deleted", ""), \
        "the hide button must never remove the analysis itself"


# ------------------------------------------------ the server-side fallback

def test_unfinished_records_can_be_listed_without_the_whole_history(client):
    """What a browser-local note cannot do: find work handed between machines.

    The audit log shows one analysis uploaded from one address, labelled from
    a second and computed from a third."""
    open_one = _start_one(client, "RESUME-OPEN")
    done = _start_one(client, "RESUME-DONE", finish=True)

    listed = client.get("/api/analyses?unfinished_only=true").json()["analyses"]
    ids = [a["id"] for a in listed]
    assert open_one in ids
    assert done not in ids, "a measured analysis is not unfinished"
    assert listed[0]["operator"] == "Stacy", \
        "the operator label is the only per-person signal there is"


def test_the_unfinished_list_can_be_kept_small(client):
    for i in range(3):
        _start_one(client, f"RESUME-MANY-{i}")
    listed = client.get(
        "/api/analyses?unfinished_only=true&limit=2").json()["analyses"]
    assert len(listed) == 2, "a slow link should not pay for the whole history"


def test_asking_for_both_states_at_once_is_refused(client):
    """They are opposites; answering "nothing" would look like "none exist"."""
    answer = client.get(
        "/api/analyses?unfinished_only=true&completed_only=true")
    assert answer.status_code == 400


def test_resuming_reads_the_record_the_server_still_holds(client):
    """Resume is a normal open: the server remains the source of truth."""
    aid = _start_one(client, "RESUME-TRUTH")
    record = client.get(f"/api/analyses/{aid}").json()
    assert record["id"] == aid
    assert record["results"] is None
    assert record["geometry"], "the work done before the reload is still there"


def test_a_remembered_analysis_that_was_deleted_answers_cleanly(client):
    """The banner can outlive its record; resuming must not break the page."""
    aid = _start_one(client, "RESUME-GONE")
    client.mod.store.delete(aid)
    assert client.get(f"/api/analyses/{aid}").status_code == 404


def test_two_browsers_keep_independent_notes(app_js):
    """One shared account, so isolation comes from where the note lives.

    It is written to this browser's storage and never sent anywhere, so two
    operators on two machines cannot see each other's. On a shared machine they
    would — which is why the banner names the file, phantom, step and operator,
    and why nothing happens until it is pressed."""
    assert "localStorage.setItem(OPEN_KEY" in app_js
    assert "OPEN_KEY" in app_js
    remember = app_js[app_js.index("function rememberOpenAnalysis("):]
    remember = remember[:remember.index("\nfunction forgetOpenAnalysis")]
    assert "postJSON" not in remember and "api(" not in remember, \
        "the note must stay in the browser; the server has no per-user identity"
    assert "catch" in remember, \
        "private browsing disables storage — resume is a convenience, not a need"
