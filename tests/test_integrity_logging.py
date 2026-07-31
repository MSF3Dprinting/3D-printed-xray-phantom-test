"""SHA-256 integrity verification, report identification block, and logging."""

import glob
import json
import logging
import os
import types

import pytest

from phantom_qa.report import build_report
from phantom_qa.store import Store

from test_store_labels import fake_scan, results_for


@pytest.fixture()
def store(tmp_path):
    return Store(str(tmp_path))


def add(store, payload=b"phantom-scan-bytes", site="Goma", phantom="MSF-01"):
    import hashlib
    scan = fake_scan(sha=hashlib.sha256(payload).hexdigest())
    aid = store.new_analysis(scan, payload, "sig", "1.0.0", "1.0",
                             labels={"site": site, "phantom": phantom,
                                     "operator": "ST", "notes": "n"})
    store.update(aid, results=results_for(), status="pass")
    return aid


# ------------------------------------------------------------------ integrity

def test_verify_passes_for_an_untouched_file(store):
    aid = add(store)
    r = store.verify_integrity(aid)
    assert r["status"] == "ok"
    assert r["stored_sha256"] == r["computed_sha256"]
    assert r["size_bytes"] > 0


def test_verify_detects_a_modified_file(store):
    aid = add(store)
    with open(store.upload_path(aid), "wb") as f:
        f.write(b"tampered")
    r = store.verify_integrity(aid)
    assert r["status"] == "mismatch"
    assert r["computed_sha256"] != r["stored_sha256"]
    assert "do not rely" in r["message"].lower()


def test_verify_detects_a_single_flipped_byte(store):
    """Silent corruption is the realistic failure, not wholesale replacement."""
    payload = b"A" * 4096
    aid = add(store, payload)
    data = bytearray(payload)
    data[2048] ^= 0x01
    with open(store.upload_path(aid), "wb") as f:
        f.write(bytes(data))
    assert store.verify_integrity(aid)["status"] == "mismatch"


def test_verify_reports_a_missing_file(store):
    aid = add(store)
    os.remove(store.upload_path(aid))
    r = store.verify_integrity(aid)
    assert r["status"] == "missing_file"
    assert r["computed_sha256"] is None


def test_verify_unknown_analysis(store):
    assert store.verify_integrity("nope")["status"] == "not_found"


def test_verify_all_covers_every_analysis(store):
    a1, a2 = add(store, b"one"), add(store, b"two")
    with open(store.upload_path(a2), "ab") as f:
        f.write(b"extra")
    out = {r["id"]: r["status"] for r in store.verify_all()}
    assert out[a1] == "ok"
    assert out[a2] == "mismatch"


