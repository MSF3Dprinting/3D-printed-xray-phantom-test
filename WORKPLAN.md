# MSF X-ray Phantom Analysis App — Work Plan

Status: **implemented, rev. 3** (2026-07-30).
Rev. 2 restructured the workflow into user-gated verification stages.
Rev. 3 records the *no-material-assumptions* correction and marks the plan as
built — see [README.md](README.md) for the user guide and
[docs/ALGORITHMS.md](docs/ALGORITHMS.md) for the computation and validation
detail.

---

## 1. What we have (data survey findings)

### Sample scans
Two exposures of the phantom, acquired 2026-07-27 on a **Philips DigitalDiagnost C50**
(Eleva station), delivered in two export formats:

| Export folder | Content | Verdict |
|---|---|---|
| `Export_2026-07-27_09-59-56_1` | JPG renders, 8-bit RGB, lossy, no metadata | Fallback only |
| `Export_2026-07-27_10-00-05_1` | DICOM CD (DICOMDIR + 2 DX images) | **Primary data source** |

DICOM properties (both images):
- 2874 × 2869 px, 12-bit stored in 16-bit, MONOCHROME2, **JPEG Lossless** (no quality loss)
- `PixelSpacing` = 0.1407 mm, `ImagerPixelSpacing` = 0.148 mm (detector plane)
- 77 kVp, ~3 mAs (2970 / 3040 µAs), `RelativeXRayExposure` 251 / 255
- Philips UNIQUE post-processing; parameters in
  `AcquisitionDeviceProcessingDescription` (`FB d:1.42` vs `FB d:1` — the two images differ!)
- No SID tag → SID is user-supplied (default 100 cm)
- Burned-in text markers; one scan has a foreign object at the field edge

### Phantom layout (from guide + scans, all positions since measured)
Square 3D-printed slab, measured side ≈ 299.4–299.9 mm, containing:
1. **Tape measures** at the middle of each of the 4 sides — alternating short/long
   lines, 5 mm pitch, first line 10 mm from the edge, central long line = 3rd mark
2. **Corner marks** at the 4 corners
3. **5 uniformity squares** (50 mm printed outlines): 4 near corners + 1 center
4. **Line-pair objects** (tungsten filament, serpentine): 5 groups on a diagonal
   strip at 2.0 / 1.6 / 1.4 / 1.2 / 1.1 lp/mm running bottom-left → top-right
5. **Low-contrast object**: rounded rectangle with 8 circles Ø 10 mm in a 4×2 grid
6. **Attenuation wedge**: 7 steps in a framed column (~19 mm each, ~26 mm bottom step)

Confirmed by the data: the sample scans are rotated ~180° relative to the guide's
reference figure — **orientation invariance is a real requirement, not hypothetical.**

### Requested analyses mapped to guide tests

| User request | Guide test | Metric (as implemented) |
|---|---|---|
| Linearity of line pattern | Spatial resolution | Profile per group + fitted line grid: measured pitch, Δ vs nominal, per-line residuals (µm); plus SD per ROI |
| Low contrast pattern | Low-contrast resolution | CNR per circle L1…L8 (design order) |
| High contrast wedge pattern | Dynamic range | Mean per step S1…S7, linear fit vs step index, R², monotonicity, saturation |
| Corner marks | Geometry verification | Corner-to-corner sides + both diagonals vs nominal |
| Dimensions via side marks | Alignment + dimensions | Per-ruler pitch & mark linearity, central-line separations, field-edge deviation (mm, % SID) |
| — (in guide, included) | Uniformity | SNR per square, ΔSNRᵢ vs mean |

---

## 2. Key design decisions

### D1 — Fully automatic DICOM ingestion; no manual export from the viewer app
The DICOM files contain everything needed. The app reads DICOM (single file, folder,
or zipped CD export with DICOMDIR) and proposes all detections itself. The user never
transcribes numbers from Eleva/CD viewer. **Built** — `ingest.py`.

### D2 — Staged workflow: the program proposes, the user verifies each stage
Nothing is measured from geometry the user has not confirmed. Six gates
(A registration → B patterns → C measuring points → D dimensions → E analysis →
F save/export), each with overlays, adjust-and-recompute, and an audit trail.
**Built** — `webapp/`, `pipeline.py`.

