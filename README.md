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
   - **Wedge**: mean per step S1…S7, linear fit vs step index, R², monotonicity,
     saturation flags.
   - **Low contrast**: CNR per circle L1…L8 (design order), ordering sanity
     check.
   - **Uniformity**: SNR per square, ΔSNR vs mean (tolerance 20 %).
   - **Field alignment**: field-edge deviation from each side's central long
     line in mm and % of SID (tolerance ±2 %; SID entered in Stage D, default
     1000 mm).
6. **F — Save & export.** Optionally mark the analysis as **baseline** for its
   protocol signature; download the printable report, CSV, or full JSON.

## Comparison & trending

*History & Trends* tab:
- table of all analyses (open / report / delete; ⚠ marks reduced precision),
- multi-select → **one CSV** in long format (`analysis_id, test, object,
  metric, value, unit, status`) — pivot-ready in Excel,
- trend chart per metric, grouped by **protocol signature**
  (detector model + kV + pixel spacing + processing family). Baseline is
  starred; the dashed band is ±20 % of baseline (constancy default).

Because pixel values of processed radiographs are not dose-proportional, all
tests are **constancy tests**: compare against the baseline of the same
signature. The app warns (via separate signature groups) when scans were
acquired with different parameters, machines, or processing.

## Where things live

```
phantom_qa/                 the Python package
  ingest.py                 DICOM/zip/image loading + normalization + signature
  features.py               sub-pixel primitives (profiles, edges, peaks, ROI stats)
  registration.py           phantom detection, orientation, affine transform
  phantom_def.py            phantom definition (JSON) loader
  analysis/                 one module per test (propose/compute split)
  pipeline.py               stage orchestration + overlay rendering
  store.py                  SQLite persistence + CSV flattening
  report.py                 printable HTML report
  cli.py                    headless batch analysis
  webapp/                   FastAPI backend + no-build JS frontend
data/phantom_definitions/msf_v1.json   calibrated phantom geometry (see below)
data/phantom_qa.sqlite3     analysis database (created on first run)
data/uploads/               original uploaded files (traceability)
tests/                      pytest suite (synthetic ground truth + sample scans)
docs/ALGORITHMS.md          how every number is computed + validation results
```

## The phantom definition

`data/phantom_definitions/msf_v1.json` holds every object's position in the
phantom's own mm coordinate system. It was **calibrated from the two reference
scans of 2026-07-27** (see its `provenance` section), with the absolute scale
anchored to the printed 5 mm tape pitch. If a phantom drawing/CAD becomes
available, update the nominal values there — especially `nominal_side_mm`
(currently the assumed 300.0; measured ≈ 299.4–299.9 mm). New phantom versions
get new definition files.

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
  two reference scans already differ in processing (`FB d:1.42` vs `FB d:1`),
  which visibly changes wedge R² and CNR values — compare like with like.
- Single-user local app. Concurrent multi-user deployment would need auth and
  a served database (see WORKPLAN.md, open question 5).
