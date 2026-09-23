"""Less waiting, with nothing measured or shown differently.

A profiling pass over one analysis, upload to comparison, found five places
where the operator waited for work that did not need doing — or for the
computer deliberately running slowly. Each is fixed without changing a number,
a picture or a word the operator gets, and each test here fails either if its
fix is undone or if the fix changed what arrives:

  run_app.py asks Windows not to throttle it. The terminal running the local
  server is in the background the whole time the operator works in the
  browser, and Windows ran it on its power-saving settings: registration to
  patterns took 2.3 s instead of 0.94 s. Only the local starter does this; a
  server deployment runs gunicorn, which never runs that file.

  A page served from the same computer, by a server without sign-in, sends a
  scan as it is. Packing saves time on a link, and there is no link: it was
  half a second of waiting, most of it with the page frozen, to save nothing.
  A tunnel at localhost to a deployed server, which has sign-in, still packs.

  The comparison report palette-encodes each chart on a helper thread while
  the next is drawn — 5.2 s became 4.6 s for two reference scans — and the
  page is byte for byte the one it was.

  The low-contrast close-up is computed once per placement instead of twice:
  the markers request keeps the picture it computed anyway, and the picture
  request that follows is a lookup when the same process answers it. Under
  gunicorn another worker may, and it then renders exactly as before.

  History reads the per-test statuses behind its verdict notes in the listing
  query itself, instead of walking every row's results a second time.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import runpy
import sqlite3
import subprocess
import sys
import textwrap
import threading
import types

import numpy as np
import pytest

import run_app
from phantom_qa import comparison_report as cr
from phantom_qa import pipeline
from phantom_qa.store import Store
from test_block_view import _proposed
from test_review_fixes import _app_js, _fn
from test_store_labels import add, fake_scan, results_for

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FIELD_NOTE = "X-ray field alignment not checked (no field edge found in the image)"

#: Every (test, key) History reads for its notes, as the route asks for them.
FIELDS = [(test, key) for test, key, _ in pipeline.STATUS_FIELDS]

_variant = iter(range(50_000, 59_000))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _counted(monkeypatch, owner, name):
    """Replace ``owner.name`` by a pass-through that notes every call."""
    calls = []
    real = getattr(owner, name)

    def counting(*a, **k):
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(owner, name, counting)
    return calls


# ------------------------------------------- 1. Windows power saving, local only

class _Kernel32Call:
    """One kernel32 function, stood in for: notes its calls, gives one answer."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.answer


def _fake_windows(monkeypatch, *, accepted=1, has_the_call=True):
    """A Windows whose kernel32 is a recorder, so what is asked of it can be
    read back without changing anything about this test run."""
    pytest.importorskip("ctypes.wintypes")
    k32 = types.SimpleNamespace(GetCurrentProcess=_Kernel32Call(-1), opened=[])
    if has_the_call:
        k32.SetProcessInformation = _Kernel32Call(accepted)

    def windll(name, **kw):
        k32.opened.append(name)
        return k32

    monkeypatch.setattr(ctypes, "WinDLL", windll, raising=False)
    monkeypatch.setattr(run_app, "os", types.SimpleNamespace(name="nt"))
    return k32


def test_the_opt_out_asks_windows_for_exactly_the_documented_setting(
        monkeypatch):
    """PROCESS_POWER_THROTTLING_STATE version 1, execution speed as the one
    setting controlled, and its state off. With the state bit set the same
    call asks for the throttling instead; with the control bit clear it hands
    the decision back to Windows — either way the server stays slow."""
    k32 = _fake_windows(monkeypatch)
    assert run_app._run_at_full_speed() is True
    assert k32.opened == ["kernel32"]
    (handle, info_class, state_ref, size), = k32.SetProcessInformation.calls
    assert handle == -1, "only this process, the pseudo-handle of itself"
    assert info_class == 4, "ProcessPowerThrottling"
    state = state_ref._obj
    assert (state.Version, state.ControlMask, state.StateMask) == (1, 0x1, 0)
    assert size == ctypes.sizeof(state) == 12


def test_windows_declining_is_an_answer_not_an_error(monkeypatch):
    _fake_windows(monkeypatch, accepted=0)
    assert run_app._run_at_full_speed() is False


@pytest.mark.parametrize("fault", ["no kernel32", "Windows 7: no such call",
                                   "the call itself fails"])
