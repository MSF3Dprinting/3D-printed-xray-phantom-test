"""Getting back to the upload step without reloading the page.

Reported after trying the app: "there is no way how user can click and get to
the Upload tab if they decide to continue different analysis, or upload a
different file, the only way is to reload the page."

That was true. The two tabs switch between the open analysis and History, and
"New analysis" exists only on the last step — so an operator who changed their
mind on step B or C had nowhere to click. The step bar already showed
"Upload" as its first entry, which reads like a control and was not one.

Leaving is not abandoning: the scan is on the server, in History and in the
unfinished list on the page this opens, so it can be continued by this
operator or a colleague. Only the first entry is clickable — the later steps
record where the work has got to, and step E starts a measurement as soon as
it is drawn, which a stray click must never do.

There is no JavaScript runtime in the tests, so these are structural checks on
the sources, in the house style of tests/test_upload_progress.py.
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
def index_html():
    return _read("index.html")


@pytest.fixture(scope="module")
def style_css():
    return _read("style.css")


def _fn(src, name):
    start = src.index(f"function {name}(")
    nxt = re.search(r"\n(async )?function |\n\$\(", src[start + 10:])
    return src[start:start + 10 + nxt.start()] if nxt else src[start:]


# ------------------------------------------------------- the way back exists

def test_the_upload_entry_is_a_control_not_a_label(index_html):
    nav = index_html[index_html.index('<ol id="stage-nav">'):
                     index_html.index("</ol>")]
    upload = nav[:nav.index("</li>")]
    assert 'id="nav-upload"' in upload
    assert 'role="button"' in upload and 'tabindex="0"' in upload


def test_it_is_wired_to_the_click_and_to_the_keyboard(app_js):
    assert '$("#nav-upload").addEventListener("click", goToUpload)' in app_js
    keys = app_js[app_js.index('$("#nav-upload").addEventListener("keydown"'):]
    keys = keys[:keys.index("});")]
    assert '"Enter"' in keys and '" "' in keys and "goToUpload()" in keys


def test_it_looks_clickable(style_css):
    assert "#stage-nav li.nav-go" in style_css and "cursor: pointer" in style_css


def test_leaving_lands_on_the_upload_step_of_the_analysis_tab(app_js):
    """Pressed from History as well, so it has to bring its own view back."""
    body = _fn(app_js, "goToUpload")
    assert "clearAnalysisState()" in body
    assert 'showTab("analyze")' in body
    assert 'setStage("U")' in body


# ------------------------------------------- what leaving must not destroy

def test_an_unfinished_analysis_keeps_its_continue_note(app_js):
    """clearAnalysisState also forgets the note, because the same call runs
    when a record is deleted. Here the record is alive and the operator is
    only starting another scan, so the note is put back and the analysis goes
    on being offered."""
    body = _fn(app_js, "goToUpload")
    assert "rememberedAnalysis()" in body
    assert "localStorage.setItem(OPEN_KEY" in body
    assert "S.record.results" in body, "a finished analysis must not be offered"


def test_a_finished_or_signed_off_analysis_is_not_offered_again(app_js):
    body = _fn(app_js, "goToUpload")
    assert "validation_status" in body


def test_the_note_of_a_different_analysis_is_never_rewritten(app_js):
    """The note may belong to another scan entirely; leaving this one must not
    overwrite it with this one's identity."""
    body = _fn(app_js, "goToUpload")
    assert "note.aid === S.aid" in body


def test_private_mode_cannot_break_leaving(app_js):
    body = _fn(app_js, "goToUpload")
    assert "catch" in body, "storage can throw; leaving must still work"


# ------------------------------------------- the later steps stay untouched

def test_only_the_upload_entry_is_clickable(app_js, index_html):
    """The later entries record where the work has got to. Step E computes as
    soon as it is drawn, so making the bar navigable would start a
    measurement on a stray click."""
    nav = index_html[index_html.index('<ol id="stage-nav">'):
                     index_html.index("</ol>")]
    assert nav.count("nav-go") == 1
    assert nav.count('role="button"') == 1
    for stage in ("A", "B", "C", "D", "E", "F"):
        entry = nav[nav.index(f'data-stage="{stage}"'):]
        entry = entry[:entry.index("</li>")]
        assert "tabindex" not in entry and "role=" not in entry, stage
    assert '#stage-nav li"' not in app_js.replace("querySelectorAll(\"#stage-nav li\")", ""), \
        "nothing may add a click handler to the whole step bar"


def test_the_last_step_button_uses_the_same_road(app_js):
    """Two entrances that behave differently is how the older one drifted."""
    assert '$("#btn-new").addEventListener("click", goToUpload)' in app_js


# ------------------------------------- it has to LOOK like a way back

def test_the_upload_entry_differs_from_the_plain_steps_without_being_touched():
    """The first version only changed colour on hover, so an operator reading
    the bar could not tell it was clickable at all — which is exactly what was
    reported. It now carries the link colour and a dashed outline, against
    muted text and no border on the steps."""
    css = _read("style.css")
    rule = css[css.index("#stage-nav li.nav-go {"):]
    rule = rule[:rule.index("}")]
    assert "color: var(--accent)" in rule
    assert "dashed" in rule
    plain = css[css.index("#stage-nav li {"):]
    plain = plain[:plain.index("}")]
    assert "var(--muted)" in plain and "transparent" in plain


def test_the_label_says_it_goes_back(index_html):
    nav = index_html[index_html.index('<ol id="stage-nav">'):]
    upload = nav[:nav.index("</li>")]
    assert "←" in upload


def test_standing_on_the_upload_step_it_looks_like_the_current_step():
    css = _read("style.css")
    assert "#stage-nav li.nav-go.active" in css


# ------------------------------------- the first step has a back button too

def test_registration_offers_the_way_back(app_js):
    """Every other step has a button back to the one before it. The step
    before registration is the upload page, and it had none."""
    assert 'id="btn-back-u"' in app_js
    assert '$("#btn-back-u").addEventListener("click", goToUpload)' in app_js


def test_it_is_worded_like_the_other_back_buttons(app_js):
    labels = re.findall(r'id="btn-back-[a-u]">([^<]+)<', app_js)
    assert "Back to upload" in labels
    assert all(l.startswith("Back to ") for l in labels), labels


def test_the_back_button_sits_on_the_registration_step(app_js):
    body = app_js[app_js.index("function stageA("):app_js.index("function stageB(")]
    assert 'id="btn-back-u"' in body and "goToUpload" in body
