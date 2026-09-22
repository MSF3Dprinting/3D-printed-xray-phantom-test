"""Being told a file is already here — before paying to send it.

From the first field test: "Different scans identifies as same... See the field
testing folder, they are each different, yet we get warning during upload."

The audit log settled that one. Seven weeks, eight duplicate refusals, every
single one byte-identical to a record that already existed; and three file
names that carried genuinely different files on different days, each accepted
without a murmur. The check has never once been wrong.

What was wrong was everything around it. The operator paid two minutes of a
512 kbit/s link to be told the file was already there, and the refusal named
neither which record held it nor what state that record was in — so the only
way to find out was to rename the file and send it again, which is exactly
what the log shows happening. Two changes follow: ask before sending, and
answer the question the operator actually has.

A hash cannot see a scan re-exported from the archive under a new wrapper, so
the same exposure can still arrive twice as two different files. That one is
remarked on, never refused: a repeated UID can come from a misconfigured
detector, and blocking real work to prevent a bookkeeping error is the wrong
trade.
"""

import hashlib
import io

import numpy as np
import pytest

from test_authorization import ADMIN_PW
from test_unusable_exposures import SYNTHETIC, _png16

#: Kept well below the 16-bit ceiling: _png16 clips, so values above it all
#: land on 65535 and every "different" scan comes out byte-identical.
_variant = iter(range(1_000, 9_000))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _scan_bytes():
    """A distinct usable scan each call."""
    pixels = SYNTHETIC["saturated"]()
    pixels[0, 0] = next(_variant)
    return _png16(pixels)


def _upload(client, data, name="scan.png", **labels):
    return client.post("/api/analyses",
                       files={"file": (name, data)},
                       data={"site": "T", "phantom": "DUP", **labels})


# ------------------------------------------------- asking before sending

def test_a_file_never_seen_before_is_not_reported(client):
    answer = client.post("/api/upload_check",
                         json={"sha256": [hashlib.sha256(b"nothing").hexdigest()]})
    assert answer.status_code == 200, answer.text
    assert answer.json()["duplicates"] == []


def test_a_file_already_here_is_reported_before_it_is_sent(client):
    """The whole point: the answer arrives for 100 bytes, not 7.5 MB."""
    data = _scan_bytes()
    aid = _upload(client, data).json()["analyses"][0]["id"]

    answer = client.post(
        "/api/upload_check",
        json={"sha256": [hashlib.sha256(data).hexdigest()]})
    dupes = answer.json()["duplicates"]
    assert len(dupes) == 1
    assert [d["id"] for d in dupes[0]["duplicate_of"]] == [aid]


def test_the_answer_says_what_became_of_the_record(client):
    """"Already analysed" was never the operator's question.

    Theirs is "is what you have what I meant to send, and what happened to
    it" — so the reply carries both sides: when it arrived, when the exposure
    was taken, who took it, which phantom, how far it got, and how big the
    stored file is."""
    data = _scan_bytes()
    _upload(client, data, operator="Stacy", phantom="MSF-07")

    side = client.post("/api/upload_check",
                       json={"sha256": [hashlib.sha256(data).hexdigest()]}
                       ).json()["duplicates"][0]["duplicate_of"][0]
    for field in ("id", "created_at", "acquired_at", "source_name", "site",
                  "phantom", "operator", "stage", "status",
                  "validation_status", "is_baseline", "finalized_at", "bytes"):
        assert field in side, f"the dialog cannot show {field}"
    assert side["operator"] == "Stacy" and side["phantom"] == "MSF-07"
    assert side["bytes"] == len(data), "the size shown is the stored file's"


def test_several_files_are_answered_in_one_question(client):
    """A CD export is a folder. One round trip for the lot, not one each."""
    here, gone = _scan_bytes(), _scan_bytes()
    _upload(client, here)
    answer = client.post("/api/upload_check", json={"sha256": [
        hashlib.sha256(here).hexdigest(),
        hashlib.sha256(gone).hexdigest()]}).json()["duplicates"]
    assert [d["sha256"] for d in answer] == [hashlib.sha256(here).hexdigest()]