def test_the_opt_out_never_stops_the_server_starting(monkeypatch, fault):
    """A saving, never a condition: on any Windows that cannot do it the
    server starts exactly as it always did."""
    if fault == "no kernel32":
        def windll(name, **kw):
            raise OSError("[WinError 126] The specified module could not be found")
        monkeypatch.setattr(ctypes, "WinDLL", windll, raising=False)
        monkeypatch.setattr(run_app, "os", types.SimpleNamespace(name="nt"))
    elif fault == "Windows 7: no such call":
        _fake_windows(monkeypatch, has_the_call=False)
    else:
        k32 = _fake_windows(monkeypatch)

        def refuses(*args):
            raise OSError("[WinError 5] Access is denied")
        k32.SetProcessInformation = refuses
    assert run_app._run_at_full_speed() is False


def test_the_opt_out_is_asked_for_on_windows_only(monkeypatch):
    opened = []
    monkeypatch.setattr(ctypes, "WinDLL",
                        lambda *a, **k: opened.append(a), raising=False)
    monkeypatch.setattr(run_app, "os", types.SimpleNamespace(name="posix"))
    assert run_app._run_at_full_speed() is False
    assert not opened, "nothing Windows-specific may be reached elsewhere"


@pytest.mark.skipif(os.name != "nt", reason="Windows power throttling")
@pytest.mark.parametrize("accepted", [1, 0])
def test_the_local_starter_uses_it_and_says_once_what_it_did(
        monkeypatch, capsys, accepted):
    """The function alone proves nothing: without the call in the starter the
    server runs throttled exactly as before. And the one line at start is how
    a slow session is told apart from a throttled one without guessing."""
    k32 = _fake_windows(monkeypatch, accepted=accepted)
    started = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append((a, k)))
    monkeypatch.setattr("phantom_qa.config.get_config",
                        lambda: types.SimpleNamespace(
                            summary=lambda: "test", auth_enabled=True,
                            behind_proxy=False))
    monkeypatch.setattr(sys, "argv", ["run_app.py"])
    runpy.run_path(os.path.join(REPO, "run_app.py"), run_name="__main__")
    out = capsys.readouterr().out
    assert len(k32.SetProcessInformation.calls) == 1
    assert out.count("Windows power saving") == 1
    assert ("turned off for this server" in out) == bool(accepted)
    assert ("could not be turned off" in out) == (not accepted)
    assert len(started) == 1, "the server must start either way"


