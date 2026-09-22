"""Points put on the image are proposals until Apply; re-run reachable from History.

From the first field test, item 3: "When clicking the manual corners for
registration only left mouse button should put the points, we should be able
to pan using middle mouse button" — and later, the part that mattered most:
"the main issue with clicking was that we mistakenly clicked sometimes".

The phantom corners were reworked for that: left button only, panning while
picking, draggable points, Undo last point, and nothing sent until Apply. The
two other places that ask for clicks on the image were not. The low-contrast
block applied itself on the fourth corner and a field edge on the first click,
so a slip there still went straight to the server and could only be undone
after the fact. All three now work one way, from one table (PICKERS), so they
cannot drift apart again.

The second half is the re-run. The plan asked for it from step F *and* from
History; it was only in step F, which meant opening an analysis just to reach
the button. History now offers the same panel on every row that has results,
with the same administrator-password rule step F applies.

There is no JavaScript runtime in the deployment and no build step, so the
browser half is checked structurally, as tests/test_upload_progress.py does.
The server half — what the History row needs to know — is checked through the
API, as tests/test_rerun.py does.
"""

from __future__ import annotations

import os
import re

import pytest

from test_authorization import ADMIN_PW
from test_unusable_exposures import SYNTHETIC, _png16

STATIC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "phantom_qa", "webapp", "static")

JOBS = ("corners", "lccorners", "fieldedge")


def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def app_js():
    return _read("app.js")


@pytest.fixture(scope="module")
def index_html():
    return _read("index.html")


def _fn(app_js, name):
    """One top-level function, from its definition to its closing brace.

    Top-level functions in app.js close with a brace in the first column, so
    the first such brace after the definition ends it."""
    start = app_js.index(f"function {name}(")
    return app_js[start:app_js.index("\n}\n", start) + 2]


def _handler(app_js, event):
    """The canvas listener for one mouse event, up to the next listener."""
    start = app_js.index(f'canvas.addEventListener("{event}"')
    return app_js[start:app_js.index("canvas.addEventListener(", start + 10)]


def _pickers(app_js):
    start = app_js.index("const PICKERS = {")
    return app_js[start:app_js.index("\n};\n", start)]


def _job(app_js, mode):
    """One entry of the PICKERS table."""
    table = _pickers(app_js)
    start = table.index(f"  {mode}: {{")
    return table[start:table.index("\n  },", start)]


# -------------------------------------------------- one way of placing points

def test_every_picking_job_is_in_one_table(app_js):
    """Three copies of the controls are how two of them stayed uncorrectable
    while the third was fixed. One table means one set of behaviour."""
    for mode in JOBS:
        entry = _job(app_js, mode)
        for field in ("points:", "applyLabel:", "apply:", "cancelled:"):
            assert field in entry, f"the {mode} job has no {field}"
    assert "points: 4" in _job(app_js, "lccorners")
    assert "points: 1" in _job(app_js, "fieldedge")
    assert '"Apply corners"' in _job(app_js, "lccorners")
    assert '"Apply edge"' in _job(app_js, "fieldedge")


def test_the_mouse_handlers_treat_every_job_alike(app_js):
    """Picking is recognised from the table, not from a list of mode names
    that a fourth job could be left out of."""
    assert "const picking = !!PICKERS[S.mode];" in _handler(app_js, "mousedown")
    assert "const placing = !!PICKERS[S.mode];" in _handler(app_js, "mouseup")


def test_the_buttons_start_picking_through_the_one_entry(app_js):
    """The block-corner and field-edge buttons used to set the mode by hand and
    skip the controls altogether; that is how they came to apply at once."""
    assert 'startPicking("corners")' in app_js
    assert 'startPicking("lccorners")' in app_js
    assert 'startPicking("fieldedge", b.dataset.side)' in app_js
    for mode in JOBS:
        assert f'S.mode = "{mode}"' not in app_js, \
            f"{mode} is still entered around startPicking"


def test_the_old_block_corner_list_is_gone(app_js):
    """It was the state that submitted on its fourth entry. Leaving it behind
    would invite the next change to start using it again."""
    assert "lcCorners" not in app_js


# ------------------------------------------------------- nothing sent by a click