@pytest.mark.parametrize("junk", [
    "", "  ", "not-a-hash", "z" * 64, "ab", "0" * 63, "0" * 65, None, 5,
])
def test_anything_that_is_not_a_hash_is_ignored_not_an_error(client, junk):
    """A pre-flight check that can 500 is worse than no pre-flight check —
    it would block the upload it exists to make cheaper."""
    answer = client.post("/api/upload_check", json={"sha256": [junk]})
    assert answer.status_code in (200, 422), answer.text
    if answer.status_code == 200:
        assert answer.json()["duplicates"] == []


def test_the_question_cannot_be_used_to_walk_the_archive(client):
    """Only the hashes asked about come back, and only ones already held.

    The reply is per-hash, so there is no listing to harvest: knowing the
    answer requires already holding the file."""
    _upload(client, _scan_bytes())
    answer = client.post("/api/upload_check", json={"sha256": []})
    assert answer.json()["duplicates"] == []

    flood = client.post("/api/upload_check",
                        json={"sha256": [f"{i:064x}" for i in range(500)]})
    assert flood.status_code == 200
    assert flood.json()["duplicates"] == []


def test_asking_does_not_create_anything(client):
    data = _scan_bytes()
    _upload(client, data)
    before = len(client.get("/api/analyses").json()["analyses"])
    client.post("/api/upload_check",
                json={"sha256": [hashlib.sha256(data).hexdigest()]})
    assert len(client.get("/api/analyses").json()["analyses"]) == before


# ------------------------------------------------- the refusal itself stands

def test_the_upload_still_checks_the_bytes_it_received(client):
    """The browser's answer is advice. The bytes are the authority.

    A hash is trivial to fake, so nothing may depend on the pre-flight reply
    being honest — it can only ever save a transfer, never permit one."""
    data = _scan_bytes()
    _upload(client, data)
    again = _upload(client, data)
    assert again.status_code == 409
    assert again.json()["duplicate_of"][0]["operator"] is not None


def test_the_refusal_carries_the_same_detail_as_the_pre_check(client):
    """Whichever way the operator arrives at it, they see the same thing."""
    data = _scan_bytes()
    _upload(client, data, operator="Stacy")
    refused = _upload(client, data).json()["duplicate_of"][0]
    checked = client.post("/api/upload_check",
                          json={"sha256": [hashlib.sha256(data).hexdigest()]}
                          ).json()["duplicates"][0]["duplicate_of"][0]
    assert refused == checked


def test_a_genuinely_different_scan_of_the_same_phantom_goes_through(client):
    """The complaint was that these were being refused. They never were.

    Two exposures of one phantom minutes apart differ in noise alone, and the
    check is on bytes, so both are accepted — which is what the log shows and
    what has to keep being true."""
    first, second = _scan_bytes(), _scan_bytes()
    assert _upload(client, first, name="003_0000.dcm").status_code == 200
    assert _upload(client, second, name="003_0000.dcm").status_code == 200
    assert len(client.get("/api/analyses").json()["analyses"]) == 2


def test_the_file_name_decides_nothing(client):
    """Three names in the field carried different files on different days.

    Identity is the content; a name is a label someone's export tool chose."""
    data = _scan_bytes()
    _upload(client, data, name="IM_0001.dcm")
    assert _upload(client, data, name="completely-different.dcm"
                   ).status_code == 409


def test_a_discarded_record_stops_blocking_its_file(client):
    """The file has to be re-sendable once the record holding it is gone —
    otherwise discarding a mistake would lock the operator out of the scan."""
    data = _scan_bytes()
    aid = _upload(client, data).json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/discard", json={"confirm": True})

    assert client.post("/api/upload_check",
                       json={"sha256": [hashlib.sha256(data).hexdigest()]}
                       ).json()["duplicates"] == []
    assert _upload(client, data).status_code == 200


# --------------------------------------- the same exposure in another wrapper

