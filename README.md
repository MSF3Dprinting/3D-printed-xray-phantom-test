# MSF Phantom QA

Analysis app for X-ray scans of the MSF 3D-printed QA phantom. Upload a DICOM
scan, verify what the program detected in a step-by-step wizard, and get
rigorous, reproducible QA metrics with full traceability — plus CSV/JSON export
and trend comparison across scans and phantoms.

Implements the tests of the *MSF X-ray QA phantom acquisition and analysis
guide*: X-ray/light-field alignment, spatial-resolution line patterns (SD +
**line-pattern linearity plots**), low-contrast CNR, uniformity SNR, and the
dynamic-range wedge — and adds dimension verification from the corner marks and
side rulers. **No material assumptions are made anywhere**: wedge steps and
low-contrast circles are identified by position/design order only, and all
reported values are direct measurements.

---

## Quick start

```powershell
# once
python -m pip install -r requirements.txt

# start the app
python run_app.py            # -> open http://127.0.0.1:8777

# run the test suite
python -m pytest tests -q

# headless batch analysis (no verification gates; for validation/scripting)
python -m phantom_qa.cli "path\to\DICOMFILE" --out qa_output --sid 1000
```

Everything runs locally; no internet connection is used or required.

**Deploying on a server?** Read [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) first —
it covers the password, secrets in `.env`, TLS via a reverse proxy, and the
systemd unit. Short version:

```bash
cp .env.example .env
python -m phantom_qa.manage gen-secret     # -> PHANTOMQA_SECRET_KEY
python -m phantom_qa.manage set-password   # -> PHANTOMQA_PASSWORD_HASH
# set PHANTOMQA_ENV=production and PHANTOMQA_ALLOWED_HOSTS in .env
python -m phantom_qa.manage check          # must pass before going live
```

With `PHANTOMQA_ENV=production` the app refuses to start without a secret key
or with a plain-text password, and it requires a login for every page and API
call. Never commit `.env` — it is git-ignored, and `.env.example` is the
template.

## What to upload

| Input | Support |
|---|---|
| **DICOM file** (from PACS / CD export, JPEG-Lossless OK) | **Preferred** — full precision, metadata, protocol signature |
| **Zipped DICOM CD export** (whole `Export_...` folder as .zip) | All DICOM images inside are found automatically |
| JPG/PNG screenshot or export | Accepted, but marked **reduced precision**: 8-bit lossy data, no metadata, scale from phantom geometry only; excluded from baselines |

**Decision from the work plan:** you never transcribe numbers from the DICOM
viewer — the app reads the pixel data and metadata itself.

## The wizard (verify-then-compute workflow)

Nothing is measured from geometry you have not confirmed. Each stage shows
overlays on the actual image (zoom with the mouse wheel, pan by dragging,
window/level with the W/C sliders):

0. **Upload — choose the file, identify the scan, then confirm.** Nothing is
   uploaded until you press **Upload & analyse**, so the file and the labels can
   be set in any order and changed freely first. **Site** and **Phantom** are how
   analyses are grouped for trending, so use consistent spelling — previously
   used values appear as autocomplete suggestions, and the last values are
   remembered for the rest of the session so a batch of scans is not retyped.
   A warning appears if both are empty.

   **Forgetting a label is never a dead end.** Once an analysis is open, an
   identity bar sits above every stage showing Site / Phantom with an **Edit**
   button, so you can add or correct them at any point — during the wizard,
   after the results are computed, or later from the History table's "label"
   link. Editing is recorded in the audit trail, and the analysis immediately
   appears in the matching filters, trends and reports.
1. **A — Registration.** The app finds the phantom, resolves orientation
   (any rotation, flipped or not) and shows the fitted outline + per-side
   ruler-landmark residuals. If detection failed, click *Manual corners* and
   click the 4 phantom corners yourself.
2. **B — Patterns.** Every detected pattern is outlined and labeled: 5 line
   groups (with lp/mm), low-contrast block, wedge steps, uniformity squares,
   rulers, field edges. Verify each label is on the right object.
3. **C — Measuring points.** The actual ROIs. Click an ROI's center dot to see
   live μ/σ/n; drag to adjust (adjusted ROIs turn orange and are kept in the
   audit trail with both auto and manual positions). If a radiation-field edge
   was not auto-detected (heavily processed images often have no clear edge),
   place it manually per side.
