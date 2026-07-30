# Algorithms & validation

How every reported number is computed, and how the implementation was
validated. Algorithm version: `phantom_qa.ALGO_VERSION` (stamped into every
stored result).

Conventions: image coordinates are (x, y) = (column, row) pixels; phantom
coordinates are mm with origin at the phantom-face center, +x right / +y up in
the guide-figure orientation. After normalization, **higher pixel value = more
attenuation** (phantom bright, direct exposure dark).

---

## 1. Ingest & normalization (`ingest.py`)

- DICOM: pixel data decoded losslessly (pylibjpeg for JPEG-Lossless), Modality
  LUT applied (`RescaleSlope/Intercept`), MONOCHROME1 inverted so polarity is
  uniform. Full header snapshot and SHA-256 of the source file stored.
- Zip archives are walked for DICOM images (viewer executables, DICOMDIR,
  HTML are skipped).
- Plain images: converted to grayscale float; flagged `reduced_precision`.
- **Protocol signature** = detector model | kV | pixel spacing | processing
  family (first tokens of `AcquisitionDeviceProcessingDescription`). Used to
  group comparable analyses for baselines/trends.

## 2. Phantom detection & registration (`registration.py`)

1. **Coarse**: 8× downsampled image → Otsu threshold → largest connected
   bright component (fill holes) = phantom face. Robust against burned-in
   text and foreign objects (smaller components).
2. **Angle**: rotating-calipers style search (0.5° coarse, 0.02° fine) for the
   rotation minimizing the bounding-box area of the component's pixels.
3. **Sub-pixel edges**: for each of the 4 sides, 25 profiles are sampled
   perpendicular to the coarse edge; the edge point is the extremum of the
   smoothed profile derivative with parabolic refinement. A total-least-squares
   line is fitted per side with two rounds of MAD outlier rejection (this
   rejects edge tabs, screws and touching foreign objects). Corners = line
   intersections.
4. **Orientation**: 8 candidate corner assignments (4 rotations × mirror) are
   fitted as affine transforms (least squares, corners ↔ nominal ±S/2 square)
   and scored on image content: wedge means must decrease from step 1 to 7
   (position only), line-pair strip must show high local SD, low-contrast
   block must be bright and smooth. Best score wins; all candidate scores are
   stored.
5. **Verification landmarks**: the central long ruler line of each side is
   located along the side normal near its predicted position; the RMS
   prediction error (mm) is reported as `residual_rms_mm`. These verify the
   fit — they do not drive it.
6. **Manual fallback**: 4 user-clicked corners replace step 1–3; orientation
   scoring still resolves the assignment, so click order does not matter
   (any walk around the square).

The transform is a full 6-DOF affine (mm→px); `mirrored` is derived from the
determinant sign (accounting for the y-down image frame), rotation from the
image of the +x axis.

## 3. Scale chain (`analysis/geometry.py`)

The definition's `side_mm = 299.5` was chosen so the measured tape pitch reads
5.000 mm — i.e. the transform's scale is anchored to the printed 5 mm pitch,
not to DICOM metadata. Per scan:

- `pitch_measured_mm`: mean regression slope of the 6 ruler-line positions
  per side (see §4).
- `k = 5.0 / pitch_measured`: residual per-scan correction; all absolute
  dimensions are reported as (T-mm × k).
- Cross-check table: pitch-anchored `absolute_mm_per_px` vs DICOM
  `ImagerPixelSpacing` (detector plane) and `PixelSpacing`. On the reference
  scans the phantom-face plane sits at magnification ≈ 1.042 vs the detector
  plane (0.1419 vs 0.148 mm/px) — a real geometric effect of the phantom
  face standing off the imaging plane; this is why tape-pitch anchoring is
  used instead of metadata.

## 4. Rulers, dimensions, field alignment (`analysis/geometry.py`)