def _dicom_bytes(uid, *, rows=256, seed=0):
    """A minimal DICOM carrying a chosen SOP Instance UID."""
    pydicom = pytest.importorskip("pydicom")
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian

    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 4000, size=(rows, rows), dtype=np.uint16)

    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.1"
    ds.file_meta.MediaStorageSOPInstanceUID = uid
    ds.SOPInstanceUID = uid
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.1"
    ds.Modality = "DX"
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.SamplesPerPixel = 1
    ds.Rows, ds.Columns = pixels.shape
    ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 16, 15
    ds.PixelRepresentation = 0
    ds.PixelData = pixels.tobytes()
    ds.is_little_endian, ds.is_implicit_VR = True, False

    buf = io.BytesIO()
    pydicom.dcmwrite(buf, ds, write_like_original=False)
    return buf.getvalue()


def test_the_same_exposure_re_exported_is_remarked_on_not_refused(client):
    """Different bytes, same exposure — the hash cannot see it.

    Re-exported from the archive, it would become a second record counting
    again in every trend. The operator is told, and decides."""
    uid = "1.2.826.0.1.3680043.8.498.11111111111111111111"
    first = _upload(client, _dicom_bytes(uid, seed=1), name="orig.dcm")
    assert first.status_code == 200, first.text
    aid = first.json()["analyses"][0]["id"]

    again = _upload(client, _dicom_bytes(uid, seed=2), name="re-export.dcm")
    assert again.status_code == 200, "a remark must never block the upload"
    entry = again.json()["analyses"][0]
    assert [d["id"] for d in entry["same_exposure"]] == [aid]


def test_a_different_exposure_is_not_remarked_on(client):
    a = "1.2.826.0.1.3680043.8.498.22222222222222222222"
    b = "1.2.826.0.1.3680043.8.498.33333333333333333333"
    _upload(client, _dicom_bytes(a, seed=1), name="a.dcm")
    entry = _upload(client, _dicom_bytes(b, seed=2),
                    name="b.dcm").json()["analyses"][0]
    assert entry["same_exposure"] == []


def test_a_record_is_never_the_same_exposure_as_itself(client):
    uid = "1.2.826.0.1.3680043.8.498.44444444444444444444"
    entry = _upload(client, _dicom_bytes(uid), name="only.dcm"
                    ).json()["analyses"][0]
    assert entry["same_exposure"] == []


def test_a_uid_lookup_cannot_be_steered_by_its_own_text(client):
    """The UID goes into a LIKE pattern, so it is checked before it gets
    there — digits and dots only, no wildcards, no quotes."""
    store = client.mod.store
    for hostile in ("%", "_", '" or "1', "1.2.%", "'; DROP TABLE analyses--",
                    "1.2.3%", ""):
        assert store.find_by_sop_uid(hostile) == []
    assert store.get is not None, "the table is still there"


def test_a_plain_image_without_a_uid_is_not_matched_to_everything(client):
    """A PNG carries no SOP UID. An empty needle must match nothing, not all."""
    _upload(client, _scan_bytes(), name="a.png")
    entry = _upload(client, _scan_bytes(), name="b.png").json()["analyses"][0]
    assert entry["same_exposure"] == []


# --------------------------------------------------------- the browser's half

import os
import re

_STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "phantom_qa", "webapp", "static")


