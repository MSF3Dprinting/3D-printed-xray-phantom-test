"""The user's rule, guarded for the whole application.

"The compression could never lead to lose of visual details! Never in any
part of the app. This is a critical qc tool we cannot compromise it."

Two guards. The first reads the code: no lossy encoding anywhere, except the
comparison's small pictures, which the user chose to keep as JPEG ("keep
compressed images not full size") — exactly one call, in one place. The
second takes two real reference scans through the application and checks
every kind of picture it shows of them, pixel by pixel, against a
calculation written out here, independently of the code that made them.
"""

import base64
import io
import os
import re

import numpy as np
import pytest
from PIL import Image

import hq_manifest as hm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "phantom_qa")
STATIC = os.path.join(PKG, "webapp", "static")


def _python_sources():
    for folder, _, names in os.walk(PKG):
        for name in names:
            if name.endswith(".py"):
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as f:
                    yield os.path.relpath(path, ROOT).replace("\\", "/"), f.read()


# ------------------------------------------------ 1. nothing lossy in the code

#: Ways a picture could be encoded with loss, in Python.
LOSSY = [
    (r'format\s*=\s*["\']jpe?g["\']', "JPEG"),
    (r'\.quantize\s*\(', "palette reduction"),
    (r'convert\(\s*["\']P["\']', "palette conversion"),
    (r'lossless\s*=\s*False', "lossy WebP"),
    (r'savefig\([^)]*format\s*=\s*["\'](jpe?g|webp)', "lossy chart"),
    (r'pil_kwargs', "encoder settings through the chart library"),
]

#: The single allowed exception: the comparison's small pictures.
ALLOWED = {("phantom_qa/thumbnails.py", "JPEG")}


def _found():
    out = []
    for rel, text in _python_sources():
        for pattern, what in LOSSY:
            for m in re.finditer(pattern, text, re.I):
                line = text.count("\n", 0, m.start()) + 1
                out.append((rel, what, line))
    return out


def test_nothing_in_the_server_code_encodes_a_picture_with_loss():
    wrong = [f for f in _found() if (f[0], f[1]) not in ALLOWED]
    assert not wrong, f"lossy encoding: {wrong}"


def test_the_one_lossy_picture_is_the_comparisons_small_pictures():
    """Exactly one JPEG call, in thumbnails.encode_on — if a second one
    appears, even in the same file, this fails."""
    import inspect
    from phantom_qa import thumbnails
    allowed = [f for f in _found() if (f[0], f[1]) in ALLOWED]
    assert len(allowed) == 1, allowed
    assert 'format="JPEG"' in inspect.getsource(thumbnails.encode_on)


def test_every_webp_the_server_writes_is_the_lossless_kind():
    for rel, text in _python_sources():
        for m in re.finditer(r'format\s*=\s*["\']WEBP["\']', text, re.I):
            line = text[text.rfind("\n", 0, m.start()) + 1:text.find("\n", m.end())]
            assert "lossless=True" in line, f"{rel}: {line.strip()}"


def test_nothing_in_the_browser_code_makes_a_lossy_picture():
    """No canvas exported as JPEG or WebP (a canvas's WebP is lossy by
    default), no JPEG picture asked for, and the only place that re-maps 8-bit
    picture values is the window/level preview, which is labelled as one."""
    from phantom_qa.comparison_report import _COMPARISON_SCRIPT
    from phantom_qa.report import REPORT_SCRIPT
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        app_js = f.read()
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        index = f.read()
    code = "\n".join((app_js, index, _COMPARISON_SCRIPT, REPORT_SCRIPT))
    for pattern in (r'toDataURL\(\s*["\']image/(jpeg|webp)',
                    r'toBlob\([^)]*image/(jpeg|webp)', r'image\.jpg',
                    r'["\']image/jpeg["\']'):
        assert not re.search(pattern, code), pattern
    preview = app_js[app_js.index("function renderWLPreview("):]
    preview = preview[:preview.index("\nlet wlTimer")]
    assert app_js.count("putImageData") == preview.count("putImageData") == 1
    assert '"Preview — exact picture loading"' in app_js


