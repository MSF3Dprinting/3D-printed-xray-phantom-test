# MSF Phantom QA

Analysis app for X-ray scans of the MSF 3D-printed QA phantom. Upload a DICOM
scan, verify what the program detected in a step-by-step wizard, and get
rigorous, reproducible QA metrics with full traceability â€” plus CSV/JSON export
and trend comparison across scans and phantoms.

Implements the tests of the *MSF X-ray QA phantom acquisition and analysis
guide*: X-ray/light-field alignment, spatial-resolution line patterns (SD +
**line-pattern linearity plots**), low-contrast CNR, uniformity SNR, and the
dynamic-range wedge â€” and adds dimension verification from the corner marks and
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

**Deploying on a server?** Read [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) â€” it
covers nginx + gunicorn, sub-path mounting, systemd, and the security model.
Short version:

```bash
cp .env.example .env
python -m phantom_qa.manage gen-secret          # -> PHANTOMQA_SECRET_KEY
python -m phantom_qa.manage set-password        # -> user login
python -m phantom_qa.manage set-admin-password  # -> delete + validate
# set PHANTOMQA_ENV=production, PHANTOMQA_ALLOWED_HOSTS,
#     PHANTOMQA_ROOT_PATH=/x-ray  (if not at the domain root)
python -m phantom_qa.manage check               # must exit 0 before going live
gunicorn -c gunicorn.conf.py phantom_qa.webapp.main:app
```

With `PHANTOMQA_ENV=production` the app refuses to start without a secret key
or with a plain-text password, and requires a login for every page and API call.
Never commit `.env` â€” it is git-ignored, and `.env.example` is the template.

## Who can do what

| Level | Credential | Can do |
|---|---|---|
| **User** | the login password | Upload, run the wizard, edit labels, read reports, verify integrity, export |
| **Admin** | additionally the **administrator password** | **Delete** an analysis, **validate** one |

The admin password is entered per action, not at sign-in, so an admin works as
an ordinary user and supplies it only when deleting or signing off. Every route
is covered by `tests/test_authorization.py`, which fails if a new endpoint is
added without being classified as public / user / admin.

## What to upload

| Input | Support |
|---|---|
| **DICOM file** (from PACS / CD export, JPEG-Lossless OK) | **Preferred** â€” full precision, metadata, protocol signature |
| **Zipped DICOM CD export** (whole `Export_...` folder as .zip) | All DICOM images inside are found automatically |
| JPG/PNG screenshot or export | Accepted, but marked **reduced precision**: 8-bit lossy data, no metadata, scale from phantom geometry only; excluded from baselines |

**Decision from the work plan:** you never transcribe numbers from the DICOM
viewer â€” the app reads the pixel data and metadata itself.

## The wizard (verify-then-compute workflow)

Nothing is measured from geometry you have not confirmed. Each stage shows
overlays on the actual image (zoom with the mouse wheel, pan by dragging,
window/level with the W/C sliders):

0. **Upload â€” choose the file, identify the scan, then confirm.** Nothing is
   uploaded until you press **Upload & analyse**, so the file and the labels can
   be set in any order and changed freely first. **Site** and **Phantom** are how
   analyses are grouped for trending, so use consistent spelling â€” previously
   used values appear as autocomplete suggestions, and the last values are
   remembered for the rest of the session so a batch of scans is not retyped.
   A warning appears if both are empty.

   **Forgetting a label is never a dead end.** Once an analysis is open, an
   identity bar sits above every stage showing Site / Phantom with an **Edit**
   button, so you can add or correct them at any point â€” during the wizard,
   after the results are computed, or later from the History table's "label"
   link. Editing is recorded in the audit trail, and the analysis immediately
   appears in the matching filters, trends and reports.
1. **A â€” Registration.** The app finds the phantom, resolves orientation
   (any rotation, flipped or not) and shows the fitted outline + per-side
   ruler-landmark residuals. If detection failed, click *Manual corners* and
   click the 4 phantom corners yourself.