4. **D — Dimensions.** Corner-mark distances (4 sides + both diagonals),
   per-ruler mark pitch with a linearity check of the 0.5 cm marks,
   central-line separations, and the three-way scale cross-check
   (tape pitch ↔ DICOM pixel spacings, including the implied magnification).
   Confirming this locks the mm calibration used by every later number.
5. **E — Analysis.** All tests computed from the confirmed geometry:
   - **Line patterns**: SD per group (guide metric) **and** the intensity
     profile across each group with the fitted line grid — measured pitch vs
     nominal frequency, per-line residuals in µm.
   - **Wedge**: mean per step S1…S7, monotonicity, dynamic-range ratio,
     saturation flags, plus the linear fit R² as a shape descriptor.
   - **Low contrast**: CNR per circle L1…L8 (design order), ordering sanity
     check.
   - **Uniformity**: SNR per square, ΔSNR vs mean (tolerance 20 %).
   - **Field alignment**: field-edge deviation from each side's central long
     line in mm and % of SID (tolerance ±2 %; SID entered in Stage D, default
     1000 mm).
6. **F — Save & export.** Optionally mark the analysis as **baseline** for its
   protocol signature; download the printable report, CSV, or full JSON.

### Reading the overlays

| Marking | Meaning |
|---|---|
| Red dashed square | Detected phantom outline (registration) |
| Yellow squares on the diagonal strip | Line-pair group ROIs, aligned to the measured strip axis |
| Pink dashed circle | Low-contrast object outline (10 mm, the printed disc) |
| Pink solid circle | Low-contrast **measurement** ROI (7 mm) |
| Pink dotted ring | Local **background** ring for that same circle |
| Green squares | Uniformity ROIs, centred in the printed squares |
| Orange rectangles | Wedge step ROIs (S1 top → S7 bottom) |
| Blue lines from each side | Ruler probes and field-edge probes |

Drag any ROI centre dot in Stage C to adjust it; the low-contrast ring and
outline follow their circle automatically.

## Comparison & trending

The *History & Trends* tab works off either a **filter** or a **manual
selection**, and everything on the page follows whichever is active:

- **Filter** by Site, Phantom and/or Protocol signature. The counts next to
  each option show how many analyses match.
- **Or tick individual rows** in the table. As soon as anything is ticked, the
  ticked rows win over the filter (the note under the buttons tells you which
  is in effect).

Three outputs, all respecting that selection:

| Output | What it gives you |
|---|---|
| **📊 Comprehensive comparison report** | A visual comparison of the whole selection — see below. Works equally for one phantom over time and for ten different phantoms side by side. |
| **Single-metric trend chart** | One metric across the selection, baseline starred, dashed green band at ±20 % of baseline. |
| **CSV export, long or wide** | *Long* = one row per metric per analysis (pivot-ready). *Wide* = one row per metric, one column per analysis (readable drift table). Both carry site, phantom, operator and acquisition time. |

### The comparison report

It is built from plots, not tables. Every visual identifies entries by
**Site / Phantom** (with the date appended only when the same phantom appears
more than once), and a key at the top maps each label to its full record.

- **Status grid** — every test × every analysis as a colour block.
- **"Where the differences are"** — a ranked bar chart of the metrics that vary
  most across the selection.
- **Per-pattern panels** — one small plot per object (per line-pair group, per
  low-contrast circle, per uniformity square, per wedge step), each analysis a
  labelled point, coloured by site, with the selection median as a dashed
  reference. Plus whole-curve comparisons for the wedge response and the
  low-contrast CNR series.
- **Per-pattern deviation heatmap** — a compact metric × analysis overview.
  Metrics that are effectively identical across the selection are dropped and
  counted rather than painted in misleading colour.
- **Numeric detail** — still there, but collapsed behind a toggle per section.

Two deliberate choices matter here:

1. **The reference is the median of the selection, not the first entry.** When
   you are comparing ten phantoms there is no meaningful "first", so "change
   since the first scan" would be an arbitrary framing. For a single phantom
   over time the median is still a sensible baseline, so one layout serves both.
2. **Metrics that are already percentages** (pitch deviation, ΔSNR) are compared
   in *percentage points*, never as a ratio. Their median sits near zero, so a
   relative comparison produces meaningless five-figure numbers.