# ------------------------------------------------ 2. real scans, every picture

SCANS = ("PH0730_03", "CS000001")


def _entry(key):
    return next(e for e in hm.INVENTORY if e["key"] == key)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_authorization import _build_app, _login
    mod = _build_app(tmp_path, monkeypatch)
    c = TestClient(mod.app)
    c.headers.update({"X-CSRF-Token": _login(c)})
    c.mod = mod
    return c


def _grey(payload: bytes) -> np.ndarray:
    arr = np.asarray(Image.open(io.BytesIO(payload)))
    if arr.ndim == 3:
        assert np.array_equal(arr[..., 0], arr[..., 1]) \
            and np.array_equal(arr[..., 0], arr[..., 2]), "the picture is not grey"
        arr = arr[..., 0]
    return arr


def _window(values, lo, hi):
    """Nearest grey level, black and white outside the window."""
    v = np.asarray(values, dtype=np.float64)
    return np.clip(np.rint((v - lo) * (255.0 / (hi - lo))), 0, 255
                   ).astype(np.uint8)


def _members(n_in, n_out):
    """Which input pixels each output pixel averages: those whose centres
    fall in it, j + 0.5 in (i*s, (i+1)*s]. Decided in the image library's
    own double-precision arithmetic, because a pixel exactly on the edge
    between two groups joins the one that arithmetic puts it in (measured:
    2874 -> 640 rows, scan row 718 at exactly 718.5 joins the next group)."""
    scale = n_in / n_out
    support, ss = 0.5 * scale, 1.0 / scale
    out = []
    for i in range(n_out):
        centre = (i + 0.5) * scale
        lo = max(int(centre - support + 0.5), 0)
        hi = min(int(centre + support + 0.5), n_in)
        out.append([j for j in range(lo, hi)
                    if -0.5 < (j - centre + 0.5) * ss <= 0.5])
    return out


def _shrunk_1d(values, n_out, axis):
    """One direction of the averaging: each output pixel the plain mean of
    its members, added up one after another in double precision (x * 1/n,
    in order) and kept as single precision — as the application's image
    library does, so the comparison can be exact to the last bit."""
    v = np.moveaxis(np.asarray(values, dtype=np.float64), axis, -1)
    members = _members(v.shape[-1], n_out)
    k = np.array([1.0 / len(m) for m in members])
    acc = np.zeros(v.shape[:-1] + (n_out,))
    for step in range(max(len(m) for m in members)):
        idx = np.array([m[step] if step < len(m) else -1 for m in members])
        valid = idx >= 0
        acc = acc + np.where(valid, v[..., np.where(valid, idx, 0)] * k, 0.0)
    return np.moveaxis(acc.astype(np.float32), -1, axis)


def _shrunk(values, size):
    """The scan averaged to (width, height): across first, then down."""
    w, h = size
    v = np.asarray(values, dtype=np.float32)
    if w < v.shape[1]:
        v = _shrunk_1d(v, w, axis=1)
    if h < v.shape[0]:
        v = _shrunk_1d(v, h, axis=0)
    return v


def _block_means(values, f):
    """Each f x f block's mean, edge blocks over what is there."""
    v = np.asarray(values, dtype=np.float64)
    h, w = v.shape
    table = np.zeros((h + 1, w + 1))
    table[1:, 1:] = v.cumsum(0).cumsum(1)
    ys, xs = np.arange(0, h, f), np.arange(0, w, f)
    y1, x1 = np.minimum(ys + f, h), np.minimum(xs + f, w)
    sums = (table[y1][:, x1] - table[ys][:, x1]
            - table[y1][:, xs] + table[ys][:, xs])
    return sums / ((y1 - ys)[:, None] * (x1 - xs)[None, :])


def _size(shape, longest):
    h, w = shape
    k = longest / max(h, w)
    return (round(w * k), round(h * k))


def test_the_averaging_is_written_out_right():
    """The written-out averaging must itself agree with the rule it states,
    on a case small enough to check by hand (7 -> 3: pixels 0-1, 2-4, 5-6)."""
    row = np.arange(7, dtype=float)[None, :]
    assert _shrunk(row, (3, 1)).tolist() == [[0.5, 3.0, 5.5]]