### D3 — Constancy-based QA with baselines
Pixel values are display-processed, so absolute values are not dose-proportional.
Each phantom × detector × protocol signature gets a baseline; comparisons are grouped
by signature so different parameters/machines/processing are never silently mixed.
**Built** — `store.py`, trends UI.

### D4 — Scale is self-checked and user-verified, never assumed
Three independent scale sources cross-compared at Stage D: DICOM spacings, measured
tape pitch (5 mm nominal), and phantom dimensions. The absolute scale is **anchored to
the tape pitch**, because the phantom face sits at magnification ≈1.042 vs the detector
plane — a real effect the metadata alone would hide. **Built** — `analysis/geometry.py`.

### D5 — JPG/PNG fallback = "reduced-precision mode"
Accepted but watermarked *reduced precision* and excluded from baselines. **Built.**

### D6 (rev. 3) — No material assumptions anywhere
The phantom is experimental and **not** made of the materials a standard phantom would
use. The guide's nominal values (mm Cu equivalents for the wedge, % contrast for the
circles) are therefore **not** used and not displayed:
- wedge steps are labeled **S1…S7 by position** (top → bottom); the wedge fit is
  mean-value vs **step index**;
- low-contrast circles are labeled **L1…L8 in design order** of increasing measured
  contrast;
- line-pair frequencies in the definition were **measured** from the reference scans,
  not taken on faith (grid fit and 2-D FFT agree within 2.5 % of 1.1–2.0 lp/mm).
Only two nominals remain, both explicit and flagged in the UI: the 5.0 mm tape pitch
(printed geometry, used for scale) and the assumed 300 mm phantom side (no drawing
available). **Built.**

---

## 3. Architecture (as built)

```
Browser (offline SPA, no build step)
  Wizard A–F: canvas viewer + overlays + ROI drag + live μ/σ + plots
  History & Trends: table, CSV of a selection, per-metric trend charts
        │  REST JSON + PNG renderings
FastAPI backend (local, single process)
  ingest      pydicom + pylibjpeg, zip/DICOMDIR walk, LUT + polarity, metadata
  registration  phantom detection, 8-orientation resolution, affine + landmarks
  analysis/   geometry · linepairs · lowcontrast · uniformity · wedge
              (each: propose(ctx) → editable geometry, compute(ctx, geom) → results)
  pipeline    stage orchestration, JSON sanitation, overlay rendering
  store       SQLite (analyses, geometry, results, audit, baselines) + CSV flattening
  report      self-contained printable HTML (inlined charts + overlay)
  cli         headless batch analysis for validation/scripting
```

Stack: Python 3.13, FastAPI + uvicorn, numpy/scipy/scikit-image, pydicom + pylibjpeg,
matplotlib (report charts), SQLite. Frontend: vanilla JS + `<canvas>`. No internet
dependency at any point.

---

## 4. User-gated analysis workflow — as built

| Stage | What the user sees | What they can change |
|---|---|---|
| **A Registration** | phantom outline, rotation, mirrored flag, scale, per-side landmark residuals | click 4 corners manually |
| **B Patterns** | every pattern outlined + labeled by identity (`G2.0`, `S4`, `L7`, `ruler top`…), detection status per object | go back to A; outlines re-derived |
| **C Measuring points** | all ROIs; click a center for live μ/σ/n | drag any ROI (turns orange, audited: auto + manual position kept); place field edges manually per side |
| **D Dimensions** | corner dimensions table, per-ruler pitch + mark linearity, central-line separations, 3-way scale cross-check, field deviations | SID input, recompute |
| **E Analysis** | per-test cards with pass/warn/fail, wedge fit chart, CNR bars, **per-group profile + linearity plots** | back to any earlier stage |
| **F Save & export** | stored record + audit trail | mark as baseline; report / CSV / JSON |

## 5. Rigor & precision measures — as built

- Sub-pixel localization throughout: error-function/derivative edge fits, intensity
  centroids for line peaks, parabolic refinement, total-least-squares line fits with
  MAD outlier rejection. Precision well below the 0.148 mm pixel.