@pytest.mark.skipif(os.name != "nt", reason="Windows power throttling")
def test_on_this_windows_the_process_really_is_no_longer_throttled():
    """Read back from Windows itself, in a process of its own so that this
    test run is not changed by it."""
    probe = textwrap.dedent("""
        import ctypes, json
        from ctypes import wintypes
        import run_app

        class State(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG),
                        ("ControlMask", wintypes.ULONG),
                        ("StateMask", wintypes.ULONG)]

        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetProcessInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]

        def read():
            s = State(1, 0, 0)
            ok = k32.GetProcessInformation(k32.GetCurrentProcess(), 4,
                                           ctypes.byref(s), ctypes.sizeof(s))
            return [bool(ok), s.ControlMask, s.StateMask]

        before = read()
        accepted = run_app._run_at_full_speed()
        print(json.dumps({"before": before, "accepted": accepted,
                          "after": read()}))
    """)
    done = subprocess.run([sys.executable, "-c", probe], cwd=REPO,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    if not seen["before"][0]:
        pytest.skip("this Windows cannot report a process's power throttling")
    assert seen["accepted"] is True
    assert seen["after"][1:] == [1, 0], "execution speed controlled, and off"


def test_a_server_deployment_never_runs_the_opt_out():
    """gunicorn loads phantom_qa.webapp.main through gunicorn.conf.py. The
    setting belongs to the local starter alone: a server's power policy is its
    administrator's business, so nothing on that path may pull it in."""
    paths = [os.path.join(REPO, "gunicorn.conf.py")]
    for root, _, files in os.walk(os.path.join(REPO, "phantom_qa")):
        paths += [os.path.join(root, f) for f in files if f.endswith(".py")]
    reaches = re.compile(r"^\s*(import\s+run_app|from\s+run_app\s+import)"
                         r"|import_module\(\s*['\"]run_app"
                         r"|SetProcessInformation|PowerThrottling", re.M)
    offenders = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            if reaches.search(f.read()):
                offenders.append(os.path.relpath(path, REPO))
    assert not offenders, f"the server path reaches the opt-out: {offenders}"


# ------------------------------- 2. no packing when page and server share a PC

def _loopback_rule(js):
    """The page's own test for "this computer", as a Python pattern.

    Taken from app.js rather than restated, so the hostnames below are judged
    by exactly the rule the page applies."""
    found = re.search(r"const LOOPBACK_HOST = /(.+)/;", js)
    assert found, "the page no longer names the loopback hosts in one place"
    return re.compile(found.group(1))


@pytest.mark.parametrize("host", [
    "localhost", "LocalHost", "127.0.0.1", "127.0.1.1", "127.255.255.254",
    "[::1]", "::1"])
def test_a_page_from_this_computer_sends_the_file_as_it_is(host):
    """Every name a browser can give the machine it runs on, which is how the
    page is reached when run_app.py serves it."""
    js = _app_js()
    here = _fn(js, "servedFromThisMachine")
    assert "location.hostname" in here and ".toLowerCase()" in here
    assert _loopback_rule(js).search(host.lower()), host


@pytest.mark.parametrize("host", [
    "192.168.1.20", "10.0.0.7", "172.16.4.2", "phantomqa.msf.org", "qa-server",
    "localhost.msf.org", "127.0.0.1.nip.io", "my127.0.0.1", "128.0.0.1",
    "0.0.0.0", "[::2]", "[fe80::1]", "localhos", ""])
def test_a_page_from_anywhere_else_still_packs(host):
    """A LAN address or a server name is a network, however fast; a field site
    always reaches the server over one. Getting this wrong costs a minute of a
    512 kbit/s link on every upload, so only the loopback names count."""
    assert not _loopback_rule(_app_js()).search(host.lower()), host


def test_the_skip_comes_after_the_fingerprint_and_before_any_packing():
    """The fingerprint and the "is it already here?" check still run first —
    they are not about the link — while nothing of the packing does: no probe,
    and no "Preparing the file…" phase for a file that will not be packed."""
    js = _app_js()
    pack = _fn(js, "packForUpload")
    skip = pack.index("if (servedFromThisMachine()) return asIs;")
    assert skip < pack.index("onProgress(0, file.size)")
    assert skip < pack.index("file.slice(0, PACK_PROBE_BYTES)")
    assert skip < pack.index("gzipBlob(")
    upload = _fn(js, "uploadFile")
    assert upload.index("fileSha256(file)") < upload.index("findExistingCopy(") \
        < upload.index("packForUpload(")


def test_a_tunnel_at_localhost_to_a_deployed_server_still_packs():
    """An SSH tunnel or a port forward to a deployed server is opened at
    localhost too. Judged by the name alone, every scan then crossed the
    512 kbit/s link unpacked — about four minutes instead of two for a
    Carestream scan. So the name counts only with the server's own word that
    it has no sign-in, which a deployment does not give; until that word has
    come, or when /api/auth failed, the page packs."""
    js = _app_js()
    here = _fn(js, "servedFromThisMachine")
    assert re.search(r"return S\.signInOff === true\s*&& LOOPBACK_HOST\.test\(",
                     here), here
    start = js.index("const S = {")
    state = js[start:js.index("\n};\n", start)]
    assert re.search(r"^\s*signInOff: false,$", state, re.M), \
        "the page must pack until the server has answered"
    auth = _fn(js, "initAuth")
    said = auth.index("S.signInOff = a.auth_enabled === false;")
    assert auth.index('await api("api/auth")') < said < auth.index("catch (e)")
    assert len(re.findall(r"S\.signInOff\s*=(?!=)", js)) == 1, \
        "only the server's answer may set it"


@pytest.mark.parametrize("env, sign_in", [("development", False),
                                          ("production", True)],
                         ids=["run_app.py as it comes", "a deployment"])
def test_only_the_local_default_answers_that_it_has_no_sign_in(
        tmp_path, monkeypatch, env, sign_in):
    """The page skips packing on nothing but a JSON false from /api/auth.
    run_app.py with no .env gives it; a production server, where sign-in is
    on unless someone turns it off, does not — so at localhost the one skips
    packing and the other, reached through a tunnel, packs."""
    from fastapi.testclient import TestClient
    from phantom_qa.config import Config
    from phantom_qa.security import hash_password
    from test_authorization import _build_app
    monkeypatch.setenv("PHANTOMQA_ENV", env)
    monkeypatch.setenv("PHANTOMQA_SECRET_KEY", "k" * 50)
    monkeypatch.setenv("PHANTOMQA_PASSWORD_HASH",
                       hash_password("a-password-1234", rounds=1000))
    for name in ("PHANTOMQA_AUTH_ENABLED", "PHANTOMQA_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    default = Config(env_path=str(tmp_path / "no.env")).auth_enabled
    assert default is sign_in

    mod = _build_app(tmp_path, monkeypatch, PHANTOMQA_ENV=env,
                     PHANTOMQA_AUTH_ENABLED=str(default).lower())
    answer = TestClient(mod.app).get("/api/auth").json()
    assert answer["auth_enabled"] is sign_in
    # What the page makes of it at localhost: `a.auth_enabled === false`,
    # then the loopback rule, both pinned by the test above.
    skips = answer["auth_enabled"] is False \
        and bool(_loopback_rule(_app_js()).search("localhost"))
    assert skips is (not sign_in)


# ------------------------------------- 3. comparison charts: same page, sooner

def _records(tmp_path, labels=None):
    """Three analyses that differ in every charted number."""
    store = Store(str(tmp_path))
    labels = labels or ["Goma", "Goma", "Bunia"]
    for i, site in enumerate(labels):
        add(store, site, f"MSF-0{i}", f"{next(_variant):064d}",
            results_for(sd=100 + 12 * i, cnr=-0.8 - 0.1 * i, snr=47 - 2 * i,
                        wedge_mean=3200 + 50 * i),
            acquired=(f"2026070{i + 1}", "0900"))
    return [store.get(r["id"]) for r in store.list_all()]


def _chart_threads():
    return [t for t in threading.enumerate()
            if t.name.startswith("comparison-chart")]


def _drawing_order(monkeypatch, recs):
    """Each chart's PNG digest, in the order the page draws the charts."""
    order = []
    real = cr._palette_b64

    def noting(png):
        order.append(hashlib.sha256(png).hexdigest())
        return real(png)

    monkeypatch.setattr(cr, "_palette_b64", noting)
    cr._compose_comparison_report(recs)
    monkeypatch.setattr(cr, "_palette_b64", real)
    assert len(order) >= 4 and len(set(order)) == len(order), \
        "the scenario needs several charts that can be told apart"
    return order


def _failing_from(order, first, real):
    """A palette step that fails for every chart from ``first`` on, each in
    its own words, so only the error encoding in turn meets first is right."""
    def palette(png):
        i = order.index(hashlib.sha256(png).hexdigest())
        if i >= first:
            raise OSError(f"chart {i} could not be encoded") \
                from MemoryError(f"chart {i}")
        return real(png)
    return palette


def test_the_comparison_page_is_byte_for_byte_the_one_encoded_in_turn(
        tmp_path, monkeypatch):
    """The helper threads change when each chart is encoded, never what is
    encoded or where it lands on the page."""
    from matplotlib.figure import Figure
    recs = _records(tmp_path)
    drawn_on, encoded_on = set(), set()
    real_savefig, real_palette = Figure.savefig, cr._palette_b64

    def savefig(self, *a, **k):
        drawn_on.add(threading.current_thread().name)
        return real_savefig(self, *a, **k)

    def palette(png):
        encoded_on.add(threading.current_thread().name)
        return real_palette(png)

    monkeypatch.setattr(Figure, "savefig", savefig)
    monkeypatch.setattr(cr, "_palette_b64", palette)
    for pictures in (None, {}):
        pooled = cr.build_comparison_report(recs, filters={"site": "Goma"},
                                            pictures=pictures)
        in_turn = cr._compose_comparison_report(
            recs, filters={"site": "Goma"}, pictures=pictures)
        assert pooled == in_turn
        assert pooled.count("data:image/png;base64,") >= 6
        assert "@@chart-" not in pooled

    me = threading.current_thread().name
    assert drawn_on == {me}, "matplotlib must only ever run on the caller's thread"
    helpers = {n for n in encoded_on if n.startswith("comparison-chart")}
    assert helpers, "the palette step never reached the helper threads"
    assert encoded_on == helpers | {me}
    assert not _chart_threads(), "the helper threads outlived the report"
    assert cr._CHART_BATCH.get() is None


def test_a_chart_that_fails_fails_the_report_with_the_same_error(
        tmp_path, monkeypatch):
    """In turn, the first chart that failed stopped the report there. With the
    helper threads the page goes on being drawn, and later charts fail too —
    but the error raised is still the first one's."""
    recs = _records(tmp_path)
    order = _drawing_order(monkeypatch, recs)
    monkeypatch.setattr(cr, "_palette_b64",
                        _failing_from(order, 2, cr._palette_b64))
    with pytest.raises(OSError) as in_turn:
        cr._compose_comparison_report(recs)
    with pytest.raises(OSError) as pooled:
        cr.build_comparison_report(recs)
    assert str(in_turn.value) == "chart 2 could not be encoded"
    assert str(pooled.value) == str(in_turn.value)
    assert repr(pooled.value.__cause__) == repr(in_turn.value.__cause__), \
        "what the chart's error came from must travel with it"
    assert not _chart_threads()
    assert cr._CHART_BATCH.get() is None


def test_a_chart_failure_comes_before_anything_the_page_ran_into_after_it(
        tmp_path, monkeypatch):
    """The pictures table is composed after every chart is drawn. In turn a
    failed chart meant the table was never reached, so its own failure must
    not be what the report dies of."""
    recs = _records(tmp_path)
    order = _drawing_order(monkeypatch, recs)
    monkeypatch.setattr(cr, "_palette_b64",
                        _failing_from(order, 1, cr._palette_b64))

    def table(*a, **k):
        raise RuntimeError("the pictures table")

    monkeypatch.setattr(cr, "_picture_section", table)
    with pytest.raises(OSError, match="chart 1 could not be encoded"):
        cr._compose_comparison_report(recs, pictures={})
    with pytest.raises(OSError, match="chart 1 could not be encoded") as pooled:
        cr.build_comparison_report(recs, pictures={})
    assert not isinstance(pooled.value.__context__, RuntimeError), \
        "the table's failure must not be reported as what the chart hit"
    assert not _chart_threads()


def test_a_page_that_fails_with_every_chart_fine_fails_as_it_did(
        tmp_path, monkeypatch):
    recs = _records(tmp_path)

    def table(*a, **k):
        raise RuntimeError("the pictures table")

    monkeypatch.setattr(cr, "_picture_section", table)
    with pytest.raises(RuntimeError, match="the pictures table"):
        cr.build_comparison_report(recs, pictures={})
    assert not _chart_threads()
    assert cr._CHART_BATCH.get() is None


def test_nothing_typed_on_the_page_can_pass_for_a_chart(tmp_path, monkeypatch):
    """A site label is typed by an operator and printed on the page. Were it
    to read like a chart's placeholder, the page would swap a picture into it
    — so the placeholder carries a string drawn afresh for every report, and
    one copied from an earlier report is only text."""
    tokens = []
    real_submit = cr._ChartBatch.submit

    def submit(self, png):
        tokens.append(real_submit(self, png))
        return tokens[-1]

    monkeypatch.setattr(cr._ChartBatch, "submit", submit)
    cr.build_comparison_report(_records(tmp_path / "earlier"))
    copied = tokens[0]
    shaped = "@@chart-0123456789abcdef-0@@"
    recs = _records(tmp_path / "now", labels=[copied, shaped, "Goma"])
    page = cr.build_comparison_report(recs)
    assert copied in page and shaped in page
    assert page == cr._compose_comparison_report(recs)


# ----------------------------------------- 4. the close-up computed only once

def _view(client, aid):
    return client.get(f"/api/analyses/{aid}/lowcontrast_view").json()


def _picture(client, aid, key):
    return client.get(f"/api/analyses/{aid}/lowcontrast_view.png?key={key}")


def test_the_close_up_is_computed_once_for_its_markers_and_its_picture(
        client, monkeypatch):
    """The page always asks for the markers and then for the picture they
    name. Both used to render the close-up; now the first keeps what it
    rendered and the second only reads it back."""
    aid = _proposed(client, phantom="ONCE")
    renders = _counted(monkeypatch, client.mod.lowcontrast, "block_view")
    key = _view(client, aid)["key"]
    assert len(renders) == 1
    reads = _counted(monkeypatch, client.mod.store, "get")
    kept = _picture(client, aid, key)
    assert kept.status_code == 200
    assert kept.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert kept.headers["cache-control"] == "private, max-age=86400"
    assert len(renders) == 1, "the picture request rendered the close-up again"
    assert not reads, "the picture request read the whole record again"

    # What was kept is exactly what rendering it afresh gives.
    client.mod._img_cache.clear()
    fresh = _picture(client, aid, key)
    assert len(renders) == 2
    assert fresh.content == kept.content
    assert fresh.headers["cache-control"] == "private, max-age=86400"


def _another_worker(mod, monkeypatch):
    """Stand in for the next gunicorn worker: the same database, and none of
    this process's memory, since the caches are per process."""
    for name in ("_scans", "_regs", "_img_cache"):
        monkeypatch.setattr(mod, name, {})


def test_on_a_server_another_worker_may_answer_and_renders_as_before(
        client, monkeypatch):
    """Under gunicorn each worker has its own cache, and nginx opens a new
    connection for every request, so the picture may be asked of a worker
    that did not answer the markers. That one renders the close-up exactly as
    before this change — the same bytes, still keepable — and the markers
    worker has spent one encode for nothing. So the saving is certain only in
    one process, as run_app.py runs; what is served is the same either way."""
    aid = _proposed(client, phantom="WORKERS")
    renders = _counted(monkeypatch, client.mod.lowcontrast, "block_view")
    encodes = _counted(monkeypatch, client.mod, "_keep_block_png")
    key = _view(client, aid)["key"]
    first = client.mod._img_cache
    kept = first[(aid, "lcview", key)]

    _another_worker(client.mod, monkeypatch)
    answer = _picture(client, aid, key)
    assert answer.status_code == 200
    assert (len(renders), len(encodes)) == (2, 2)
    assert answer.content == kept
    assert answer.headers["cache-control"] == "private, max-age=86400"
    assert list(client.mod._img_cache) == [(aid, "lcview", key)]
    assert first[(aid, "lcview", key)] == kept, \
        "the first worker's copy is untouched, only unused"


def test_every_nudge_renders_its_close_up_once(client, monkeypatch):
    aid = _proposed(client, phantom="NUDGES")
    renders = _counted(monkeypatch, client.mod.lowcontrast, "block_view")
    for n, angle in enumerate((-10.0, -20.0, -30.0), start=1):
        assert client.post(f"/api/analyses/{aid}/lowcontrast_block",
                           json={"angle_deg": angle}).status_code == 200
        before = len(renders)
        key = _view(client, aid)["key"]
        assert _picture(client, aid, key).status_code == 200
        assert len(renders) - before == 1, f"nudge {n}"


def test_a_name_is_only_ever_answered_with_its_own_picture(client, monkeypatch):
    """The name digests what the picture shows, so the bytes kept under it are
    that picture. A name this worker no longer holds — evicted, or kept by
    another worker — still gets today's picture, marked not to be kept, so a
    stale page can never store it under a placement it does not show.

    The synthetic scan's close-up is flat grey at any angle, so the render is
    wrapped to paint the angle into it and the bytes say which placement they
    were made from."""
    real = client.mod.lowcontrast.block_view

    def telltale(ctx, centre, angle, *a, **k):
        view = real(ctx, centre, angle, *a, **k)
        return {**view, "image": np.full_like(view["image"],
                                              (abs(angle) % 90.0) / 90.0)}

    monkeypatch.setattr(client.mod.lowcontrast, "block_view", telltale)
    aid = _proposed(client, phantom="NAMES")
    client.post(f"/api/analyses/{aid}/lowcontrast_block", json={"angle_deg": -30.0})
    old_key = _view(client, aid)["key"]
    old = _picture(client, aid, old_key).content
    client.post(f"/api/analyses/{aid}/lowcontrast_block", json={"angle_deg": -60.0})
    new_key = _view(client, aid)["key"]
    new = _picture(client, aid, new_key)
    assert new_key != old_key and new.content != old
    assert new.headers["cache-control"].startswith("private")

    held = _picture(client, aid, old_key)
    assert held.content == old, "a kept name must give back its own picture"
    assert held.headers["cache-control"].startswith("private")

    client.mod._img_cache.clear()
    for stale in (old_key, "0" * 16, ""):
        answer = _picture(client, aid, stale)
        assert answer.status_code == 200
        assert answer.headers["cache-control"] == "no-store", stale
        assert answer.content == new.content, "today's picture is still sent"


def test_a_record_deleted_elsewhere_is_not_served_from_the_kept_close_up(
        client):
    """The markers request now leaves the picture in this worker's memory,
    ready to be sent without reading the record — which must not make it
    outlive the record. Deleted through the store directly, as another worker
    would, so nothing here has been told."""
    aid = _proposed(client, phantom="GONE")
    key = _view(client, aid)["key"]
    assert [k for k in client.mod._img_cache if k[0] == aid], \
        "the scenario needs the picture kept here"
    client.mod.store.delete(aid)
    assert _picture(client, aid, key).status_code == 404
    assert not [k for k in client.mod._img_cache if k[0] == aid]


def test_a_run_of_nudges_loads_one_more_close_up_not_one_each():
    """Each load is two requests and a render on the server, and a nudge
    takes a fraction of that. Five quick nudges used to start five loads and
    draw four placements that were already gone; now a nudge during a load
    only asks for one more, and only the newest load may draw."""
    js = _app_js()
    place = _fn(js, "placeBlock")
    assert "refreshBlockView()" in place and "loadBlockView(" not in place
    refresh = _fn(js, "refreshBlockView")
    assert "if (S.blockViewBusy) { S.blockViewAgain = true; return; }" in refresh
    assert refresh.index("await loadBlockView(true)") \
        < refresh.index("while (S.blockViewAgain)")
    assert "S.blockViewBusy = false" in refresh[refresh.index("finally"):], \
        "a load that fails must not leave the close-up unable to refresh"

    load = _fn(js, "loadBlockView")
    assert load.index("++S.blockViewGen") < load.index("await api(")
    after_markers = load[load.index("await api("):]
    assert after_markers.index("if (!current()) return;") \
        < after_markers.index("img.src")
    after_picture = load[load.index("img.onload = ok"):]
    assert after_picture.index("if (!current()) return;") \
        < after_picture.index("S.blockView = {")
    assert "S.aid === aid" in load, \
        "a late answer must not be shown under another analysis"


# ---------------------------------- 5. History's verdict notes in the one query

def _two_query_statuses(store, ids, fields):
    """Store.result_statuses as it was before the listing read the statuses
    itself — the second query this change removed, kept here as the answer
    the one-pass listing must give, row for row."""
    out = {}
    if not ids or not fields:
        return out
    paths = ", ".join("?" for _ in fields)
    path_args = [f"$.{test}.{key}" for test, key in fields]
    with store._conn() as c:
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            marks = ",".join("?" for _ in chunk)
            try:
                rows = c.execute(
                    f"SELECT id, json_extract(CASE WHEN json_valid("
                    f"results_json) THEN results_json END, {paths}) AS s"
                    f" FROM analyses WHERE id IN ({marks})"
                    f" AND results_json IS NOT NULL",
                    [*path_args, *chunk]).fetchall()
            except sqlite3.OperationalError:
                return {}
            for row in rows:
                if len(fields) == 1:
                    found = [row["s"]]
                else:
                    try:
                        found = json.loads(row["s"]) if row["s"] else None
                    except (json.JSONDecodeError, TypeError):
                        found = None
                if not isinstance(found, list):
                    continue
                d = {}
                for (test, key), status in zip(fields, found):
                    if isinstance(status, str):
                        d.setdefault(test, {})[key] = status
                out[row["id"]] = d
    return out


def _mixed_history(store):
    """One of every kind of row History meets, by what it should read as."""
    def stored(results, status="pass"):
        aid = store.new_analysis(fake_scan(sha=f"{next(_variant):064d}"),
                                 b"payload", "sig", "1.0.0", "1.0",
                                 labels={"site": "T", "phantom": "MIXED"})
        if results is not None:
            store.update(aid, results=results, status=status, stage="F")
        return aid

    legacy = results_for()
    legacy["geometry"]["field_status"] = "n/a"
    unmeasured = results_for()
    unmeasured["lowcontrast"]["status"] = "not measured"
    several = results_for()
    several["uniformity"]["status"] = "not applicable"
    several["wedge"]["status"] = "not applicable"
    odd = results_for()
    odd["geometry"] = ["not", "a", "test"]
    odd["wedge"]["status"] = 3
    rows = {
        "passes, field edge not in view": stored(results_for()),
        "stored before the split (n/a)": stored(legacy, "n/a"),
        "not measured": stored(unmeasured, "warn"),
        "three tests not applicable": stored(several),
        "odd shapes inside the results": stored(odd, "warn"),
        "no results yet": stored(None),
        "damaged results": stored(results_for()),
        "results that are not an object": stored(results_for()),
    }
    with store._conn() as c:
        c.execute("UPDATE analyses SET results_json=? WHERE id=?",
                  ("{not json", rows["damaged results"]))
        c.execute("UPDATE analyses SET results_json=? WHERE id=?",
                  ("[1, 2]", rows["results that are not an object"]))
    return rows


def test_history_notes_are_exactly_the_ones_the_two_queries_gave(client):
    store = client.mod.store
    rows = _mixed_history(store)
    listed = {r["id"]: r["verdict_notes"]
              for r in client.get("/api/analyses").json()["analyses"]}
    before = _two_query_statuses(store, list(listed), FIELDS)
    for kind, aid in rows.items():
        assert listed[aid] == pipeline.verdict_notes(before.get(aid)), kind

    # The set really does exercise the notes, in both directions.
    assert listed[rows["passes, field edge not in view"]] == [FIELD_NOTE]
    assert listed[rows["stored before the split (n/a)"]] == []
    assert len(listed[rows["three tests not applicable"]]) == 3
    assert listed[rows["damaged results"]] == []


@pytest.mark.parametrize("fields", [FIELDS, [("geometry", "field_status")]],
                         ids=["every status", "one status"])
def test_the_listing_hands_over_the_same_statuses_and_nothing_else_changes(
        client, fields):
    """One status comes back from SQLite bare and several as a JSON array, so
    both shapes are compared. The rest of every row is the listing as it was."""
    store = client.mod.store
    _mixed_history(store)
    one_pass = store.list_all(status_fields=fields)
    before = _two_query_statuses(store, [d["id"] for d in one_pass], fields)
    assert {d["id"]: d["statuses"] for d in one_pass} \
        == {d["id"]: before.get(d["id"]) for d in one_pass}
    assert [{k: v for k, v in d.items() if k != "statuses"} for d in one_pass] \
        == store.list_all()


class _SqliteWithoutJson(sqlite3.Connection):
    """A connection to an SQLite built without its JSON functions."""

    def execute(self, sql, *args):
        if re.search(r"\bjson_\w+\(", sql):
            raise sqlite3.OperationalError("no such function: json_valid")
        return super().execute(sql, *args)


def test_without_sqlites_json_functions_history_lists_everything_unnoted(
        client, monkeypatch):
    """The note is a courtesy, History is not — the same answer the two
    queries gave on such an SQLite, where the second one simply failed."""
    store = client.mod.store
    _mixed_history(store)
    listing = store.list_all()

    def connect():
        c = sqlite3.connect(store.db_path, timeout=15,
                            factory=_SqliteWithoutJson)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(store, "_connect", connect)
    answer = client.get("/api/analyses")
    assert answer.status_code == 200, answer.text[:300]
    rows = answer.json()["analyses"]
    assert [r["id"] for r in rows] == [d["id"] for d in listing]
    assert all(r["verdict_notes"] == [] for r in rows)
    assert _two_query_statuses(store, [d["id"] for d in listing], FIELDS) == {}


def test_a_listing_that_fails_on_its_own_still_fails(tmp_path, monkeypatch):
    """Only the statuses may be given up. A listing that cannot be read at
    all must fail as loudly as it always did, not come back empty."""
    store = Store(str(tmp_path))

    class Broken(sqlite3.Connection):
        def execute(self, sql, *args):
            if "FROM analyses" in sql:
                raise sqlite3.OperationalError("database disk image is malformed")
            return super().execute(sql, *args)

    def connect():
        c = sqlite3.connect(store.db_path, timeout=15, factory=Broken)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(store, "_connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="malformed"):
        store.list_all(status_fields=FIELDS)


def test_history_reads_its_notes_in_the_listing_query_itself(client,
                                                             monkeypatch):
    """The saving: every column listed after the results already walks
    through them, so the second query for the statuses walked every row's
    results a second time. One statement reads the results, and it is the
    listing."""
    store = client.mod.store
    _mixed_history(store)
    statements = []
    real = store._connect

    def traced():
        c = real()
        c.set_trace_callback(statements.append)
        return c

    monkeypatch.setattr(store, "_connect", traced)
    assert client.get("/api/analyses").status_code == 200
    reading = [s for s in statements if "results_json" in s]
    assert len(reading) == 1, reading
    assert "json_extract" in reading[0] and "has_results" in reading[0]