2. **B â€” Patterns.** Every detected pattern is outlined and labeled: 5 line
   groups (with lp/mm), low-contrast block, wedge steps, uniformity squares,
   rulers, field edges. Verify each label is on the right object.
3. **C â€” Measuring points.** The actual ROIs. Click an ROI's center dot to see
   live Î¼/Ïƒ/n; drag to adjust (adjusted ROIs turn orange and are kept in the
   audit trail with both auto and manual positions). If a radiation-field edge
   was not auto-detected (heavily processed images often have no clear edge),
   place it manually per side.
4. **D â€” Dimensions.** Corner-mark distances (4 sides + both diagonals),
   per-ruler mark pitch with a linearity check of the 0.5 cm marks,
   central-line separations, and the three-way scale cross-check
   (tape pitch â†” DICOM pixel spacings, including the implied magnification).
   Confirming this locks the mm calibration used by every later number.
5. **E â€” Analysis.** All tests computed from the confirmed geometry:
   - **Line patterns**: SD per group (guide metric) **and** the intensity
     profile across each group with the fitted line grid â€” measured pitch vs
     nominal frequency, per-line residuals in Âµm.
   - **Wedge**: mean per step S1â€¦S7, monotonicity, dynamic-range ratio,
     saturation flags, plus the linear fit RÂ² as a shape descriptor.
   - **Low contrast**: CNR per circle L1â€¦L8 (design order), ordering sanity
     check.
   - **Uniformity**: SNR per square, Î”SNR vs mean (tolerance 20 %).
   - **Field alignment**: field-edge deviation from each side's central long
     line in mm and % of SID (tolerance Â±2 %; SID entered in Stage D, default
     1000 mm).
6. **F â€” Save & export.** Optionally mark the analysis as **baseline** for its
   protocol signature; download the printable report, CSV, or full JSON; and
   **verify the source file** against its recorded SHA-256.

## Validation â€” the administrator's sign-off

The measurements say what the phantom looks like; a person still has to decide
whether it is **accepted**. That decision is recorded on the analysis:

| State | Meaning |
|---|---|
| **pending review** | Nobody has ruled yet (the state of every new analysis). |
| **validated** | The phantom and this measurement are accepted. |
| **conditionally validated** | Accepted with reservations â€” state them in the comment. |
| **not validated** | Rejected â€” state why in the comment. |

Recording a decision requires the **administrator password** (the same
credential as deletion â€” it is the other call an ordinary user must not make),
plus the **name of the person approving** and an optional comment. The name is
stored separately from the password on purpose: a shared credential proves the
*right* to sign off, it cannot say *who* did.

Set it from the identity bar (**Setâ€¦**), Stage F, or the **validate** link in
History. A ruling can be changed or withdrawn later; every change â€” including
refused attempts â€” is written to `logs/audit.log`.

Where it shows up:

- **At the top of the printable report**, colour-coded, with the approver's
  name, the date and the comment.
- **In the comparison report** â€” a dedicated VALIDATION row in the status grid
  and a column in the key table.
- **In History** â€” a column, plus a filter (including "pending review", to find
  what still needs a decision).
- **In both CSV exports**.

## Your data and code updates

**Updating the code never costs you your analyses, and you never have to
re-upload a scan.** `data/` is git-ignored so `git pull` cannot touch it, the
schema migrates in place on startup, and the app only ever deletes an analysis
through the admin-gated delete.

What *can* go stale is the numbers: stored results are what the algorithm
produced on the day they were computed, and they deliberately do not change
under you. Every analysis records `algo_version` and `pdef_version`, so:

```bash
python -m phantom_qa.manage outdated             # which predate the current version
python -m phantom_qa.manage reanalyze --dry-run  # preview a recompute
python -m phantom_qa.manage reanalyze            # apply, from the kept source files
```

