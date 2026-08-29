"""Every filter the History tab offers must reach every output it drives.

index.html tells the operator "The filter drives the table, the trend chart and
the comparison report". The Validation filter did not reach any of the three:
FastAPI ignores an unknown query parameter, so narrowing History to "not
validated" and exporting produced a file covering everything — a wrong list
that looks exactly like a right one.
"""

from __future__ import annotations

import hashlib
import io

import pytest
from fastapi.testclient import TestClient

from test_authorization import ADMIN_PW, _build_app, _login
from test_store_labels import fake_scan, results_for


def _png(seed: int = 0) -> bytes:
    import numpy as np
    from PIL import Image
    arr = np.full((24, 24), 100 + (seed % 50), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    return _build_app(tmp_path, monkeypatch)


@pytest.fixture()
def client(mod):
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    return c


@pytest.fixture()
def two_analyses(mod):
    """One signed off, one not — the exact case an operator filters for."""
    ids = []
    for i, status in enumerate(("validated", "not_validated")):
        payload = _png(i)
        aid = mod.store.new_analysis(
            fake_scan(sha=hashlib.sha256(payload).hexdigest()), payload,
            "sig", "1.0.0", "1.0",
            labels={"site": "Goma", "phantom": "MSF-01"})
        mod.store.update(aid, results=results_for(), status="pass")
        mod.store.set_validation(aid, status, "Dr A")
        ids.append(aid)
    return {"validated": ids[0], "not_validated": ids[1]}


#: Everything the History filter bar drives.
FILTERED = [
    ("/api/analyses", "analyses"),
    ("/api/trends", "trends"),
    ("/api/export.csv", "long CSV"),
    ("/api/export.csv?layout=wide", "wide CSV"),
    ("/api/comparison_report.html", "comparison report"),
]


#: The comparison report identifies analyses by an 8-character prefix, the
#: other outputs by the full id. A prefix match covers both.
def _ref(aid: str) -> str:
    return aid[:8]


@pytest.mark.parametrize("path,label", FILTERED)
def test_the_validation_filter_reaches_every_output(client, two_analyses,
                                                    path, label):
    keep, drop = two_analyses["not_validated"], two_analyses["validated"]
    sep = "&" if "?" in path else "?"
    r = client.get(f"{path}{sep}validation=not_validated")
    assert r.status_code == 200, r.text
    body = r.text
    assert _ref(keep) in body, f"the {label} lost the analysis the filter selected"
    assert _ref(drop) not in body, (
        f"the {label} ignored the validation filter and included an analysis "
        f"the operator had filtered out")


@pytest.mark.parametrize("path,label", FILTERED)
def test_the_pending_sentinel_reaches_every_output(client, mod, two_analyses,
                                                   path, label):
    """"pending" means "nobody has ruled", which is stored as an empty string
    and so needs its own handling all the way down."""
    payload = _png(9)
    fresh = mod.store.new_analysis(
        fake_scan(sha=hashlib.sha256(payload).hexdigest()), payload,
        "sig", "1.0.0", "1.0", labels={"site": "Goma", "phantom": "MSF-01"})
    mod.store.update(fresh, results=results_for(), status="pass")

    sep = "&" if "?" in path else "?"
    r = client.get(f"{path}{sep}validation=pending")
    assert r.status_code == 200, r.text
    assert _ref(fresh) in r.text, f"the {label} lost the unruled analysis"
    for ruled in two_analyses.values():
        assert _ref(ruled) not in r.text, (
            f"the {label} included an analysis that had already been ruled on")


def test_the_site_and_phantom_filters_still_work(client, mod, two_analyses):
    """The regression guard for the change that added validation alongside."""
    payload = _png(21)
    other = mod.store.new_analysis(
        fake_scan(sha=hashlib.sha256(payload).hexdigest()), payload,
        "sig", "1.0.0", "1.0", labels={"site": "Kinshasa", "phantom": "MSF-02"})
    mod.store.update(other, results=results_for(), status="pass")

    body = client.get("/api/export.csv?site=Goma").text
    assert _ref(other) not in body
    assert _ref(two_analyses["validated"]) in body


def test_an_explicit_selection_still_overrides_the_filter(client, two_analyses):
    """Ticked rows take precedence — the filter must not narrow them further."""
    both = ",".join(two_analyses.values())
    body = client.get(f"/api/export.csv?ids={both}&validation=not_validated").text
    for aid in two_analyses.values():
        assert _ref(aid) in body, (
            "an explicitly selected row was dropped by a filter")


def test_the_comparison_report_states_the_validation_filter(client, two_analyses):
    html = client.get("/api/comparison_report.html?validation=not_validated").text
    assert "not_validated" in html, (
        "the report does not say which validation filter produced it")