def test_recorded_hash_matches_a_plain_sha256_of_the_file(store):
    """The value in the report must be what sha256sum / Get-FileHash produce,
    otherwise the documented manual check would not work."""
    import hashlib
    payload = b"exact-bytes-of-the-dicom"
    aid = add(store, payload)
    rec = store.get(aid)
    assert rec["sha256"] == hashlib.sha256(payload).hexdigest()
    with open(store.upload_path(aid), "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == rec["sha256"]


# --------------------------------------------------------------------- report

def test_report_header_shows_identification(store):
    aid = add(store)
    rec = store.get(aid)
    html_out = build_report(rec, integrity=store.verify_integrity(aid))
    assert "Identification" in html_out
    for field in ("Site", "Phantom", "Operator", "Notes"):
        assert f">{field}<" in html_out, f"{field} label missing"
    assert "Goma" in html_out and "MSF-01" in html_out
    assert "ST" in html_out


def test_report_warns_when_identification_is_missing(store):
    aid = add(store, site="", phantom="")
    html_out = build_report(store.get(aid))
    assert "No site or phantom recorded" in html_out


def test_report_explains_and_verifies_the_hash(store):
    aid = add(store)
    rec = store.get(aid)
    out = build_report(rec, integrity=store.verify_integrity(aid))
    assert "Source file integrity" in out
    assert rec["sha256"] in out
    assert "verified" in out
    # the manual instructions must actually be present
    assert "Get-FileHash" in out and "sha256sum" in out
    assert "What this is for" in out


def test_report_flags_a_mismatch_loudly(store):
    aid = add(store)
    with open(store.upload_path(aid), "wb") as f:
        f.write(b"changed")
    out = build_report(store.get(aid), integrity=store.verify_integrity(aid))
    assert "MISMATCH" in out
    assert "do not rely" in out.lower()


# -------------------------------------------------------------------- logging

def test_logging_creates_rotating_files(tmp_path):
    import phantom_qa.logging_setup as ls
    ls._configured = False
    for name in (ls.APP_LOGGER, ls.AUDIT_LOGGER):
        logging.getLogger(name).handlers.clear()
    d = tmp_path / "logs"
    app = ls.setup_logging(str(d), level="DEBUG", max_mb=1, backups=3,
                           console=False)
    app.info("hello")
    app.error("bad thing")
    ls.audit("delete", user="qa", client="1.2.3.4", analysis="abc",
             outcome="ok", reason="test")
    for h in list(app.handlers) + list(logging.getLogger(ls.AUDIT_LOGGER).handlers):
        h.flush()

    assert (d / "phantomqa.log").exists()
    assert (d / "errors.log").exists()
    assert (d / "audit.log").exists()
    assert "hello" in (d / "phantomqa.log").read_text(encoding="utf-8")
    # errors.log must contain only the warning-and-above records
    err = (d / "errors.log").read_text(encoding="utf-8")
    assert "bad thing" in err and "hello" not in err
    aud = (d / "audit.log").read_text(encoding="utf-8")
    assert "event=delete" in aud and "user=qa" in aud and "analysis=abc" in aud


def test_log_handlers_are_rotating(tmp_path):
    import logging.handlers
    import phantom_qa.logging_setup as ls
    ls._configured = False
    for name in (ls.APP_LOGGER, ls.AUDIT_LOGGER):
        logging.getLogger(name).handlers.clear()
    app = ls.setup_logging(str(tmp_path / "l2"), max_mb=1, backups=4,
                           console=False)
    rotating = [h for h in app.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(rotating) >= 2, "app + error logs must both rotate"
    for h in rotating:
        assert h.maxBytes == 1024 * 1024
        assert h.backupCount == 4


def test_log_files_actually_rotate(tmp_path):
    import phantom_qa.logging_setup as ls
    ls._configured = False
    for name in (ls.APP_LOGGER, ls.AUDIT_LOGGER):
        logging.getLogger(name).handlers.clear()
    d = tmp_path / "l3"
    app = ls.setup_logging(str(d), max_mb=1, backups=3, console=False)
    for h in app.handlers:
        if hasattr(h, "maxBytes"):
            h.maxBytes = 2048                     # force rotation quickly
    for i in range(400):
        app.info("padding line %04d %s", i, "x" * 80)
    for h in app.handlers:
        h.flush()
    assert glob.glob(str(d / "phantomqa.log.*")), "no rotated files produced"
    assert len(glob.glob(str(d / "phantomqa.log*"))) <= 4, "backupCount ignored"


def test_audit_redacts_secrets(tmp_path):
    import phantom_qa.logging_setup as ls
    ls._configured = False
    for name in (ls.APP_LOGGER, ls.AUDIT_LOGGER):
        logging.getLogger(name).handlers.clear()
    d = tmp_path / "l4"
    ls.setup_logging(str(d), console=False)
    ls.audit("delete", user="qa", admin_password="hunter2-should-not-appear",
             token="abc123", nested={"password": "also-secret", "site": "Goma"})
    for h in logging.getLogger(ls.AUDIT_LOGGER).handlers:
        h.flush()
    text = (d / "audit.log").read_text(encoding="utf-8")
    assert "hunter2-should-not-appear" not in text
    assert "also-secret" not in text
    assert "abc123" not in text
    assert "***" in text
    assert "Goma" in text, "non-secret detail should survive"


def test_redact_helper_is_recursive():
    from phantom_qa.logging_setup import redact
    out = redact({"password": "x", "a": [{"secret": "y"}], "keep": 1})
    assert out["password"] == "***"
    assert out["a"][0]["secret"] == "***"
    assert out["keep"] == 1