- **Ruler probe**: profile along the side's inward normal through the ruler
  center, averaged ±3.5 mm along the edge (dilutes the pointer tick). The 6
  line positions = 6 strongest peaks (sub-pixel via intensity centroid).
  Offsets are referenced to the **locally detected face edge** (strongest
  rising step within −2…+6 mm), which decouples them from corner-fit
  residuals. Note: the edge feature itself is influenced by edge-enhancement
  processing at the ±0.5 mm level (the two reference scans differ ~0.9 mm on
  one side); mark-to-mark quantities (pitch, linearity) are robust.
- **Pitch & linearity**: linear regression position-vs-index per ruler;
  reported pitch (nominal 5.0 mm), per-line residuals and their RMS
  (`linearity_rms_mm` — the "linearity of the marks").
- **Dimensions**: corner-to-corner distances in calibrated mm (4 sides, both
  diagonals) vs assumed nominal side 300.0 mm; central-line separations
  (nominal 260.0 mm = side − 2×20 mm).
- **Field alignment**: probe outward from each side (8 mm past the face edge to
  the image border, averaged ±10 mm along the edge). A collimation border is a
  genuine **step** — direct exposure on one side, blocked beam on the other —
  whereas scatter gradients and the phantom's own carrier frame are not. The
  detector fits a two-plateau change-point model *and* a single linear ramp,
  and accepts the step only if **all** of:
  - probe length ≥ 25 mm, with ≥ 8 mm of plateau on both sides of the split
    (a "step" at the end of a short probe is an image-boundary artifact),
  - `SSE_step ≤ 0.35 × SSE_linear` (a ramp must not explain it as well),
  - ≥ 70 % of the plateau difference occurs within a few mm of the split,
  - step height > max(8× local noise, 4 % of the image dynamic range).

  Otherwise the side is reported **not detected with a reason string**, and the
  wizard asks for manual placement. Deviation = distance field edge ↔ central
  long line of that side, in mm and % of SID (user-entered, default 1000 mm;
  tolerance ±2 %).

  *On the two reference scans all four sides are correctly reported as not
  measurable*: the phantom nearly fills the detector (only 30–61 mm of image
  outside its edge) and the radiation field extends past the image, so there is
  no collimation edge to find. An earlier, looser criterion produced spurious
  "6 % of SID" failures by locking onto the scatter gradient and the phantom
  carrier frame — see `tests/test_fieldedge.py`, which locks in both directions
  (synthetic collimation edges at 25/40/60 mm are found to <2 mm; gradients and
  short probes are rejected).

## 5. Line patterns (`analysis/linepairs.py`)

- **Group centers**: block-sized matched filter — box average (≈0.85× the
  12.6 mm ROI) of the local high-pass variance, searched ±3 mm around the
  definition position. (A plain variance centroid is NOT used: the junction
  frames between groups attract it; ±3 mm cannot jump to a neighboring block
  30 mm away.)
- **SD metric** (guide): standard deviation inside the 12.6 × 12.6 mm ROI
  aligned to the strip.
- **Linearity** (first-class output): the pattern's true frequency and
  direction are measured by 2-D FFT of a 10 mm patch (band 0.85–2.6 mm⁻¹,
  peak must exceed 8× the band median); the intensity profile is sampled
  along the measured direction (~0.07 mm sampling, averaged ±4 mm along the
  lines), detrended, line peaks detected and the longest run of plausible
  spacings kept; a uniform grid `pos_i = p0 + i·pitch` is least-squares
  fitted. Reported: measured pitch (and lp/mm), deviation vs nominal
  frequency, per-line residuals (µm) and their RMS, plus the raw profile for
  plotting.
- Frequencies in the definition (2.0/1.6/1.4/1.2/1.1 lp/mm from
  bottom-left to top-right along the strip) were themselves measured from the
  reference scans (grid fit and FFT agree within 2.5 %).

## 6. Low contrast (`analysis/lowcontrast.py`)

- Block center/angle from the smoothed bright blob (PCA of the pixel
  covariance, angle snapped to the nominal −45° ± 25°).