def _static(name):
    with open(os.path.join(_STATIC, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def app_js():
    return _static("app.js")


@pytest.fixture(scope="module")
def index_html():
    return _static("index.html")


def test_the_check_happens_before_the_bytes_are_sent(app_js):
    """Otherwise the change saves nothing at all.

    The refusal was never the expensive part — the transfer before it was."""
    start = app_js.index("async function uploadFile(")
    body = app_js[start:app_js.index("\nasync function openAnalysis(")]
    # Whichever way the bytes are sent — this has already moved once, from
    # fetch() to the XMLHttpRequest that can report progress — the check has
    # to come first. Named rather than pattern-matched so that changing the
    # sender again fails here with a sentence instead of a ValueError.
    senders = [c for c in ("uploadWithProgress(", 'api("api/analyses"')
               if c in body]
    assert senders, ("uploadFile no longer sends the file by any known means; "
                     "point this test at the new one")
    assert body.index("findExistingCopy") < min(body.index(s) for s in senders), \
        "the duplicate check must precede the upload, not follow it"


def test_a_browser_that_cannot_hash_still_uploads(app_js):
    """crypto.subtle is absent over plain http, which is how some sites run.

    Losing the pre-flight check must cost a transfer and nothing more — never
    the ability to upload."""
    body = app_js[app_js.index("async function fileSha256("):
                  app_js.index("async function uploadFile(")]
    assert "return null" in body and "catch" in body
    assert "crypto.subtle" in body


def test_a_failed_check_never_blocks_the_upload(app_js):
    body = app_js[app_js.index("async function findExistingCopy("):
                  app_js.index("async function uploadFile(")]
    assert body.count("return null") >= 2, \
        "every way the check can fail must fall through to the upload"
    assert ".zip" in body, "a zip's container hash fingerprints nothing"


def test_continuing_the_existing_analysis_is_the_primary_action(index_html):
    """It is nearly always what was meant, and it was previously phrased as
    "Open the existing analysis" next to "Analyse again anyway" — which reads
    as a choice between looking and working."""
    card = index_html[index_html.index('id="dup-backdrop"'):
                      index_html.index('id="val-backdrop"')]
    primary = card[card.index('id="dup-open"'):card.index('id="dup-again"')]
    assert 'class="primary"' in primary
    assert "Continue this analysis" in primary
    assert 'class="secondary"' in card[card.index('id="dup-again"'):]


def test_the_dialog_shows_both_sides(index_html):
    card = index_html[index_html.index('id="dup-backdrop"'):
                      index_html.index('id="val-backdrop"')]
    for field in ("dup-new-name", "dup-new-size", "dup-new-mtime",
                  "dup-name", "dup-size", "dup-operator", "dup-acquired",
                  "dup-when", "dup-stage", "dup-status", "dup-thumb"):
        assert f'id="{field}"' in card, f"the dialog cannot show {field}"


def test_every_field_the_dialog_fills_exists_in_the_markup(app_js, index_html):
    """The two files have no compiler between them."""
    body = app_js[app_js.index("function duplicateDialog("):
                  app_js.index("async function placeBlock(")]
    for ref in set(re.findall(r'\$\("#(dup-[a-z-]+)"\)', body)):
        assert f'id="{ref}"' in index_html, f"app.js fills #{ref}, absent here"


def test_operator_text_is_never_written_as_markup(app_js):
    """Site, phantom, operator and file names are all typed by people or
    chosen by an export tool; none of them may be parsed as markup."""
    body = app_js[app_js.index("function duplicateDialog("):
                  app_js.index("async function placeBlock(")]
    assert ".innerHTML" not in body
    assert body.count("textContent") >= 10

    chosen = app_js[app_js.index("const box = $(\"#file-chosen\")"):]
    chosen = chosen[:chosen.index("drop.addEventListener")]
    assert "box.innerHTML" not in chosen, "a file name is not markup"


def test_the_thumbnail_is_asked_for_small(app_js):
    """A few kB settles "is that my scan". The full image costs fifteen
    seconds on the link this is deployed over."""
    body = app_js[app_js.index("function duplicateDialog("):
                  app_js.index("async function placeBlock(")]
    match = re.search(r"image\.(?:png|jpg)\?scale=(\d+)", body)
    assert match and int(match.group(1)) <= 256, \
        "the dialog must not pull the full-size render"


def test_the_chosen_file_box_shows_what_tells_two_exports_apart(app_js):
    """A machine names every scan 003_0000.dcm. The clock is the difference."""
    chosen = app_js[app_js.index("const box = $(\"#file-chosen\")"):]
    chosen = chosen[:chosen.index("drop.addEventListener")]
    assert "fileStamp" in chosen and "fileSize" in chosen