def test_the_fourth_block_corner_is_not_sent(app_js):
    """The complaint in its plainest form: one slip and the block had moved."""
    up = _handler(app_js, "mouseup")
    assert "corners_px" not in up and "lowcontrast_block" not in up, \
        "releasing the mouse must not place the block"
    place = _fn(app_js, "placePickPoint")
    for sender in ("placeBlock", "postJSON", "applyPicking", "submit"):
        assert sender not in place, f"placing a point calls {sender}"
    assert "S.manualCorners.length < job.points" in place, \
        "a stray fifth click must not replace a corner already judged"


def test_a_field_edge_click_is_a_candidate_not_a_request(app_js):
    """The edge went to the server on the first click, and the only remedy for
    a click that missed was Undo afterwards."""
    up = _handler(app_js, "mouseup")
    assert "submitFieldEdge" not in up and "field_edge" not in up, \
        "the field-edge click still posts immediately"
    place = _fn(app_js, "placePickPoint")
    assert "S.manualCorners = [nat]" in place, \
        "a second click must move the candidate, not add a second edge"
    send = _fn(app_js, "submitFieldEdge")
    assert "function submitFieldEdge()" in send, \
        "the edge is read from the placed candidate, not from the click"
    assert "S.manualCorners[0]" in send


def test_apply_is_the_only_road_to_the_server(app_js):
    """Each job's sender is reached from its table entry and nowhere else, and
    the table is only consulted by Apply — the button or Enter."""
    for sender in ("submitManualCorners", "submitBlockCorners", "submitFieldEdge"):
        calls = [m.start() for m in re.finditer(rf"\b{sender}\(", app_js)]
        defined = app_js.index(f"async function {sender}(")
        others = [c for c in calls if c != defined + len("async function ")]
        assert len(others) == 1, \
            f"{sender} is called from {len(others)} places, not just Apply"
        assert _pickers(app_js).count(f"{sender}()") == 1
    apply_calls = [m.start() for m in re.finditer(r"\bapplyPicking\(\)", app_js)
                   if not app_js.startswith("function ", m.start() - 9)]
    assert len(apply_calls) == 2, "Apply button and Enter, nothing else"
    assert "job.apply()" in _fn(app_js, "applyPicking")
    assert "S.manualCorners.length === job.points" in _fn(app_js, "applyPicking"), \
        "Apply must not send an incomplete set"


def test_the_block_still_goes_through_its_own_endpoint(app_js):
    """The server contract is unchanged; only the moment of sending moved.
    placeBlock keeps the refusal handling and the resync in one place."""
    body = _fn(app_js, "submitBlockCorners")
    assert "placeBlock({ corners_px: corners })" in body
    assert body.index("endPicking()") < body.index("placeBlock("), \
        "the points must stop being editable before they are sent"


def test_cancel_sends_nothing(app_js):
    body = _fn(app_js, "cancelCornerPicking")
    assert "postJSON" not in body and "api(" not in body
    assert "job.cancelled" in body, "the operator is told nothing changed"


# ---------------------------------------------------------- only the left button

def test_only_a_left_click_places_for_every_job(app_js):
    """A middle click meant as a pan used to drop a point. The guard sits in
    front of the one call every job places through."""
    up = _handler(app_js, "mouseup")
    guard = up.index("if (ev.button !== LEFT || spaceHeld)")
    assert up.index("placePickPoint(") > guard
    assert "> 4) return" in up[guard:up.index("placePickPoint(")], \
        "a left press that travelled is a drag, not a placement"

    down = _handler(app_js, "mousedown")
    picking = down[down.index("if (picking) {"):]
    assert "if (ev.button !== LEFT || spaceHeld) { startPan(pos); return; }" \
        in picking, "middle, right and Space+drag must pan while picking"
    assert "hitCorner(pos)" in picking, "a press on a placed point grabs it"


# --------------------------------------------------- the controls, for every job

def test_the_strip_serves_every_job(app_js):
    body = _fn(app_js, "renderCornerControls")
    assert "const job = PICKERS[S.mode];" in body
    assert 'S.mode !== "corners"' not in body, \
        "the strip is still shown for the phantom corners only"
    for control in ("btn-corners-apply", "btn-corners-undo", "btn-corners-cancel"):
        assert f'id="{control}"' in body, f"the strip has no {control}"
    assert "${job.applyLabel}" in body, "Apply must say what it applies"
    assert 'n === job.points ? "" : "disabled"' in body, \
        "Apply must wait for every point the job needs"