- The rigid 4×2 circle grid (±30/±10 mm along the block axis, ±10 mm across)
  is shifted by the median offset of the 3 strongest matched-filter responses
  (dark disc: annulus mean − disc mean, ±2.5 mm search).
- Per circle: object ROI Ø 7 mm; background ROI Ø 7 mm at the same
  along-axis position on the block midline (equidistant from both rows).
  `CNR = (μ_obj − μ_bg) / √(σ_obj² + σ_bg²)` (guide formula, signed; circles
  are darker → negative).
- Circles are labeled **L1…L8 in design order** (boustrophedon starting
  top-right, as established by measurement on the reference scans); no
  nominal contrast values are claimed. Sanity check: |CNR| should broadly
  increase with level (warn otherwise).

## 7. Uniformity (`analysis/uniformity.py`)

Square outlines are re-detected per scan (three line-center probes per side of
each square, median). ROI = 30 × 30 mm centered in each square.
`SNR = μ/σ`, `ΔSNRᵢ = (SNRᵢ − SNR_avg)/SNR_avg`; pass if |ΔSNRᵢ| ≤ 20 %.

## 8. Wedge (`analysis/wedge.py`)

- x-center from the dark frame minima (3 heights, median); step boundaries
  from |gradient| peaks along the wedge axis, each searched ±3 mm around the
  **measured** nominal boundaries stored in the definition (the steps are
  ~19 mm with a ~26 mm bottom step — measured, not assumed).
- Step ROIs: 10 mm wide, boundary-to-boundary minus 3 mm margins.
- Metrics: mean/σ per step S1…S7 (position labels only), least-squares line
  mean-vs-step-index with R² (tolerance ≥ 0.95), monotonicity, and saturation
  flags (mean within 1 % of the image extremes). The axis profile is stored
  for plotting.

## 9. Statuses and constancy

Fixed tolerances follow the guide (±2 % SID, ΔSNR < 20 %, R² > 0.95);
everything else is a **constancy test** vs the baseline of the same protocol
signature (default band ±20 %, shown in trends). Absolute values of processed
radiographs are not dose-proportional; comparing across signatures is
possible but the UI keeps the groups separate on purpose.

---

## 10. Validation summary

Automated tests (`python -m pytest tests -q`, **31 tests, all passing**):

- **Synthetic ground truth**: sub-pixel edge (<0.3 px), peak centroids
  (<0.15 px), ROI statistics exact, TLS/affine round-trips exact; rect
  detection <1 px corner error at 0°–12° rotation; grating pitch recovery
  <1 % error incl. a 6° direction mismatch (2-D FFT path); field-edge
  detection finds synthetic collimation edges at 25/40/60 mm to <2 mm on all
  four sides and rejects scatter gradients and too-short probes.
- **Reference scans** (both exposures of 2026-07-27): rotation ≈180°
  resolved, not mirrored; tape pitch 5.00 ± 0.05 mm; mean side 299.35 /
  299.88 mm; central-line separations 260 ± 1 mm; all five line groups
  within 4 % of nominal frequency with grid residual RMS ≤ 0.03 mm; wedge
  monotonic; uniformity |ΔSNR| < 4 %; low-contrast ordering monotone.
- **Cross-scan consistency**: line-group pitches agree between the two
  exposures to <1 % (typically 0.1 %).
- **Rotation invariance**: a 90°-rotated copy of scan 1 reproduces pitch,
  dimensions and group frequencies within the same tolerances.

Known limitations:

- Field-edge detection is genuinely limited by image processing; the manual
  placement path exists for that reason.
- Mark-to-edge distances (first ruler line, side lengths) carry a
  processing-dependent uncertainty of ~±0.5 mm because the "edge" feature
  itself shifts with edge enhancement; pitch and mark-to-mark metrics do not.
- The phantom definition encodes measured geometry of THIS phantom print
  (v1). Re-calibrate for new prints: run the pipeline, inspect Stage B/C
  overlays, and update the JSON (`provenance` documents the procedure values).
