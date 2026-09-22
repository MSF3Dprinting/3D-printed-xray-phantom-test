"""What the independent review of the merged round found, pinned.

Five pieces of work were written in parallel and merged; two reviewers then
read the combined change without having written any of it. Each test here is
one of their findings, fixed — kept so the defect cannot come back unnoticed.
"""

from __future__ import annotations

import base64
import io
import os
import re
import sqlite3

import pytest

from phantom_qa.store import Store
from test_exposure_index import FUJI, _dicom, _legacy, _old_row, _raw_rows

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "phantom_qa", "webapp", "static")


def _app_js():
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        return f.read()


def _fn(src, name):
    start = src.index(f"function {name}(")
    nxt = re.search(r"\n(async )?function ", src[start + 10:])
    return src[start:start + 10 + nxt.start()] if nxt else src[start:]


# ------------------------------------------- the first start after an upgrade

def test_a_worker_that_loses_the_race_to_add_a_column_carries_on(tmp_path):
    """Every gunicorn worker opens the store at start-up. On the first start
    after an upgrade they all read the old column list together, and the one
    that lost the race died on "duplicate column name" — taking the server
    down with it."""
    store = Store(str(tmp_path))
    with store._conn() as c:
        assert Store._add_column(c, "analyses", "exposure_index", "REAL") is False
        assert Store._add_column(c, "analyses", "zz_probe", "TEXT") is True
        assert Store._add_column(c, "analyses", "zz_probe", "TEXT") is False


def test_any_other_database_error_still_stops_start_up(tmp_path):
    """Only the one benign error is swallowed."""
    store = Store(str(tmp_path))
    with store._conn() as c, pytest.raises(sqlite3.OperationalError):
        Store._add_column(c, "no_such_table", "x", "TEXT")


def test_the_exposure_backfill_finishes_even_if_its_first_attempt_did_not(
        tmp_path):
    """It used to run only in the worker that added the columns. A worker
    that died half way — or a race lost — left every older record "not
    recorded" for good, silently. Now it runs until it has completed once."""
    root = str(tmp_path)
    _legacy(root, [_old_row("fuji01")], {"fuji01": _dicom(FUJI)})
    # The columns exist — an earlier start added them — but nothing was filled
    # and nothing says the backfill completed.
    con = sqlite3.connect(os.path.join(root, "data", "phantom_qa.sqlite3"))
    for col in ("exposure_index", "target_exposure_index", "deviation_index",
                "sensitivity"):
        con.execute(f"ALTER TABLE analyses ADD COLUMN {col} REAL")
    con.commit()
    con.close()

    Store(root)
    row = _raw_rows(root)["fuji01"]
    assert row["exposure_index"] == 691 and row["deviation_index"] == -1.0


def test_once_complete_the_backfill_is_not_repeated(tmp_path):
    """Every later start would otherwise re-read every stored header."""
    root = str(tmp_path)
    _legacy(root, [_old_row("fuji01")], {"fuji01": _dicom(FUJI)})
    Store(root)
    con = sqlite3.connect(os.path.join(root, "data", "phantom_qa.sqlite3"))
    assert con.execute("SELECT 1 FROM schema_state WHERE key='exposure_backfill'"
                       ).fetchone()
    con.execute("UPDATE analyses SET exposure_index=NULL")
    con.commit()
    con.close()
    Store(root)
    assert _raw_rows(root)["fuji01"]["exposure_index"] is None


def test_the_finalised_backfill_stays_tied_to_its_column(tmp_path):
    """Deliberately not given the same treatment. It reconstructs which
    records were finished from their audit trails; run again later, it would
    re-finalise records an operator has since reopened with a re-run."""
    src = open(os.path.join(os.path.dirname(STATIC), "..", "store.py"),
               encoding="utf-8").read()
    body = src[src.index("    def _migrate(self, conn):"):
               src.index("    @staticmethod\n    def _backfill_finalized")]
    assert '"finalized_at" in added' in body
    assert "_state_done(conn, \"finalized" not in body


# -------------------------------------------------- the comparison report