def test_the_controls_are_wired(app_js):
    body = _fn(app_js, "renderCornerControls")
    wiring = {
        "btn-corners-apply": "applyPicking()",
        "btn-corners-undo": "S.manualCorners.pop()",
        "btn-corners-cancel": "cancelCornerPicking()",
    }
    for control, action in wiring.items():
        start = body.index(f'$("#{control}").addEventListener("click"')
        assert action in body[start:start + 200], \
            f"#{control} does not {action}"


def test_the_keyboard_serves_every_job(app_js):
    start = app_js.index("if (!job || isTypingTarget(e.target)) return;")
    keys = app_js[app_js.rindex("document.addEventListener", 0, start):
                  app_js.index("\n});", start)]
    assert "const job = PICKERS[S.mode];" in keys
    assert "applyPicking()" in keys and "S.manualCorners.length === job.points" in keys
    assert "cancelCornerPicking()" in keys and "Backspace" in keys
    assert "ArrowLeft" in keys, "a placed point is nudged the same way in every job"


def test_leaving_the_step_drops_points_nobody_applied(app_js):
    """A half-placed set of block corners must not sit, Apply button and all,
    over step D — where pressing it would move the block after the measuring
    points were confirmed."""
    body = _fn(app_js, "setStage")
    assert "if (st !== S.stage && PICKERS[S.mode]) endPicking();" in body
    assert "renderCornerControls()" in _fn(app_js, "clearAnalysisState"), \
        "closing an analysis must also take the strip down"


def test_every_element_the_new_code_drives_exists(app_js, index_html):
    """The strip and the re-run panel build and read their own markup; the two
    halves can still disagree about an id."""
    names = ("renderCornerControls", "cancelCornerPicking", "startPicking",
             "endPicking", "placePickPoint", "applyPicking",
             "submitManualCorners", "submitBlockCorners", "submitFieldEdge",
             "rerunDialog", "rerunAnalysis", "loadHistory")
    driven = set()
    for name in names:
        driven |= set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', _fn(app_js, name)))
    assert {"corner-controls", "btn-corners-apply", "rerun-target"} <= driven
    for ref in sorted(driven):
        assert f'id="{ref}"' in index_html or f'id="{ref}"' in app_js, \
            f"#{ref} is driven but never created"


# ------------------------------------------------------------ re-run, in History

def test_history_offers_rerun_only_where_there_are_results(app_js):
    """A record with nothing measured has nothing to re-run; opening it is the
    way on, and a link that could only be refused would teach distrust."""
    body = _fn(app_js, "loadHistory")
    assert 'class="rerun"' in body
    link = body.index('class="rerun"')
    assert "a.has_results ?" in body[link - 200:link], \
        "the row action must depend on the row having results"


def test_history_rerun_is_the_step_f_panel(app_js):
    """The same dialog and the same request, fed with the row — so the three
    starting points, the warnings and the password rule are identical."""
    body = _fn(app_js, "loadHistory")
    start = body.index('tb.querySelectorAll("a.rerun")')
    handler = body[start:body.index("}));", start)]
    assert "rerunAnalysis(rec.id, rec)" in handler
    assert "H.rows.find" in handler, "the row, which carries its protection"

    rerun = _fn(app_js, "rerunAnalysis")
    assert "function rerunAnalysis(aid, rec = S.record)" in rerun, \
        "step F must keep working with the open record"
    assert "rerunDialog(" in rerun
    assert "needsAdmin: protection.length > 0" in rerun
    assert "openAnalysis(aid)" in rerun, "a started re-run opens the analysis"
    assert '$("#btn-rerun").addEventListener("click", () => rerunAnalysis(S.aid))' \
        in app_js, "step F's button is unchanged"


def test_the_panel_says_which_analysis_it_is_about(app_js, index_html):
    """From a History row nothing else on screen names the record — and the
    administrator password is about to be typed for it."""
    assert 'id="rerun-target"' in index_html
    assert '$("#rerun-target").textContent = target' in _fn(app_js, "rerunDialog")
    assert "target:" in _fn(app_js, "rerunAnalysis")


