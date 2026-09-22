"""Knowing an upload is still alive.

The constraint behind this one: "Consider that the user will work with very
low connection speed." A 7.5 MB scan at 512 kbit/s is two minutes, and for
those two minutes the interface said nothing — the status line that announced
the upload cleared itself after six seconds, leaving a screen indistinguishable
from one that had died.

What the field team did about that is in the audit log: they reloaded and sent
the file again. That is where most of the duplicate refusals came from, so the
silence was not a cosmetic problem — it manufactured the very duplicates the
duplicate check then refused.

There is no JavaScript runtime in the deployment and no build step, so these
are structural checks on the source: that the upload reports progress at all,
that it can be called off, and that calling it off is distinguishable from
failing.
"""

from __future__ import annotations

import os
import re

import pytest

STATIC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "phantom_qa", "webapp", "static")


def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def app_js():
    return _read("app.js")


@pytest.fixture(scope="module")
def style_css():
    return _read("style.css")


def _fn(app_js, name, end_marker):
    start = app_js.index(f"function {name}(")
    return app_js[start:app_js.index(end_marker, start)]


# --------------------------------------------------- the upload reports itself

def test_the_upload_goes_through_the_path_that_can_report_progress(app_js):
    """fetch() cannot say how much of a request body has been sent.

    That is the whole reason this request alone uses XMLHttpRequest — so if
    the upload is ever routed back through api(), the progress bar becomes a
    decoration that never moves."""
    body = _fn(app_js, "uploadFile", "\nasync function openAnalysis(")
    assert "uploadWithProgress" in body
    assert 'api("api/analyses", { method: "POST", body: fd })' not in body, \
        "the upload went back to fetch(), which cannot report progress"


def test_progress_is_read_from_the_bytes_actually_sent(app_js):
    body = _fn(app_js, "uploadWithProgress", "\n/* Seconds remaining")
    assert "xhr.upload.onprogress" in body
    assert "lengthComputable" in body, \
        "a total that is not known must not be reported as one"


def test_the_time_left_is_averaged_over_the_whole_transfer(app_js):
    """A field link stalls in bursts. An estimate from the latest instant
    swings between "3 seconds" and "9 minutes" and tells nobody anything."""
    body = _fn(app_js, "timeRemaining", "\nasync function uploadFile(")
    assert "startedAt" in body and "elapsed" in body
    assert "elapsed < 1.5" in body, \
        "an estimate from the first instants is noise presented as fact"


def test_the_finished_transfer_is_named_as_its_own_phase(app_js):
    """The bytes are gone but the server is still decoding and registering.

    A bar parked at 100% for several seconds reads as stuck, which is the
    same failure in a smaller form."""
    body = _fn(app_js, "showUploadProgress", "\nfunction hideUploadProgress(")
    assert "loaded < 0" in body
    assert "server is reading" in body


def test_the_progress_box_does_not_clear_itself(app_js):
    """The six-second status line is what made an upload in progress look
    like a dead screen; the replacement must not inherit that."""
    body = _fn(app_js, "showUploadProgress", "\nfunction hideUploadProgress(")
    assert "setTimeout" not in body


# ------------------------------------------------------- calling it off

def test_the_upload_can_be_called_off(app_js):
    assert "function cancelUpload(" in app_js
    body = _fn(app_js, "cancelUpload", "\n/* The SHA-256")
    assert ".abort()" in body


def test_cancelling_is_reported_as_a_choice_not_a_failure(app_js):
    """"Upload failed" for something the operator did on purpose teaches them
    to distrust the message when it is real."""
    body = _fn(app_js, "uploadFile", "\nasync function openAnalysis(")
    assert "e.cancelled" in body
    cancel_branch = body[body.index("e.cancelled"):]
    cancel_branch = cancel_branch[:cancel_branch.index("duplicateOf")]
    assert "true" in cancel_branch.lower() or "status(" in cancel_branch
    assert "Upload failed" not in cancel_branch


def test_the_chosen_file_survives_a_cancellation(app_js):
    """Otherwise changing your mind costs you the file selection too."""
    body = _fn(app_js, "uploadFile", "\nasync function openAnalysis(")
    branch = body[body.index("e.cancelled"):]
    branch = branch[:branch.index("duplicateOf")]
    assert "S.pendingFile = null" not in branch


def test_cancelling_stops_being_offered_once_the_bytes_are_gone(app_js):
    """Past that point aborting saves nothing and only loses the analysis the
    server is already making."""
    body = _fn(app_js, "showUploadProgress", "\nfunction hideUploadProgress(")
    assert "cancel.disabled = true" in body


# ------------------------------- the error shape every caller already handles

def test_a_duplicate_still_reaches_the_duplicate_dialog(app_js):
    """The upload path changed; the refusal it can produce did not."""
    body = _fn(app_js, "uploadWithProgress", "\n/* Seconds remaining")
    assert "duplicate_of" in body and "err.duplicateOf" in body


def test_an_expired_session_still_goes_to_the_login_page(app_js):
    body = _fn(app_js, "uploadWithProgress", "\n/* Seconds remaining")
    assert "Authentication required" in body
    assert 'window.location = "login"' in body


def test_a_refused_admin_password_is_not_mistaken_for_an_expired_session(app_js):
    """api() draws this distinction; the upload path must not lose it."""
    body = _fn(app_js, "uploadWithProgress", "\n/* Seconds remaining")
    branch = body[body.index("xhr.status === 401"):]
    assert "Authentication required" in branch[:200]


def test_a_dropped_connection_says_nothing_was_stored(app_js):
    """So the operator knows re-sending is safe rather than a way to make a
    second record."""
    body = _fn(app_js, "uploadWithProgress", "\n/* Seconds remaining")
    assert "xhr.onerror" in body
    assert "Nothing was stored" in body


def test_the_csrf_token_still_travels_with_the_upload(app_js):
    body = _fn(app_js, "uploadWithProgress", "\n/* Seconds remaining")
    assert "X-CSRF-Token" in body and "csrfToken()" in body
    assert "withCredentials" in body


# --------------------------------------------------------- markup and styling

def test_every_control_the_code_drives_exists_in_the_markup(app_js):
    """The upload step builds its own markup, so the two are in one file and
    can still disagree."""
    referenced = set()
    for name in ("showUploadProgress", "hideUploadProgress"):
        body = _fn(app_js, name, "\nfunction " if name ==
                   "showUploadProgress" else "\nfunction cancelUpload(")
        referenced |= set(re.findall(r'\$\("#(upload-[a-z-]+)"\)', body))
    referenced.add("upload-cancel")
    for ref in referenced:
        assert f'id="{ref}"' in app_js, f"#{ref} is driven but never created"


def test_the_bar_has_a_style_to_be_seen_by(style_css):
    for selector in ("#upload-bar", ".upload-track", ".upload-progress"):
        assert selector in style_css, f"{selector} is unstyled"


def test_the_animation_yields_to_a_reduced_motion_setting(style_css):
    assert "prefers-reduced-motion" in style_css
    block = style_css[style_css.index("prefers-reduced-motion"):]
    assert "animation: none" in block