def test_comparison_charts_are_palette_pngs():
    """A chart is a few flat colours and text; a palette halves its bytes
    and loses nothing visible — as the single report already does."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image
    from phantom_qa import comparison_report as CR

    fig, ax = plt.subplots(figsize=(4, 2))
    ax.plot([0, 1, 2], [1, 3, 2])
    img = Image.open(io.BytesIO(base64.b64decode(CR._b64(fig))))
    assert img.mode == "P" and len(img.getpalette()) // 3 <= 256


def _recs(n, baseline=None):
    out = []
    for i in range(n):
        out.append({"id": f"a{i:02d}", "results": {"x": 1},
                    "acquired_at": f"2026-09-{i + 1:02d} 10:00:00",
                    "created_at": "", "is_baseline": 1 if i == baseline else 0,
                    "site": "S", "phantom": "P", "status": "pass"})
    return out


def test_the_picture_table_is_capped_and_keeps_the_reference(tmp_path,
                                                            monkeypatch):
    """Sixty scans of one phantom were sixty columns of pictures: about three
    megabytes and minutes of first-time decoding inside one request, on a
    512 kbit/s link. Now at most twelve — the reference first, then the most
    recent — and the charts still cover every scan."""
    from test_authorization import _build_app
    mod = _build_app(tmp_path, monkeypatch)
    monkeypatch.setattr(mod.store, "get", lambda aid: {"id": aid})
    monkeypatch.setattr(mod.store, "exists", lambda aid: True)
    monkeypatch.setattr(mod.store, "thumbs_dir", lambda aid: None)
    monkeypatch.setattr(mod.thumbnails, "pictures_for",
                        lambda rec, d, ctx, **k: {"phantom": rec["id"]})

    recs = _recs(20, baseline=0)          # the reference is the OLDEST scan
    chosen = mod._comparison_pictures(recs)
    assert len(chosen) == mod.PICTURE_COLUMNS_MAX == 12
    assert "a00" in chosen, "the reference scan must always be shown"
    newest = sorted((r["id"] for r in recs[1:]), reverse=True)[:11]
    assert set(chosen) == {"a00", *newest}


def test_scans_left_out_get_no_column_and_the_page_says_so():
    from phantom_qa import comparison_report as CR
    from phantom_qa import thumbnails
    ordered = _recs(3)
    labels = [r["id"] for r in ordered]
    pictures = {"a00": thumbnails.unavailable(thumbnails.FAILED),
                "a02": thumbnails.unavailable(thumbnails.FAILED)}
    page = CR._picture_section(ordered, labels, pictures)
    assert page.count("class='pic-head'") == 2
    assert "Pictures are shown for 2 of 3 scans" in page


def test_nothing_is_said_when_every_scan_has_pictures():
    from phantom_qa import comparison_report as CR
    from phantom_qa import thumbnails
    ordered = _recs(2)
    pictures = {r["id"]: thumbnails.unavailable(thumbnails.FAILED)
                for r in ordered}
    assert "Pictures are shown for" not in CR._picture_section(
        ordered, [r["id"] for r in ordered], pictures)


@pytest.mark.parametrize("status", ["error", "not measured"])
def test_a_geometry_test_that_never_measured_shows_its_own_status(status):
    """It showed a neutral "no result", where the status grid on the same
    page said "error" or "not measured"."""
    from phantom_qa import comparison_report as CR
    got, _ = CR._picture_facts("phantom", {"geometry": {"status": status}})
    assert got == status


# -------------------------------------------------- the printed report

def _lc_result(orientation):
    rows = [{"id": f"L{i}", "disc": f"L{i}", "level": i, "design_level": i,
             "cnr": 0.1 * i, "abs_cnr": 0.1 * i, "obj_mean": 1.0,
             "obj_std": 1.0, "bg_mean": 1.0, "bg_std": 1.0}
            for i in range(1, 9)]
    return {"lowcontrast": {"rows": rows, "status": "warn",
                            "orientation": orientation}}


def test_an_undecided_reading_with_turned_rings_says_what_was_assumed():
    """"Named as drawn" was printed on a signed document when the discs were
    in fact named as if the insert were turned — contradicting the reasons,
    the marking step and the comparison report."""
    from phantom_qa import report
    page = report._lowcontrast_section(
        _lc_result({"source": "undetermined", "flipped": True,
                    "rings_turned": True}), {}, None)
    assert "as if the insert were fitted a half turn round" in page
    assert "named as drawn" not in page


def test_an_undecided_reading_without_turned_rings_still_says_as_drawn():
    from phantom_qa import report
    page = report._lowcontrast_section(
        _lc_result({"source": "undetermined", "flipped": False}), {}, None)
    assert "named as drawn" in page


# -------------------------------------------------- the page

def test_a_finalised_analysis_is_shown_as_locked_before_anything_is_dragged():
    """It looked editable in the marking step and failed only on the server."""
    body = _fn(_app_js(), "signedOff")
    assert "finalized_at" in body and "validation_status" in body


def test_the_lock_notice_names_the_way_through_that_works():
    """Withdrawing a ruling does not un-finalise, so "withdraw first" led
    straight into a second refusal. A re-run is the way through."""
    body = _fn(_app_js(), "lockedBar")
    assert "Re-run analysis" in body
    assert "withdraw the validation" not in body.lower()


def test_finalising_tells_the_page_so_the_discard_button_goes():
    src = _app_js()
    handler = src[src.index('$("#btn-finalize").addEventListener'):]
    handler = handler[:handler.index('status("Finalized.")')]
    assert '"finalized"' in handler and "renderIdentityBar()" in handler


def test_the_close_up_follows_the_block():
    """After a drag, a typed angle or Turn 180° it kept showing the old
    placement until Refresh was pressed."""
    assert "loadBlockView(true)" in _fn(_app_js(), "placeBlock")


def test_an_out_of_date_close_up_is_never_passed_off_as_current():
    body = _fn(_app_js(), "setInsertOrientation")
    assert "S.blockView.seq === before" in body


def test_the_results_table_names_discs_as_the_chart_and_report_do():
    src = _app_js()
    lc = src[src.index("/* low contrast */"):]
    lc = lc[:lc.index("</table>")]
    assert "row.disc || row.id" in lc