Default mode reuses the geometry you confirmed (manual ROI adjustments survive)
and suits an **algorithm** change; `--full` re-detects everything and suits a
**phantom definition** change. Labels, validation, approver, baseline and the
audit trail are always preserved, and signed-off analyses are skipped unless you
pass `--include-validated`.

Full explanation: [docs/DATA_LIFECYCLE.md](docs/DATA_LIFECYCLE.md).

## Source file integrity (SHA-256)

Every analysis records a SHA-256 fingerprint of the exact file it was computed
from, and keeps a copy of that file. This ties the numbers to their input and
detects silent corruption, a wrong backup restore, or the wrong scan attached to
a record.

- The **report** shows the hash and re-checks it as it is generated.
- **Verify source file** in Stage F, or the **verify** link in History,
  re-hashes the stored copy on demand.
- `python -m phantom_qa.manage verify --all` does the same from the command
  line and exits non-zero on failure, so it can be scheduled.
- To check it yourself: `Get-FileHash -Algorithm SHA256 <file>` (PowerShell) or
  `sha256sum <file>` (Linux/macOS) must reproduce the value exactly.

Full explanation, including what to do about a mismatch:
[docs/INTEGRITY.md](docs/INTEGRITY.md).

## Deleting analyses

Because several people share one installation, deletion is deliberately hard to
do by accident. It requires a **separate administrator password** (not the
everyday login) *and* typing the analysis id back to confirm, and it is
throttled. If no administrator password is configured, deletion is refused
outright â€” the default. Every attempt, successful or not, is written to
`logs/audit.log` together with the site, phantom and SHA-256 of what was
removed. Set it up with
`python -m phantom_qa.manage set-admin-password`.

## Logs

Three rotating files under `logs/`: `phantomqa.log` (all activity),
`errors.log` (warnings and above), and `audit.log` (who did what to the data â€”
sign-ins, uploads, computes, label edits, integrity checks, deletions).
Passwords and tokens are redacted. See
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#logging) for retention guidance.

### Reading the overlays

| Marking | Meaning |
|---|---|
| Red dashed square | Detected phantom outline (registration) |
| Yellow squares on the diagonal strip | Line-pair group ROIs, aligned to the measured strip axis |
| Pink dashed circle | Low-contrast object outline (10 mm, the printed disc) |
| Pink solid circle | Low-contrast **measurement** ROI (7 mm) |
| Pink dotted ring | Local **background** ring for that same circle |
| Green squares | Uniformity ROIs, centred in the printed squares |
| Orange rectangles | Wedge step ROIs (S1 top â†’ S7 bottom) |
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
| **ðŸ“Š Comprehensive comparison report** | A visual comparison of the whole selection â€” see below. Works equally for one phantom over time and for ten different phantoms side by side. |
| **Single-metric trend chart** | One metric across the selection, baseline starred, dashed green band at Â±20 % of baseline. |
| **CSV export, long or wide** | *Long* = one row per metric per analysis (pivot-ready). *Wide* = one row per metric, one column per analysis (readable drift table). Both carry site, phantom, operator and acquisition time. |

### The comparison report

It is built from plots, not tables. Every visual identifies entries by
**Site / Phantom** (with the date appended only when the same phantom appears
more than once), and a key at the top maps each label to its full record.

- **Status grid** â€” every test Ã— every analysis as a colour block.
- **"Where the differences are"** â€” a ranked bar chart of the metrics that vary
  most across the selection.
- **Per-pattern panels** â€” one small plot per object (per line-pair group, per
  low-contrast circle, per uniformity square, per wedge step), each analysis a
  labelled point, coloured by site, with the selection median as a dashed
  reference. Plus whole-curve comparisons for the wedge response and the
  low-contrast CNR series.
- **Per-pattern deviation heatmap** â€” a compact metric Ã— analysis overview.
  Metrics that are effectively identical across the selection are dropped and
  counted rather than painted in misleading colour.
