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

Block positions are **measured in every scan**; the definition supplies only
nominal seeds. This is what fixed the misaligned ROIs — stored coordinates
cannot track a phantom that differs from the reference by 6–12 mm.

- **Periodicity map, not variance.** A local-variance detector locks onto the
  *wedge*: its dark steps carry far more quantum noise than the tungsten
  patterns carry signal. Noise is broadband and a printed grating is a sharp
  spectral peak, so the discriminator is the **peak-to-median energy ratio in
  the 1.0–2.4 mm⁻¹ band**, computed on 10 mm windows at 2 mm stride over a
  corridor around the nominal strip. Blobs with ratio > 12 are candidates.
- **Impostor rejection.** Uniformity-square outlines, ruler marks and the wedge
  frame also produce band peaks. Two facts kill them: the real groups all lie on
  one strip, so they (a) share a modulation direction (circular median, ±12°
  kept) and (b) are collinear (MAD-based rejection about the fitted centre
  line). Before this filter, one group per scan was being hijacked by an
  axis-aligned impostor.
- **Angle convention.** The rectified corridor's row index runs along
  *decreasing* phantom y, so the row-axis FFT frequency is the negative of the
  phantom-y frequency. Without that sign flip every measured angle is mirrored
  about the x axis — invisible at exactly 45°, but a ~5° error elsewhere
  (`tests/test_linepairs_detection.py` locks this at 30/60/120°).
- **Strip axis** is fitted through the detected block centres, not derived from
  the modulation direction — that avoids having to assume whether the printed
  lines run along or across the strip.
- **Sub-window centring.** The periodicity grid is coarse, so each centre is
  refined by band-passing the region at that block's own measured frequency and
  taking an iterated, **isotropic** energy centre of mass over a disc. A box
  filter is not used: it biases a block rotated ~45° to the sampling grid.
- **Identity assignment**: blocks are ordered along the strip and matched to the
  definition's groups, validated by correlating measured against nominal
  frequency; a block whose frequency contradicts its assigned identity is
  dropped rather than reported.
- **SD metric** (guide): standard deviation inside the 12.6 × 12.6 mm ROI.
- **Linearity** (first-class output): the pattern's frequency and direction come
  from a 2-D FFT of a 10 mm patch (peak must exceed 8× the band median); the
  profile is sampled along that direction (~0.07 mm steps, averaged ±4 mm along
  the lines), detrended, and line peaks detected. The run of peaks is selected
  against the **measured median gap** (±18 %), not the nominal — block-edge
  artefacts produce short spurious gaps that a nominal-only window admits and
  that drag the fitted pitch down by several percent. A uniform grid
  `pos_i = p0 + i·pitch` is then least-squares fitted with one MAD-based outlier
  pass. Reported: measured pitch and lp/mm, deviation vs nominal, per-line
  residuals (µm) and RMS, plus the raw profile.

Measured across all six reference scans, the pitch deviations are stable to
~0.1 %: +0.8 %, −2.0 %, +1.4 %, −2.4 %, +1.9 % for 2.0/1.6/1.4/1.2/1.1 lp/mm.
That is reproducible print geometry, not measurement noise.

## 6. Low contrast (`analysis/lowcontrast.py`)

- Block center/angle from the smoothed bright blob (PCA of the pixel
  covariance, angle snapped to the nominal −45° ± 25°).
- The rigid 4×2 circle grid (±30/±10 mm along the block axis, ±10 mm across)
  is shifted by the median offset of the 3 strongest matched-filter responses
  (dark disc: annulus mean − disc mean, ±2.5 mm search).
- Per circle: object ROI Ø 7 mm; background is the **annulus around that same
  circle** (Ø 12 → 16 mm). `CNR = (μ_obj − μ_bg) / √(σ_obj² + σ_bg²)` (guide
  formula, signed; circles are darker → negative).

  The background used to be a disc on the block midline. That was wrong twice
  over: the midline sits between the two circle rows where the real objects are,
  so the eight background ROIs overlapped into four unexplained blobs in the
  middle of the block, and being far from its own circle the ROI picked up the
  block's brightness gradient. A concentric ring is unambiguously tied to its
  own object, is local, and cannot collide with a neighbour.
- Grid geometry measured on the reference scans: circles at
  u = ±10, ±30 mm and v = +9.4 / −10.2 mm in block coordinates; block
  84.5 × 44.5 mm at −45.1°.
- Circles are labeled **L1…L8 in design order** (boustrophedon starting
  top-right — confirmed by measurement: contrast rises monotonically along that
  path, ~0.33 % to ~1.37 % at 77 kV). No nominal contrast values are claimed.
  Sanity check: |CNR| should broadly increase with level (warn otherwise).

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
- Metrics: mean/σ per step S1…S7 (position labels only), monotonicity,
  dynamic-range ratio (S1/S7), the axis profile, and a least-squares fit of
  mean-vs-step-index with R² and per-step residuals as % of span.

