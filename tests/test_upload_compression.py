"""Sending a scan packed, and storing exactly the scan that was chosen.

The constraint behind this one is the same as behind the progress bar: "Consider
that the user will work with very low connection speed." At 512 kbit/s the
transfer is nearly all of an operator's waiting, and most detectors write their
pixels uncompressed. Measured with gzip on the 38 real scans, every one of them
round-tripping byte for byte:

    Fuji (field site)   7.5 MB -> 2.9 MB (1.1 MB over-exposed)   118 s -> 45 s
    Carestream         15.1 MB -> 7.1 MB on average               236 s -> 111 s
    Philips             7.4 MB -> 7.4 MB — compressed inside the file already

Field projects meet a different detector every time, so the browser decides per
file: it packs the first megabyte as a probe and sends the file as it is unless
that clearly shrinks, and it keeps the packed file only if the whole of it
clearly shrinks too. The worst case for an unknown detector is no gain — never
a slower upload, a broken one, or a different file.

That last part is what these tests are mostly about. Packing is only acceptable
because nothing downstream can tell it happened: the server unpacks, proves the
result identical to the file on the operator's disk (gzip's CRC32, one member
and nothing after it, the original size, and the SHA-256 the browser took
before packing), and only then carries on exactly as for a file sent as it is —
same stored bytes, same recorded fingerprint, same duplicate check, same
analysis. Anything that disagrees is refused with nothing stored.

The browser half has no JavaScript runtime here, so it is checked structurally,
like the rest of the page code.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import time
import zlib

import pytest

import field_scans
import hq_manifest
from test_unusable_exposures import SYNTHETIC, _png16

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(REPO, "phantom_qa", "webapp", "static")

#: Kept well below the 16-bit ceiling: _png16 clips, so values above it all
#: land on 65535 and every "different" scan comes out byte-identical.
_variant = iter(range(20_000, 29_000))


def _make_client(tmp_path, monkeypatch, **env):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch, **env)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    c.audit_log = os.path.join(str(tmp_path), "logs", "audit.log")
    return c


@pytest.fixture()
def client(tmp_path, monkeypatch):
    return _make_client(tmp_path, monkeypatch)


@pytest.fixture()
def small_cap_client(tmp_path, monkeypatch):
    """An upload cap of 1 MB, so a bomb past it costs a test almost nothing."""
    return _make_client(tmp_path, monkeypatch, PHANTOMQA_MAX_UPLOAD_MB="1")


def _scan_bytes():
    """A distinct usable scan each call."""
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    return _png16(pixels)


def _pack(data: bytes, level: int = 6) -> bytes:
    """One gzip member, as the browser's CompressionStream("gzip") makes."""
    return gzip.compress(data, compresslevel=level, mtime=0)


def _send(client, data, name="scan.png", **labels):
    """The file as it is — the upload as it has always been."""
    return client.post("/api/analyses", files={"file": (name, data)},
                       data={"site": "T", "phantom": "PACK", **labels})


def _send_packed(client, original, name="scan.png", *, packed=None,
                 sha=None, size=None, encoding="gzip", **fields):
    """A packed upload in the shape the page sends it."""
    packed = _pack(original) if packed is None else packed
    form = {"site": "T", "phantom": "PACK", "encoding": encoding,
            "original_sha256": hashlib.sha256(original).hexdigest()
            if sha is None else sha,
            "original_size": str(len(original) if size is None else size),
            "original_name": name, **fields}
    return client.post("/api/analyses",
                       files={"file": (name + ".gz", packed)}, data=form)