- **Numeric detail** â€” still there, but collapsed behind a toggle per section.

Two deliberate choices matter here:

1. **The reference is the median of the selection, not the first entry.** When
   you are comparing ten phantoms there is no meaningful "first", so "change
   since the first scan" would be an arbitrary framing. For a single phantom
   over time the median is still a sensible baseline, so one layout serves both.
2. **Metrics that are already percentages** (pitch deviation, Î”SNR) are compared
   in *percentage points*, never as a ratio. Their median sits near zero, so a
   relative comparison produces meaningless five-figure numbers.

Analyses are grouped by site and phantom, and ordered by **acquisition time from
the DICOM header** within a phantom â€” so re-analysing an old scan does not
distort the ordering.

Because pixel values of processed radiographs are not dose-proportional, all
tests remain **constancy tests**: compare against the baseline of the same
signature. Filtering by protocol signature alongside site/phantom is how you
keep that honest â€” the comparison report shows each analysis's signature in its
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
  logging_setup.py          rotating app / error / audit logs, secret redaction
  reanalyze.py              recompute stored analyses from the kept source files
  manage.py                 admin CLI (secrets, verify, backup, outdated, reanalyze)
  cli.py                    headless batch analysis
  webapp/                   FastAPI backend + no-build JS frontend
data/phantom_definitions/msf_v1.json   calibrated phantom geometry (see below)
data/phantom_qa.sqlite3     analysis database (created on first run)
data/uploads/               original uploaded files (traceability)
logs/                       rotating application, error and audit logs
gunicorn.conf.py            production server config (workers, timeouts)
tests/                      pytest suite (315 tests, incl. 147 authorization)
docs/ALGORITHMS.md          how every number is computed + validation results
docs/DEPLOYMENT.md          server deployment, TLS, security, deletion, logging
docs/DATA_LIFECYCLE.md      what survives a code update, and how to re-analyse
docs/INTEGRITY.md           what the SHA-256 is for and how to verify it
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
  features sit up to ~12 mm apart, with strip angles of 45.2Â° and 43.0Â°. The
  stored values are the mean of the two; per-scan measurement absorbs the
  difference. Do **not** read the stored coordinates as design intent.

If a phantom drawing/CAD becomes available, update the nominal values â€”
especially `nominal_side_mm` (currently the assumed 300.0; measured
â‰ˆ 299.3â€“300.4 mm). New phantom versions get new definition files.

## Notes & limitations

- The viewer displays a downscaled (â‰¤1600 px) rendering; all measurements run
  on the full-resolution raw data server-side.
- Field-edge auto-detection is honest: it requires a genuine collimation
  *step* (a linear ramp must not explain the profile as well), otherwise it
  reports *not detected* **with the reason** and asks for manual placement
  rather than guessing. On the two reference scans all four sides are
  correctly reported as not measurable â€” the phantom nearly fills the
  detector, so the radiation field runs past the image edge and there is no
  collimation edge to find. To make this test usable, acquire with the field
  visibly collimated inside the detector.
- `AcquisitionDeviceProcessingDescription` is part of the stored metadata; the
  reference scans differ in processing (`FB d:1.42` vs `FB d:1`), which visibly
  changes wedge RÂ² and CNR values â€” compare like with like.
- **The wedge is not linear, by design of the phantom.** Its response is
  reproducibly S-shaped (linear RÂ² â‰ˆ 0.92 with the same residual pattern in all
  six reference scans), because the printed steps are not equal attenuation
  increments. Pass/fail is therefore decided by monotonicity and saturation;
  RÂ² is reported as a shape descriptor with a soft 0.85 warning threshold.
  Judging the detector by the linearity of that curve would fail a perfectly
  good detector.
- One shared login, not per-user accounts. The audit trail records what was
  changed and when, not who. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
- `data/uploads/` keeps the original DICOM files. They are phantom scans, but
  the headers still carry institution and device fields â€” treat that folder as
  sensitive.