- Statistics on raw 12-bit values in float64; ROI pixel counts always reported.
- Deterministic: same file + same confirmations → identical results; algorithm
  version stamped into every stored result.
- Detectors are honest about failure: field edges that cannot be resolved report
  *not detected with a reason* and request manual placement instead of guessing.
  On the reference scans this correctly reports all four sides as not
  measurable — the phantom nearly fills the detector, so no collimation edge is
  in the image. (An earlier looser criterion produced spurious ±6 %-of-SID
  failures by locking onto the scatter gradient; `tests/test_fieldedge.py` now
  locks in both directions.)
- Manual adjustments audited (auto + manual value, timestamp, stage).
- **Validation: 31 automated tests pass** — synthetic ground truth (sub-pixel edge
  <0.3 px, peak centroid <0.15 px, exact ROI stats, rect corners <1 px over 0–12°
  rotation, grating pitch <1 % incl. 6° direction error, collimation edges found
  to <2 mm while gradients are rejected), both reference scans
  (pitch 5.00 ±0.05 mm, sides 299.35/299.88 mm, separations 260 ±1 mm, all 5 groups
  within 4 % of nominal with ≤0.03 mm grid RMS, uniformity |ΔSNR| <4 %), cross-scan
  agreement <1 % on group pitches, and 90°-rotation invariance.

## 6. Handling scan variability — as built

| Variation | Handling |
|---|---|
| Rotation / flip / off-center | 8-orientation scoring + affine landmark fit, user-verified at A–B (proven necessary: samples are 180° vs guide) |
| Different detector / pixel spacing | geometry in mm via registration; scale triple-check confirmed at D |
| Different kV / mAs / machine | recorded into the protocol signature; trends grouped by signature |
| Different post-processing | recorded; separate signature group (the two samples differ: `FB d:1.42` vs `FB d:1`, visibly changing wedge R² and CNR) |
| Foreign objects / burned-in text | excluded by largest-component detection + MAD edge rejection; visible to the user at A |
| Collimation differences | field detection independent of phantom detection, per side, manual fallback |
| JPG-only input | reduced-precision mode |

## 7. Export & comparison — as built

- **Per-scan**: printable HTML report (summary, metadata, all metrics with baseline
  deltas, wedge/CNR/linearity charts, overlay snapshot, audit trail — all inlined,
  no external files); full JSON; flat CSV.
- **Cross-scan**: history table → select N analyses → single long-format CSV
  (`analysis_id, created_at, source, signature, is_baseline, test, object, metric,
  value, unit, status`), pivot-ready; per-metric trend charts with the baseline
  starred and a ±20 % constancy band.

## 8. Implementation status

| Phase | Deliverable | Status |
|---|---|---|
| **P1 Core & registration** | ingest, detection, registration, phantom definition | **done** — both samples registered at 180°, landmark RMS 0.13 / 0.55 mm |
| **P2 Analysis modules** | 5 test modules incl. line-pattern linearity, overlays, unit tests | **done** — 26 tests pass |
| **P3 Wizard web app** | Stages A–F with confirm/adjust gates, live recompute, plots | **done** — full API smoke test green |
| **P4 Persistence & trending** | SQLite, baselines, history/trends, CSV/JSON/report | **done** |
| **P5 Hardening & docs** | README, ALGORITHMS, reduced-precision mode, manual fallbacks | **done** (trusted-auto mode intentionally not added — gated mode is the point) |

## 9. Open questions (unchanged, none blocking)

1. **Phantom drawing/CAD** — would replace the assumed 300 mm nominal side and let
   dimension deviations be judged against design intent rather than a calibrated
   reference. Everything else in the definition is measured.
2. **Tolerances** — guide values are wired in (±2 % SID, ΔSNR < 20 %, R² > 0.95);
   constancy bands default to ±20 % and should be revisited after a few weeks of data.
3. **SID** — absent from these headers; entered per analysis (default 1000 mm).
4. **Line-pattern linearity** — implemented as fitted-grid pitch + per-line residuals
   within each group's confirmed ROI. Confirm this matches the intent.
5. **Deployment** — local single-user app; a shared team server would add auth and
   concurrent-DB considerations.