Analyses are grouped by site and phantom, and ordered by **acquisition time from
the DICOM header** within a phantom — so re-analysing an old scan does not
distort the ordering.

Because pixel values of processed radiographs are not dose-proportional, all
tests remain **constancy tests**: compare against the baseline of the same
signature. Filtering by protocol signature alongside site/phantom is how you
keep that honest — the comparison report shows each analysis's signature in its
header row so a mixed series is obvious.

## Where things live

```
phantom_qa/                 the Python package
  ingest.py                 DICOM/zip/image loading + normalization + signature
  features.py               sub-pixel primitives (profiles, edges, peaks, ROI stats)
  registration.py           phantom detection, orientation, affine transform
  phantom_def.py            phantom definition (JSON) loader
  analysis/                 one module per test (propose/compute split)
  pipeline.py               stage orchestration + overlay rendering
  store.py                  SQLite persistence, labels/filtering, CSV export
  report.py                 printable HTML report, one section per pattern
  comparison_report.py      multi-analysis comparison report (trends + drift)
  config.py                 .env loading, production safety checks
  security.py               password hashing, signed sessions, CSRF, throttling
  manage.py                 admin CLI (gen-secret, set-password, check)
  cli.py                    headless batch analysis
  webapp/                   FastAPI backend + no-build JS frontend
data/phantom_definitions/msf_v1.json   calibrated phantom geometry (see below)
data/phantom_qa.sqlite3     analysis database (created on first run)
data/uploads/               original uploaded files (traceability)
tests/                      pytest suite (95 tests)
docs/ALGORITHMS.md          how every number is computed + validation results
docs/DEPLOYMENT.md          server deployment, TLS, and the security model
.env.example                configuration template (copy to .env)
```

## The phantom definition

`data/phantom_definitions/msf_v1.json` holds every object's position in the
phantom's own mm coordinate system, calibrated from **six reference scans**
covering three orientations (see its `provenance` section), with the absolute
scale anchored to the printed 5 mm tape pitch.

Two things matter about how it is used:

- **The line-pair blocks are measured in every scan, not read from the file.**
  The stored positions are only search seeds. This is what makes the ROIs land
  correctly even when a phantom differs from the reference.
- **The reference scans contain two different phantom units.** The scans split
  cleanly into two groups (detector labels `HmmEi` and `A8Tgg`) whose internal
  features sit up to ~12 mm apart, with strip angles of 45.2° and 43.0°. The
  stored values are the mean of the two; per-scan measurement absorbs the
  difference. Do **not** read the stored coordinates as design intent.

If a phantom drawing/CAD becomes available, update the nominal values —
especially `nominal_side_mm` (currently the assumed 300.0; measured
≈ 299.3–300.4 mm). New phantom versions get new definition files.

## Notes & limitations

- The viewer displays a downscaled (≤1600 px) rendering; all measurements run
  on the full-resolution raw data server-side.
- Field-edge auto-detection is honest: it requires a genuine collimation
  *step* (a linear ramp must not explain the profile as well), otherwise it
  reports *not detected* **with the reason** and asks for manual placement
  rather than guessing. On the two reference scans all four sides are
  correctly reported as not measurable — the phantom nearly fills the
  detector, so the radiation field runs past the image edge and there is no
  collimation edge to find. To make this test usable, acquire with the field
  visibly collimated inside the detector.
- `AcquisitionDeviceProcessingDescription` is part of the stored metadata; the
  reference scans differ in processing (`FB d:1.42` vs `FB d:1`), which visibly
  changes wedge R² and CNR values — compare like with like.
- **The wedge is not linear, by design of the phantom.** Its response is
  reproducibly S-shaped (linear R² ≈ 0.92 with the same residual pattern in all
  six reference scans), because the printed steps are not equal attenuation
  increments. Pass/fail is therefore decided by monotonicity and saturation;
  R² is reported as a shape descriptor with a soft 0.85 warning threshold.
  Judging the detector by the linearity of that curve would fail a perfectly
  good detector.
- One shared login, not per-user accounts. The audit trail records what was
  changed and when, not who. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
- `data/uploads/` keeps the original DICOM files. They are phantom scans, but
  the headers still carry institution and device fields — treat that folder as
  sensitive.