@pytest.mark.parametrize("key", SCANS)
def test_every_picture_of_a_real_scan_is_exact(client, key):
    """The viewer's picture at two screen sizes in both formats, every zoom
    piece at the two smaller levels, the full-size pieces across the middle
    and along the edges, three contrast steps of the disc close-up, and the
    printed report's picture — each decoded and compared pixel by pixel."""
    entry = _entry(key)
    if not hm.available(entry):
        pytest.skip(f"{key} is not on this computer")
    with open(hm.scan_path(entry), "rb") as f:
        up = client.post("/api/analyses", files={"file": ("scan.dcm", f.read())},
                         data={"site": "GUARD", "phantom": key})
    aid = up.json()["analyses"][0]["id"]
    client.post(f"/api/analyses/{aid}/confirm", json={"stage": "A"})
    client.post(f"/api/analyses/{aid}/propose", json={})
    mod = client.mod
    stored = mod._scan(aid).pixels
    lo, hi = np.percentile(stored, [1, 99])
    get = lambda url: _grey(client.get(url).content)

    # The viewer's picture.
    for longest in (640, 1024):
        want = _window(_shrunk(stored, _size(stored.shape, longest)), lo, hi)
        for fmt in ("webp", "png"):
            got = get(f"/api/analyses/{aid}/image.{fmt}?scale={longest}")
            assert np.array_equal(got, want), f"overview {longest} px {fmt}"

    # Full detail: the two smaller levels whole, the full size in part.
    h, w = stored.shape
    for level in (1, 2):
        f = 2 ** level
        means = _window(_block_means(stored, f), lo, hi)
        for ty in range(-(-means.shape[0] // 256)):
            for tx in range(-(-means.shape[1] // 256)):
                got = get(f"/api/analyses/{aid}/tile/{level}/{tx}/{ty}.webp")
                want = means[ty * 256:(ty + 1) * 256, tx * 256:(tx + 1) * 256]
                assert np.array_equal(got, want), f"level {level} ({tx}, {ty})"
    last_x, last_y = (w - 1) // 256, (h - 1) // 256
    pieces = {(x, y) for x in range(last_x // 2 - 1, last_x // 2 + 2)
              for y in range(last_y // 2 - 1, last_y // 2 + 2)}
    pieces |= {(last_x, y) for y in range(last_y + 1)} | {(x, last_y) for x in range(last_x + 1)}
    full = _window(stored, lo, hi)
    for tx, ty in sorted(pieces):
        got = get(f"/api/analyses/{aid}/tile/0/{tx}/{ty}.webp")
        assert np.array_equal(got, full[ty * 256:(ty + 1) * 256,
                                         tx * 256:(tx + 1) * 256]), f"full ({tx}, {ty})"

    # The disc close-up: a processed view, but each contrast step exactly
    # its processed values on the narrowed window.
    key_ = client.get(f"/api/analyses/{aid}/lowcontrast_view").json()["key"]
    rec = mod.store.get(aid)
    _, centre, angle = mod._block_placement(rec)
    view = mod.lowcontrast.block_view(mod._ctx(aid, rec), centre, angle)
    vlo, vhi = view["window"]
    values = np.where(np.isfinite(view["values"]), view["values"], vlo)
    for gain in (50, 100, 250):
        mid, half = (vlo + vhi) / 2, (vhi - vlo) / 2 / (gain / 100)
        got = get(f"/api/analyses/{aid}/lowcontrast_view.webp?key={key_}&gain={gain}")
        assert np.array_equal(got, _window(values, mid - half, mid + half)), \
            f"close-up at {gain} %"

    # The printed report's picture of the scan.
    html = client.get(f"/api/analyses/{aid}/report.html").text
    raw = base64.b64decode(re.search(
        r'<div class="overview"><img [^>]*src="data:image/\w+;base64,'
        r'([A-Za-z0-9+/=]+)"', html).group(1))
    want = _window(_shrunk(stored, _size(stored.shape, 1000)), lo, hi)
    assert np.array_equal(_grey(raw), want), "report picture"