def _bomb(megabytes: int) -> bytes:
    """Zeros packed a megabyte at a time, so the test never holds the bomb's
    unpacked size itself: 50 MB pack to about 50 kB."""
    packer = zlib.compressobj(1, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    zeros = bytes(1024 * 1024)
    parts = [packer.compress(zeros) for _ in range(megabytes)]
    return b"".join(parts) + packer.flush()


def _nothing_stored(client):
    store = client.mod.store
    uploads = os.path.join(store.root, "data", "uploads")
    return store.list_all() == [] and os.listdir(uploads) == []


def _audit_lines(client, event="upload"):
    with open(client.audit_log, encoding="utf-8") as f:
        return [l for l in f.read().splitlines() if f"event={event} " in l]


def _stored_bytes(client, aid):
    with open(client.mod.store.upload_path(aid), "rb") as f:
        return f.read()


# ------------------------------------------------- the packed file is the file

def test_a_packed_scan_is_stored_as_the_original_byte_for_byte(client):
    """The stored file is what every later integrity check, re-run and
    comparison reads. It has to be the file the operator chose, not the
    packed copy that happened to cross the link."""
    original = _scan_bytes()
    answer = _send_packed(client, original, name="003_0000.png")
    assert answer.status_code == 200, answer.text
    aid = answer.json()["analyses"][0]["id"]

    rec = client.mod.store.get(aid)
    assert _stored_bytes(client, aid) == original
    assert rec["sha256"] == hashlib.sha256(original).hexdigest(), \
        "the recorded fingerprint must be the original's, not the packed one's"
    assert rec["source_name"] == "003_0000.png", \
        "the record carries the original's name, not the packed part's"


def test_a_packed_scan_is_analysed_exactly_like_the_file_sent_as_it_is(client):
    """Same bytes in, same registration and same exposure verdict out.

    Both arrive in one app — the second deliberately, as a separate record —
    so nothing but the way they travelled differs."""
    original = _scan_bytes()
    plain = _send(client, original).json()["analyses"][0]
    packed = _send_packed(client, original,
                          allow_duplicate="true").json()["analyses"][0]
    assert plain["registered"] and packed["registered"]
    assert packed["registration"]["summary"] == plain["registration"]["summary"]
    assert packed["quality"] == plain["quality"]
    assert _stored_bytes(client, packed["id"]) == \
        _stored_bytes(client, plain["id"])


def test_a_file_sent_as_it_is_is_unchanged(client):
    """Every browser without CompressionStream, every plain-http site and
    every file that does not shrink still travels this way."""
    for encoding in (None, "identity"):
        original = _scan_bytes()
        form = {"site": "T"} if encoding is None else \
            {"site": "T", "encoding": encoding}
        answer = client.post("/api/analyses",
                             files={"file": ("plain.png", original)},
                             data=form)
        assert answer.status_code == 200, answer.text
        aid = answer.json()["analyses"][0]["id"]
        assert _stored_bytes(client, aid) == original
        assert client.mod.store.get(aid)["sha256"] == \
            hashlib.sha256(original).hexdigest()


def test_the_transfer_is_written_into_the_audit_log(client):
    """When an upload was slow, the audit line is where that is explained:
    what crossed the link, how big the file really was, and how it went."""
    original = _scan_bytes()
    packed = _pack(original)
    aid = _send_packed(client, original, packed=packed
                       ).json()["analyses"][0]["id"]
    line = next(l for l in _audit_lines(client) if f"analysis={aid}" in l)
    detail = json.loads(line.split("detail=", 1)[1])
    assert detail["encoding"] == "gzip"
    assert detail["sent_bytes"] == len(packed)
    assert detail["bytes"] == len(original)

    aid2 = _send(client, _scan_bytes()).json()["analyses"][0]["id"]
    line = next(l for l in _audit_lines(client) if f"analysis={aid2}" in l)
    detail = json.loads(line.split("detail=", 1)[1])
    assert detail["encoding"] == "identity"
    assert detail["sent_bytes"] == detail["bytes"]


# ------------------------------------------ the same file, whichever way it came

def test_a_packed_re_send_of_a_stored_file_is_the_usual_duplicate(client):
    """The duplicate check reads the unpacked bytes, so packing cannot make a
    file look new — and the refusal is the one the page already handles."""
    original = _scan_bytes()
    aid = _send(client, original).json()["analyses"][0]["id"]
    again = _send_packed(client, original)
    assert again.status_code == 409, again.text
    assert [d["id"] for d in again.json()["duplicate_of"]] == [aid]
    assert len(client.mod.store.list_all()) == 1


def test_a_file_first_sent_packed_is_recognised_when_sent_as_it_is(client):
    original = _scan_bytes()
    aid = _send_packed(client, original).json()["analyses"][0]["id"]
    assert _send(client, original).status_code == 409
    check = client.post("/api/upload_check", json={
        "sha256": [hashlib.sha256(original).hexdigest()]}).json()
    assert [d["id"] for d in check["duplicates"][0]["duplicate_of"]] == [aid], \
        "the pre-check before sending must find a file that arrived packed"


# ------------------------------------------------------- refused, nothing kept

def _assert_damaged(client, answer):
    assert answer.status_code == 400, answer.text
    assert answer.json()["detail"] == client.mod.DAMAGED_UPLOAD_MESSAGE
    # The flag the page acts on (it stops packing); the English is for people.
    assert answer.json().get("damaged_transfer") is True
    assert _nothing_stored(client)
    damaged = [l for l in _audit_lines(client) if "outcome=damaged" in l]
    assert damaged, "a refused transfer must leave a line in the audit log"
    return json.loads(damaged[-1].split("detail=", 1)[1])


def test_the_refusal_says_plainly_what_happened_and_what_to_do(client):
    """The operator is at an X-ray unit, not a terminal. "incorrect data
    check" tells them nothing; that nothing was kept, and that sending it
    again is the remedy, is all they need."""
    msg = client.mod.DAMAGED_UPLOAD_MESSAGE
    assert "damaged on the way" in msg
    assert "nothing was stored" in msg
    assert "send it again" in msg


def test_a_wrong_fingerprint_is_refused_and_nothing_is_stored(client):
    """The fingerprint was taken from the file on the operator's disk. If what
    unpacked here is not that file, storing it would store a different scan
    under that scan's name."""
    original = _scan_bytes()
    other = hashlib.sha256(b"a different file").hexdigest()
    detail = _assert_damaged(client, _send_packed(client, original, sha=other))
    assert "SHA-256" in detail["error"]
    assert detail["original_sha256"] == other
    assert detail["sent_bytes"] > 0 and detail["original_bytes"] == len(original)


@pytest.mark.parametrize("delta", [-1, +1, -1000])
def test_a_wrong_size_is_refused(client, delta):
    original = _scan_bytes()
    detail = _assert_damaged(
        client, _send_packed(client, original, size=len(original) + delta))
    assert "bytes" in detail["error"]


def _flip(data: bytes, where: int) -> bytes:
    b = bytearray(data)
    b[where] ^= 0x5A
    return bytes(b)


_CORRUPTIONS = {
    # a byte of the compressed body: deflate itself or the CRC32 catches it
    "flipped byte in the body": lambda p, o: _flip(p, len(p) // 2),
    # the trailer is the CRC32 and the length; damaging it must not pass
    "flipped byte in the CRC32": lambda p, o: _flip(p, len(p) - 6),
    "flipped byte in the length": lambda p, o: _flip(p, len(p) - 2),
    # a connection that dropped: the stream never reaches its end
    "truncated by the trailer": lambda p, o: p[:-8],
    "truncated by half": lambda p, o: p[: len(p) // 2],
    "only the header": lambda p, o: p[:10],
    # something appended after a complete, correct file
    "trailing garbage": lambda p, o: p + b"\x00garbage",
    "trailing newline": lambda p, o: p + b"\n",
    # two members whose contents together ARE the original, fingerprint and
    # size both right: still refused, because "the file plus whatever was
    # appended" is not a form the page ever sends
    "two members": lambda p, o: _pack(o[:1000]) + _pack(o[1000:]),
    # not gzip at all
    "a zlib stream": lambda p, o: zlib.compress(o),
    "the original unpacked": lambda p, o: o,
    "empty": lambda p, o: b"",
}


@pytest.mark.parametrize("damage", sorted(_CORRUPTIONS))
def test_a_damaged_packed_file_is_refused_and_nothing_is_stored(client, damage):
    original = _scan_bytes()
    packed = _CORRUPTIONS[damage](_pack(original), original)
    _assert_damaged(client, _send_packed(client, original, packed=packed))


def test_a_refused_file_can_be_sent_again_straight_away(client):
    """"Please send it again" has to be true: a refusal leaves nothing behind
    that would turn the second attempt into a duplicate."""
    original = _scan_bytes()
    _send_packed(client, original, packed=_pack(original)[:-8])
    again = _send_packed(client, original)
    assert again.status_code == 200, again.text


def test_a_damaged_file_can_be_sent_again_as_it_is(client):
    """What the page does after a damaged refusal: the next attempt goes
    unpacked. That must be accepted like any first upload — the refusal left
    nothing behind to collide with."""
    original = _scan_bytes()
    _assert_damaged(client, _send_packed(client, original,
                                         packed=_flip(_pack(original), 40)))
    again = _send(client, original)
    assert again.status_code == 200, again.text
    aid = again.json()["analyses"][0]["id"]
    assert _stored_bytes(client, aid) == original


def test_only_a_damaged_transfer_tells_the_page_to_stop_packing(client):
    """The page stops packing for the rest of the session on the damaged
    flag. A refusal packing had nothing to do with must not carry it: a file
    that unpacked perfectly but is not a scan would fail just the same sent
    as it is, and turning packing off for it would make every later upload in
    the session slower for nothing."""
    not_a_scan = b"these bytes unpack perfectly but are not an image" * 50
    answer = _send_packed(client, not_a_scan)
    assert answer.status_code == 400, answer.text
    assert "Could not read file" in answer.json()["detail"]
    assert "damaged_transfer" not in answer.json()

    answer = _send_packed(client, _scan_bytes(), encoding="br")
    assert answer.status_code == 400
    assert "damaged_transfer" not in answer.json()

    answer = _send_packed(client, _scan_bytes(), sha="")
    assert answer.status_code == 400
    assert "damaged_transfer" not in answer.json()


def test_a_bomb_does_not_tell_the_page_to_stop_packing(small_cap_client):
    """Past the cap is past the cap however the file travels — sending it as
    it is would be refused on its size too — so it is no reason to stop
    packing."""
    answer = _send_packed(small_cap_client, b"", name="bomb.dcm",
                          packed=_bomb(50), sha="0" * 64,
                          size=50 * 1024 * 1024)
    assert answer.status_code == 413
    assert "damaged_transfer" not in answer.json()


# --------------------------------------------------------- decompression bombs

def test_a_bomb_declaring_its_size_is_refused_before_any_work(small_cap_client):
    """50 MB of zeros packs to about 50 kB — far under the body cap, which is
    why the body cap alone cannot stop it."""
    c = small_cap_client
    answer = _send_packed(c, b"", name="bomb.dcm", packed=_bomb(50),
                          sha="0" * 64, size=50 * 1024 * 1024)
    assert answer.status_code == 413, answer.text
    assert "1 MB" in answer.json()["detail"]
    assert _nothing_stored(c)
    assert any("outcome=rejected" in l and '"encoding":"gzip"' in l
               for l in _audit_lines(c))


def test_a_bomb_lying_about_its_size_is_stopped_while_unpacking(
        small_cap_client):
    """It declares a size under the cap, so it passes the first check. The
    output is measured while it is produced and stopped one step past the
    declared size — refused as a file that is not what it said it was."""
    c = small_cap_client
    started = time.perf_counter()
    answer = _send_packed(c, b"", name="bomb.dcm", packed=_bomb(50),
                          sha="0" * 64, size=500_000)
    assert time.perf_counter() - started < 5
    assert answer.status_code == 400, answer.text
    assert answer.json()["detail"] == c.mod.DAMAGED_UPLOAD_MESSAGE
    assert _nothing_stored(c)


def test_the_unpacker_never_produces_more_than_the_cap():
    """The cap is enforced on the output while it is produced — not on a
    result already sitting in memory — so a bomb costs at most one step past
    it. Checked on the helper directly, with the declared size allowed right
    up to the cap, by measuring what it allocated."""
    import tracemalloc
    from phantom_qa import ingest
    cap = 1024 * 1024
    bomb = _bomb(200)
    tracemalloc.start()
    try:
        with pytest.raises(ingest.OversizedTransfer):
            ingest.unpack_gzip(bomb, original_size=cap,
                               original_sha256="0" * 64, max_bytes=cap)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 8 * cap, (
        f"{peak / cap:.0f} MB were held unpacking a bomb against a 1 MB cap")


def test_the_unpacker_gives_up_when_its_time_runs_out():
    """A backstop: no real scan comes near it, but a stream still going after
    the budget is not a scan, and the worker is needed by other operators."""
    from phantom_qa import ingest
    data = os.urandom(3 * 1024 * 1024)
    with pytest.raises(ingest.OversizedTransfer):
        ingest.unpack_gzip(_pack(data, 1), original_size=len(data),
                           original_sha256=hashlib.sha256(data).hexdigest(),
                           max_bytes=len(data), time_budget_s=-1)


def test_the_body_cap_still_applies_to_a_packed_upload(small_cap_client):
    """The body cap is untouched: a packed body larger than the cap is
    refused on its own size, before any unpacking."""
    c = small_cap_client
    body = os.urandom(2 * 1024 * 1024)
    answer = _send_packed(c, body, packed=body)
    assert answer.status_code == 413
    assert _nothing_stored(c)


# ----------------------------------------------- what the server will not guess

@pytest.mark.parametrize("encoding", ["br", "deflate", "zip", "gzip2",
                                      "x-gzip", "compress"])
def test_an_unknown_encoding_is_refused(client, encoding):
    """Guessing would mean storing bytes the server did not know how to read
    back — something other than the scan, under the scan's name."""
    original = _scan_bytes()
    answer = _send_packed(client, original, encoding=encoding)
    assert answer.status_code == 400, answer.text
    assert "encoding" in answer.json()["detail"].lower()
    assert _nothing_stored(client)
    assert any("unknown encoding" in l for l in _audit_lines(client))


@pytest.mark.parametrize("missing", [
    {"sha": ""}, {"sha": "not-a-hash"}, {"sha": "z" * 64}, {"size": ""},
    {"size": "-5"}, {"size": "7.5 MB"},
    # Sizes str.isdigit() passes but no browser writes. The first two once
    # escaped int() as a 500 with no audit line: "²" is a digit to isdigit()
    # and not to int(), and int() refuses more than 4300 digits.
    {"size": "²"}, {"size": "9" * 5000},
    {"size": "١٢"},          # Arabic-Indic digits
    {"size": "１２"},          # full-width digits
    {"size": "9" * 16},                # past a petabyte: not a measured size
])
def test_a_packed_file_must_say_what_it_packs(client, missing):
    """The decision for a browser that could not fingerprint the file (plain
    http, where crypto.subtle does not exist): it does not pack. CRC32 and the
    length alone would catch a damaged transfer but not a wrong file, so a
    packed body without the fingerprint did not come from the page and is
    refused rather than half-verified — and like every refusal, it is
    written into the audit log."""
    original = _scan_bytes()
    answer = _send_packed(client, original, **missing)
    assert answer.status_code == 400, answer.text
    assert "SHA-256" in answer.json()["detail"]
    assert _nothing_stored(client)
    assert any("outcome=rejected" in l and "fingerprint" in l
               for l in _audit_lines(client)), \
        "a malformed packed upload must leave a line in the audit log"


def test_the_original_name_decides_how_the_file_is_read(client):
    """A .png is read as an image and a .dcm as DICOM, so the name the bytes
    are read under must be the original's even when the page leaves it out —
    the packed part's own name then stands in, less its .gz."""
    original = _scan_bytes()
    answer = client.post(
        "/api/analyses", files={"file": ("x-ray.png.gz", _pack(original))},
        data={"encoding": "gzip",
              "original_sha256": hashlib.sha256(original).hexdigest(),
              "original_size": str(len(original))})
    assert answer.status_code == 200, answer.text
    assert answer.json()["analyses"][0]["source_name"] == "x-ray.png"


# ------------------------------------------------------------- the real scans

def _real_scans():
    """Every reference scan, and the field Fuji exposures.

    The field scans only CHECK behaviour here — that a packed copy unpacks to
    the identical file — which sets nothing; see field_scans.py."""
    out = [pytest.param(hq_manifest.scan_path(e), id=e["key"])
           for e in hq_manifest.INVENTORY]
    out += [pytest.param(field_scans.path_of(e), id=e["key"])
            for e in field_scans.INVENTORY]
    return out


@pytest.mark.parametrize("path", _real_scans())
def test_every_real_scan_unpacks_to_the_identical_file(path):
    """All three detectors, both transfer syntaxes, every exposure we have.

    A unit test of the unpacker, not an analysis per scan: packed at the
    fastest level, since the level changes only the size and never what
    unpacks, and the helper is what decides whether an upload is stored."""
    if not os.path.exists(path):
        pytest.skip("scan not present")
    from phantom_qa import ingest
    with open(path, "rb") as f:
        original = f.read()
    out = ingest.unpack_gzip(
        _pack(original, 1), original_size=len(original),
        original_sha256=hashlib.sha256(original).hexdigest(),
        max_bytes=200 * 1024 * 1024)
    assert out == original


def _page_constants():
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        js = f.read()
    probe = int(re.search(r"const PACK_PROBE_BYTES = (\d+);", js).group(1))
    ratio = float(re.search(r"const PACK_WORTH_IT = ([\d.]+);", js).group(1))
    return probe, ratio


def test_the_probe_tells_the_detectors_apart_on_every_real_scan():
    """The probe is only worth having if the first megabyte predicts the rest.

    On every scan we hold, with the page's own thresholds: the Philips, which
    compresses inside the file, is recognised from its first megabyte and
    sent at once; every Carestream and Fuji scan passes the probe."""
    probe_bytes, ratio = _page_constants()
    assert probe_bytes == 1024 * 1024 and ratio == 0.9
    seen = {"philips": 0, "carestream": 0, "fuji": 0}
    entries = [(hq_manifest.scan_path(e), e["group"])
               for e in hq_manifest.INVENTORY]
    entries += [(field_scans.path_of(e), "fuji")
                for e in field_scans.INVENTORY]
    for path, group in entries:
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            head = f.read(probe_bytes)
        packs = len(_pack(head)) <= ratio * len(head)
        assert packs == (group != "philips"), (
            f"{os.path.basename(path)} ({group}): the probe decided "
            f"{'to pack' if packs else 'not to pack'}")
        seen[group] += 1
    if not any(seen.values()):
        pytest.skip("no real scans present")


def _real_upload(tmp_path, monkeypatch, path):
    if not os.path.exists(path):
        pytest.skip("scan not present")
    with open(path, "rb") as f:
        original = f.read()
    c = _make_client(tmp_path, monkeypatch)
    answer = _send_packed(c, original, name=os.path.basename(path))
    assert answer.status_code == 200, answer.text
    entry = answer.json()["analyses"][0]
    assert entry["registered"], entry.get("error")
    assert _stored_bytes(c, entry["id"]) == original
    return c, entry, original


@pytest.mark.parametrize("key", ["PH0730_03", "CS000001"])
def test_a_packed_reference_scan_goes_the_whole_way(tmp_path, monkeypatch,
                                                    key):
    """Through HTTP, the threadpool and the store, one scan per detector: the
    record carries the benchmark's fingerprint and the registration lands
    where the benchmark pinned it."""
    entry = next(e for e in hq_manifest.INVENTORY if e["key"] == key)
    c, up, _ = _real_upload(tmp_path, monkeypatch, hq_manifest.scan_path(entry))
    golden = hq_manifest.load_manifest()["scans"][key]
    assert c.mod.store.get(up["id"])["sha256"] == golden["sha256"]
    summary = up["registration"]["summary"]
    assert abs(summary["rotation_deg"] - golden["registration"]["rotation_deg"]) \
        <= hq_manifest.TOL["rotation_deg"]
    assert abs(summary["mm_per_px"] / golden["registration"]["mm_per_px"] - 1) \
        <= hq_manifest.TOL["mm_per_px_rel"]
    assert up["quality"]["verdict"] == golden["quality"]["verdict"]


def test_a_packed_field_fuji_scan_goes_the_whole_way(tmp_path, monkeypatch):
    """The detector the field site actually uses, and the one packing helps
    most. Checked for behaviour only: stored identical, under its own
    fingerprint, and registered."""
    entry = next(e for e in field_scans.INVENTORY if e["key"] == "F002_usable")
    c, up, _ = _real_upload(tmp_path, monkeypatch, field_scans.path_of(entry))
    assert c.mod.store.get(up["id"])["sha256"] == entry["sha256"]


# ------------------------------------------------------------ the browser half

def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def app_js():
    return _read("app.js")


def _between(js, start, end):
    i = js.index(start)
    return js[i:js.index(end, i + len(start))]


def _upload_file(js):
    return _between(js, "async function uploadFile(",
                    "\nasync function openAnalysis(")


def _pack_for_upload(js):
    return _between(js, "async function packForUpload(",
                    "\nasync function uploadFile(")


def test_packing_uses_the_browsers_own_gzip_and_no_library(app_js):
    """A library would be one more download over the link it is meant to
    spare, and the page has no build step to bundle one."""
    body = _between(app_js, "async function gzipBlob(", "\n/* What to send")
    assert 'new CompressionStream("gzip")' in body
    assert ".stream()" in body
    index = _read("index.html")
    assert re.findall(r"<script[^>]*src=", index) == ['<script src='], \
        "a script was added to the page"


def test_a_browser_without_compression_sends_the_file_as_it_is(app_js):
    """CompressionStream is recent. A browser without it must upload exactly
    as before, not fail."""
    detect = _between(app_js, "function canPack(", "\n/* One blob gzipped")
    assert 'typeof CompressionStream === "function"' in detect
    body = _pack_for_upload(app_js)
    guard = body.index("!canPack()")
    assert "return asIs" in body[guard:guard + 40]
    assert guard < body.index("gzipBlob("), \
        "the feature test must come before any packing"
    catch = body[body.index("catch (e)"):]
    assert "return asIs" in catch, \
        "a packing failure must fall back to the file as it is"


def test_no_fingerprint_means_no_packing(app_js):
    """Plain http has no crypto.subtle, so no SHA-256. The decision: send the
    file as it is rather than packed with only CRC32 and length to vouch for
    it — today's guarantee, kept, instead of a weaker new one."""
    body = _pack_for_upload(app_js)
    assert "!sha" in body
    assert body.index("!sha") < body.index("gzipBlob(")


def test_a_zip_is_sent_as_it_is_without_probing(app_js):
    """A CD export is compressed already; even the probe would be wasted."""
    body = _pack_for_upload(app_js)
    zip_test = body.index(r"/\.zip$/i.test(file.name")
    assert "return asIs" in body[zip_test:zip_test + 80]
    assert zip_test < body.index("gzipBlob(")


def test_the_first_megabyte_decides_before_the_whole_file_is_packed(app_js):
    """One megabyte packs in a fraction of a second on an old laptop; the
    Philips, which gains nothing, then costs almost no time at all."""
    assert "const PACK_PROBE_BYTES = 1048576;" in app_js
    assert "const PACK_WORTH_IT = 0.9;" in app_js
    body = _pack_for_upload(app_js)
    probe = body.index("file.slice(0, PACK_PROBE_BYTES)")
    probe_rule = body.index("probePacked.size > PACK_WORTH_IT * probe.size")
    whole = body.index("gzipBlob(file,")
    whole_rule = body.index("whole.size > PACK_WORTH_IT * file.size")
    assert probe < probe_rule < whole < whole_rule, \
        "probe, then its rule, then the whole file, then the same rule again"
    assert "return asIs" in body[probe_rule:probe_rule + 80], \
        "a probe that does not shrink must send the file straight away"
    assert "return asIs" in body[whole_rule:whole_rule + 80], \
        "a whole file that does not shrink enough must be sent as it is"


def test_the_fingerprint_is_taken_once_and_used_twice(app_js):
    """The digest of 15 MB is not free on an old laptop. The pre-check and
    the packed upload both need it, so it is taken once and handed on."""
    body = _upload_file(app_js)
    assert body.count("fileSha256(") == 1
    assert "findExistingCopy(file, sha)" in body
    assert "packForUpload(" in body and "file, sha, job" in body
    check = _between(app_js, "async function findExistingCopy(",
                     "\n/* ---- Packing")
    assert "sha === undefined" in check, \
        "the check must use the digest it is given rather than hash again"


def test_the_packed_upload_carries_the_originals_identity(app_js):
    body = _upload_file(app_js)
    packed = body[body.index("if (sending.packed) {"):body.index("} else {")]
    for field in ('fd.append("encoding", "gzip")',
                  'fd.append("original_sha256", sha)',
                  'fd.append("original_size", String(file.size))',
                  'fd.append("original_name", file.name)'):
        assert field in packed, f"the packed upload does not send {field}"
    assert "sending.blob" in packed
    plain = body[body.index("} else {"):]
    assert 'fd.append("file", file)' in plain[:80], \
        "a file that is not packed must be sent exactly as before"


def test_the_pre_check_still_comes_before_any_transfer_or_packing(app_js):
    """Otherwise the operator waits for the packing only to be told the file
    was already here — or, worse, pays for the transfer as well."""
    body = _upload_file(app_js)
    assert body.index("findExistingCopy(") < body.index("packForUpload(") \
        < body.index("uploadWithProgress(")


def test_the_upload_still_goes_through_the_path_that_reports_progress(app_js):
    """Packed or not, one request, sent by the one sender that can say how far
    it has got."""
    body = _upload_file(app_js)
    assert body.count("uploadWithProgress(") == 1
    assert "fetch(" not in body


def test_preparing_is_shown_as_its_own_phase(app_js):
    """A few seconds of packing on an old laptop is a still screen unless it
    says what it is doing — and a still screen is what got uploads reloaded."""
    prep = _between(app_js, "function showPreparing(",
                    "\n/* The upload's own progress")
    assert "Preparing the file…" in prep
    assert "nothing is sent yet" in prep
    assert "setTimeout" not in prep, "the phase must not clear itself"
    body = _upload_file(app_js)
    assert "showPreparing(done, total)" in body
    pack = _pack_for_upload(app_js)
    assert pack.index("onProgress(0, file.size)") > pack.index("!canPack()"), \
        "Preparing must only appear when packing will actually be tried"


def test_the_progress_is_counted_on_the_bytes_actually_sent(app_js):
    """Time left and percentage come from what crosses the link; the original
    size is said beside them so a 2.2 MB transfer does not look like the
    wrong file."""
    show = _between(app_js, "function showUploadProgress(",
                    "\nfunction hideUploadProgress(")
    assert "packedFrom" in show and "packed from ${fileSize(packedFrom)}" in show
    assert "timeRemaining(loaded, total, startedAt)" in show
    body = _upload_file(app_js)
    assert "showUploadProgress(0, sending.blob.size, startedAt, packedFrom)" \
        in body, "the first reading must be the size that will be sent"
    assert "const packedFrom = sending.packed ? file.size : 0;" in body


def test_cancel_works_while_the_file_is_being_packed(app_js):
    """Pressing Cancel while preparing must stop the packing and send nothing
    — and be reported as the operator's choice, like any other Cancel."""
    cancel = _between(app_js, "function cancelUpload(", "\n/* The SHA-256")
    assert "S.packJob" in cancel
    assert "job.cancelled = true" in cancel
    assert "job.reader.cancel()" in cancel
    assert "S.uploadXhr.abort()" in cancel, "cancelling a transfer still works"

    gz = _between(app_js, "async function gzipBlob(", "\n/* What to send")
    assert "job.reader = reader" in gz, "Cancel needs the reader to stop"
    assert "job.cancelled" in gz

    body = _upload_file(app_js)
    after_pack = body[body.index("packForUpload("):]
    flag = after_pack.index("if (job.cancelled)")
    assert flag < after_pack.index("new FormData()") \
        < after_pack.index("uploadWithProgress("), \
        "a cancelled packing must be seen before anything is sent"
    thrown = after_pack[flag:after_pack.index("new FormData()")]
    assert "err.cancelled = true" in thrown and "throw err" in thrown, \
        "it must reach the same 'cancelled' branch as an aborted transfer"
    assert "S.packJob = job" in body
    hide = _between(app_js, "function hideUploadProgress(",
                    "\nfunction cancelUpload(")
    assert "S.packJob = null" in hide


def test_the_cancel_button_is_live_while_preparing(app_js):
    prep = _between(app_js, "function showPreparing(",
                    "\n/* The upload's own progress")
    assert "cancel.disabled = false" in prep


def test_a_duplicate_found_after_sending_reuses_the_fingerprint(app_js):
    """The second attempt after the duplicate dialog must not hash again."""
    body = _upload_file(app_js)
    assert "uploadFile(file, { allowDuplicate: true, sha })" in body
    assert "opts.sha !== undefined ? opts.sha" in body


def test_after_a_damaged_refusal_the_file_is_sent_as_it_is(app_js):
    """The server cannot tell damage on the link from this browser's own
    packing going wrong, and the second would go wrong on every attempt: an
    operator pressing Upload again, as told, would be refused forever from a
    browser that uploaded fine before packing existed. So a damaged refusal
    turns packing off until the page is reloaded, and the next attempt is
    exactly today's upload. Nothing else turns it off, and nothing is re-sent
    behind the operator's back."""
    sender = _between(app_js, "function uploadWithProgress(",
                      "\n/* Seconds remaining")
    assert "if (body && body.damaged_transfer) err.damagedTransfer = true;" \
        in sender, "the page must read the server's flag, not its English"

    assert "sendUnpacked: false," in _between(app_js, "const S = {", "\n};")
    assert app_js.count("S.sendUnpacked = true") == 1, \
        "only the damaged refusal may turn packing off"

    body = _upload_file(app_js)
    catch = body[body.index("} catch (e) {"):]
    branch = catch[catch.index("if (e.damagedTransfer) {"):]
    branch = branch[:branch.index("\n    }\n")]
    assert "S.sendUnpacked = true" in branch
    assert "Upload failed: " in branch and "unpacked" in branch, \
        "the operator is told the next try goes unpacked, and slower"
    assert '$("#btn-upload").disabled = false' in branch
    assert "uploadFile(" not in branch and "uploadWithProgress(" not in branch, \
        "the whole file again on a slow link is the operator's call"
    assert catch.index("if (e.damagedTransfer) {") \
        < catch.index('status("Upload failed: " + e.message, true);')

    pack = _pack_for_upload(app_js)
    guard = pack.index("if (S.sendUnpacked) return asIs;")
    assert guard < pack.index("gzipBlob("), \
        "the flag must stop packing before any of it is done"
    assert guard < pack.index("onProgress(0, file.size)"), \
        "no Preparing phase for a file that will not be packed"