# ------------------------------------------------ what the History row is told

#: A distinct first pixel per upload, inside the 16-bit range, so no two scans
#: in one test are refused as the same file.
_variant = iter(range(50_000, 60_000))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _uploaded(client, phantom):
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    up = client.post("/api/analyses", files={"file": ("s.png", _png16(pixels))},
                     data={"site": "T", "phantom": phantom})
    assert up.status_code == 200, up.text
    return up.json()["analyses"][0]["id"]


def _measured(client, phantom):
    aid = _uploaded(client, phantom)
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    client.post(f"/api/analyses/{aid}/confirm",
                json={"stage": "C", "save_profile": False})
    client.post(f"/api/analyses/{aid}/compute", json={"sid_mm": 1000.0})
    return aid


def _row(client, aid):
    rows = client.get("/api/analyses").json()["analyses"]
    return next(r for r in rows if r["id"] == aid)


def test_the_listing_says_which_rows_have_results(client):
    """Without it the row could not decide whether to offer re-run — short of
    sending every record's results with the listing, which is what the slow
    links cannot afford."""
    fresh = _uploaded(client, "PICK-FRESH")
    measured = _measured(client, "PICK-MEASURED")
    assert _row(client, fresh)["has_results"] is False
    assert _row(client, measured)["has_results"] is True


def test_the_row_and_the_record_agree_about_protection(client):
    """Step F asks for the password when the record is protected; History has
    to ask in exactly the same cases, so both read the one definition."""
    aid = _measured(client, "PICK-PROTECT")
    assert _row(client, aid)["protection"] == []
    client.post(f"/api/analyses/{aid}/finalize", json={})
    record = client.get(f"/api/analyses/{aid}").json()
    assert _row(client, aid)["protection"] == record["protection"] == ["finalized"]


@pytest.mark.parametrize("start", ["registration", "points", "results"])
def test_a_rerun_opens_where_the_server_says(client, start):
    """The page opens the record through openingStage(); this is that rule in
    Python. It must land on the step the re-run endpoint reports, or History
    would drop the operator somewhere the server did not reopen."""
    aid = _measured(client, f"PICK-OPEN-{start}")
    answer = client.post(f"/api/analyses/{aid}/rerun", json={"start": start})
    assert answer.status_code == 200, answer.text
    rec = client.get(f"/api/analyses/{aid}").json()
    if not rec["geometry"]:
        opens_at = "A"
    elif rec["results"]:
        opens_at = "F"
    else:
        opens_at = "D" if rec["stage"] == "E" else rec["stage"]
    assert opens_at == answer.json()["stage"]


def test_a_protected_row_is_refused_plainly_without_an_administrator_password(
        tmp_path, monkeypatch):
    """What History shows when nobody configured the administrator password:
    the server's own refusal, saying why — exactly as step F shows it."""
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch, PHANTOMQA_ADMIN_PASSWORD_HASH="")
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    aid = _measured(c, "PICK-NOADMIN")
    c.post(f"/api/analyses/{aid}/finalize", json={})
    assert _row(c, aid)["protection"] == ["finalized"], \
        "the row must know to ask for the password"
    refused = c.post(f"/api/analyses/{aid}/rerun",
                     json={"start": "results", "admin_password": "anything",
                           "reason": "trying from History"})
    assert refused.status_code == 403
    assert "none is configured" in refused.json()["detail"]


def test_a_protected_row_reruns_with_the_password_and_a_reason(client):
    aid = _measured(client, "PICK-ADMIN")
    client.post(f"/api/analyses/{aid}/finalize", json={})
    assert client.post(f"/api/analyses/{aid}/rerun",
                       json={"start": "results"}).status_code == 401
    ok = client.post(f"/api/analyses/{aid}/rerun",
                     json={"start": "results", "admin_password": ADMIN_PW,
                           "reason": "re-run from the History row"})
    assert ok.status_code == 200, ok.text
    row = _row(client, aid)
    assert row["has_results"] is False and row["protection"] == [], \
        "reopened: nothing measured yet, nothing protected"