**Why R² is not the pass criterion.** Measured on all six reference scans the
response is reproducibly S-shaped: R² ≈ 0.918–0.953 with a near-identical
residual pattern (−13, −1, +9, +10, +7, +2, −15 % of span) and step-to-step
ratios from 1.03 to 2.0. The printed steps are simply not equal increments of
attenuation. A log-linear fit is worse (R² ≈ 0.77–0.81), so this is not a
missing exponential either. Requiring R² > 0.95 on a linear fit therefore fails
a perfectly good detector because of the phantom's own geometry. The criteria
are now:

  - **fail** — non-monotonic response, or any saturated step
  - **warn** — R² below `wedge_r2_min` (default 0.85)
  - otherwise **pass**, with the per-step means trended against the baseline

- **Saturation** is tested against the detector's full scale (2^`BitsStored` − 1,
  or the next power of two above the image maximum when the tag is absent), not
  against the image's own min/max — being the brightest object in one particular
  image is not saturation. A near-zero ROI standard deviation is the second
  signature of clipping.

## 9. Statuses and constancy

Fixed tolerances follow the guide (±2 % SID, ΔSNR < 20 %, R² > 0.95);
everything else is a **constancy test** vs the baseline of the same protocol
signature (default band ±20 %, shown in trends). Absolute values of processed
radiographs are not dose-proportional; comparing across signatures is
possible but the UI keeps the groups separate on purpose.

---

## 10. Validation summary

Automated tests (`python -m pytest tests -q`, **73 tests, all passing**):

- **Synthetic ground truth**: sub-pixel edge (<0.3 px), peak centroids
  (<0.15 px), ROI statistics exact, TLS/affine round-trips exact; rect
  detection <1 px corner error at 0°–12° rotation; grating pitch recovery
  <1 % error incl. a 6° direction mismatch (2-D FFT path); field-edge
  detection finds synthetic collimation edges at 25/40/60 mm to <2 mm on all
  four sides and rejects scatter gradients and too-short probes.
- **Line-block detection** on synthetic phantoms that include the impostors
  (uniformity-square outlines, ruler marks): exactly 5 blocks found, centres
  within 1.5 mm, impostors never selected, a strip displaced by 8 mm still
  tracked to <2 mm, and grating angles at 30/60/120° reported in phantom
  coordinates to <8°.
- **Wedge criteria**: the real S-shaped response passes; a non-monotonic
  response fails; a strongly curved but monotonic response warns rather than
  fails; the annulus background excludes the disc it surrounds.
- **Security**: password hashing is salted and never contains the plaintext,
  malformed hashes are rejected, sessions detect tampering and expire, CSRF
  requires an exact match, the login throttle is per-client and resets on
  success, and production config refuses to start without a secret key or with
  a plain-text password.
- **Reference scans** (all six, three orientations): rotation resolved
  correctly in every case (−180°, +179°, −0.25° ×2, +90° ×2), none mirrored,
  landmark RMS 0.13–0.55 mm; tape pitch 5.00 ± 0.02 mm; mean side
  299.35–300.44 mm; central-line separations 260 ± 1 mm; all five line groups
  detected in all six scans with grid residual RMS ≤ 0.03 mm; wedge monotonic
  everywhere; uniformity |ΔSNR| < 4 %; low-contrast CNR monotone in L1…L8.
- **Cross-scan consistency**: line-group pitches agree across all six scans to
  ~0.1 %; block centres repeat to 0.4–0.9 mm within a phantom variant.
- **Rotation invariance**: a 90°-rotated copy of a scan reproduces pitch,
  dimensions and group frequencies within the same tolerances — and the four
  new scans provide the same check with real acquisitions at 0°, 90° and 180°.

Known limitations:

- Field-edge detection is genuinely limited by the acquisition: in all six
  reference scans the phantom nearly fills the detector, so no collimation edge
  exists to find and every side is reported not measurable.
- Mark-to-edge distances (first ruler line, side lengths) carry a
  processing-dependent uncertainty of ~±0.5 mm because the "edge" feature
  itself shifts with edge enhancement; pitch and mark-to-mark metrics do not.
- **The reference set contains two physically different phantom units.** Their
  internal features differ by up to ~12 mm and their strip angle by ~2°. Runtime
  measurement handles this, but it means the stored nominal coordinates are a
  mid-point, not design intent, and a *third* variant differing by more than the
  28 mm search pad would need the seeds updated.
- The low-contrast circle grid is still placed from the definition (with a
  small matched-filter shift), not detected per circle — the individual discs
  are too faint to localise reliably in a single scan. If a future phantom moves
  them, Stage C manual adjustment is the fallback.
