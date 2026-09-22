# Algorithms and validation

How each reported number is computed. The algorithm version is stamped into
every stored result as `phantom_qa.ALGO_VERSION`.

**Conventions.** Image coordinates are (x, y) = (column, row) in pixels; phantom
coordinates are millimetres with the origin at the phantom-face centre, +x right
and +y up in the guide-figure orientation. After normalisation, a higher pixel
value means more attenuation, so the phantom is bright and direct exposure dark.

---

## 1. Ingest and normalisation — `ingest.py`

- DICOM pixel data is decoded losslessly (pylibjpeg for JPEG-Lossless), the
  Modality LUT is applied (`RescaleSlope`, `RescaleIntercept`), and MONOCHROME1
  is inverted so polarity is uniform. The inversion is about the detector's own
  range, from `BitsStored` (else `BitsAllocated`) and `PixelRepresentation`:
  `(lo + hi)·slope + 2·intercept − value`, so the same exposure of the same
  object always gives the same values. Only when the header declares no usable
  bit depth does it fall back to the image's own maximum; the rule used is
  recorded in the stored header as `_inversion`. The full header and the SHA-256
  of the source file are stored.
- The detector's exposure values — `ExposureIndex`, `TargetExposureIndex`,
  `DeviationIndex`, `Sensitivity` — are stored as numbers (absent when not
  written, never zero). They are displayed and exported; nothing is judged from
  them.
- Zip archives are walked for DICOM images; viewer executables, DICOMDIR and
  HTML are skipped.
- Plain images are converted to greyscale float and flagged
  `reduced_precision`.
- The **protocol signature** is detector model | kV | pixel spacing | processing
  family (the first tokens of `AcquisitionDeviceProcessingDescription`). It
  groups comparable analyses for baselines and trends.

## 2. Phantom detection and registration — `registration.py`

1. **Coarse detection.** The image is downsampled 8×, Otsu-thresholded, and the
   largest bright connected component taken as the phantom face. Burned-in text
   and foreign objects form smaller components and are ignored.
2. **Angle.** A rotating-calipers search (0.5° coarse, 0.02° fine) finds the
   rotation that minimises the component's bounding-box area.
3. **Sub-pixel edges.** For each side, 25 profiles are sampled perpendicular to
   the coarse edge. The edge point is the extremum of the smoothed profile
   derivative with parabolic refinement. A total-least-squares line is fitted per
   side with two rounds of MAD outlier rejection, which removes edge tabs, screws
   and touching objects. Corners are the line intersections.
4. **Orientation.** Eight candidate corner assignments (four rotations ×
   mirror) are fitted as affine transforms and scored on image content: wedge
   means must decrease from step 1 to 7, the line-pair strip must show high
   local standard deviation, and the low-contrast block must be bright and
   smooth. All candidate scores are stored.
5. **Verification landmarks.** The central long ruler line of each side is
   located along the side normal near its predicted position. The RMS prediction
   error in millimetres is reported as `residual_rms_mm`. These landmarks verify
   the fit; they do not drive it.
6. **Manual fallback.** Four user-clicked corners replace steps 1–3. Orientation
   scoring still resolves the assignment, so click order does not matter.

The transform is a full six-degree-of-freedom affine (mm → px). `mirrored` is
derived from the determinant sign, accounting for the y-down image frame;
rotation from the image of the +x axis.

## 3. Scale chain — `analysis/geometry.py`

`side_mm = 299.5` in the phantom definition is chosen so the measured tape pitch
reads 5.000 mm: the transform scale is anchored to the printed pitch, not to
DICOM metadata. Per scan:

- `pitch_measured_mm` — mean regression slope of the six ruler-line positions,
  over the sides that pass the quality gate below.
- `k = 5.0 / pitch_measured` — a residual per-scan correction. All absolute
  dimensions are reported as (transform mm × k).
- A cross-check table compares the pitch-anchored `absolute_mm_per_px` with the
  DICOM `ImagerPixelSpacing` (detector plane) and `PixelSpacing`.

**Ruler quality gate.** Because `k` multiplies every reported length, one ruler
that was not actually found corrupts the whole report. A side is admitted to the
mean only if its mark positions are evenly spaced — linearity residual RMS
≤ `ruler_linearity_rms_max_mm` (0.5 mm; the reference scans sit below 0.25 mm).
Rejected sides are listed in `scale.rulers_rejected` and named in the dimension
reasons. Two admitted sides are required for `scale.reliable`; below that the
scale cannot be cross-checked and the results say so rather than presenting a
number that looks measured. If every side is rejected the mean falls back to all
detected sides, flagged unreliable — a bad scale that announces itself is more
useful than no result.

> Measured on a scan from a different X-ray unit, three of four rulers were
> mis-detected (residual RMS 1.2–2.0 mm against 0.02 mm for the good side) and
> outvoted the one good ruler, reporting the 300 mm phantom as 232.7 mm. With
> the gate the same scan reports 293.1 mm and flags the scale as resting on a
> single ruler.

On the reference scans the phantom face sits at magnification ≈ 1.042 relative
to the detector plane (0.1419 against 0.148 mm/px), because the phantom face
stands off the imaging plane. This is why the tape pitch, not the metadata, sets
the scale.

## 4. Rulers, dimensions and field alignment — `analysis/geometry.py`

**Ruler probe.** A profile is taken along the side's inward normal through the
ruler centre, averaged over ±3.5 mm along the edge to dilute the pointer tick.
The six line positions are the six strongest peaks, located sub-pixel by
intensity centroid. Offsets are referenced to the locally detected face edge —
the strongest rising step within −2…+6 mm — which decouples them from
corner-fit residuals.

**Pitch and linearity.** Linear regression of position against index per ruler
gives the pitch (nominal 5.0 mm), the per-line residuals, and their RMS
(`linearity_rms_mm`). The RMS is also the admission test for the scale chain
above; `reliable` on each ruler row records the outcome.

**Dimensions.** Corner-to-corner distances in calibrated millimetres for four
sides and both diagonals, against the assumed nominal side of 300.0 mm; plus
central-line separations (nominal 260.0 mm = side − 2 × 20 mm).

**Field alignment.** A probe runs outward from each side, from 8 mm past the
face edge to 12 mm short of the image border, averaged over ±10 mm along the
edge. A collimation border is a genuine step — direct exposure on one side, a
blocked beam on the other — whereas scatter gradients and the phantom's carrier
frame are not. The detector fits both a two-plateau change-point model and a
single linear ramp, and accepts the step only if **all** of:

- the probe is at least 25 mm long, with at least 8 mm of plateau on both sides
  of the split;
- `SSE_step ≤ 0.35 × SSE_linear`, so a ramp does not explain it as well;
- at least 70 % of the plateau difference occurs within a few millimetres of the
  split;
- the step height exceeds max(8 × local noise, 4 % of the image dynamic range).

Otherwise the side is reported not detected, with a reason, and manual placement
is requested. Deviation is the distance from the field edge to that side's
central long line, in millimetres and as a percentage of SID (tolerance ±2 %).

The probe deliberately stops short of the image border, which is itself a large
sharp intensity change that would otherwise be reported as a collimation edge.

> On all six reference scans every side is correctly reported as not measurable:
> the phantom nearly fills the detector, leaving only 30–61 mm of image outside
> its edge, and the radiation field extends past the image. To use this test,
> collimate visibly inside the detector.

**Accuracy note.** Mark-to-edge distances carry a processing-dependent
uncertainty of roughly ±0.5 mm, because the edge feature itself shifts with edge
enhancement. Pitch and mark-to-mark metrics do not.

## 5. Line patterns — `analysis/linepairs.py`

Block positions are **measured in every scan**; the phantom definition supplies
nominal seeds only.

**Periodicity map.** Local variance cannot be used to find the groups: the dark
wedge steps carry far more quantum noise than the tungsten patterns carry
signal, so a variance detector locks onto the wedge. Noise is broadband and a
printed grating is a sharp spectral peak, so the discriminator is the
**peak-to-median energy ratio in the 1.0–2.4 mm⁻¹ band**, computed on 10 mm
windows at 2 mm stride over a corridor around the nominal strip. Blobs with a
ratio above 12 are candidates.

**Impostor rejection.** Uniformity-square outlines, ruler marks and the wedge
frame also produce peaks in that band. The real groups all lie on one strip, so
they share a modulation direction (circular median, ±12° kept) and are collinear
(MAD-based rejection about the fitted centre line). Both conditions must hold.

**Angle convention.** The rectified corridor's row index runs along *decreasing*
phantom y, so the row-axis FFT frequency is the negative of the phantom-y
frequency. The sign is flipped when converting to phantom coordinates; without
it every measured angle is mirrored about the x axis, which is invisible at
exactly 45° but a ~5° error elsewhere.

**Strip axis** is fitted through the detected block centres rather than derived
from the modulation direction, so no assumption is needed about whether the
printed lines run along or across the strip.

**Sub-window centring.** The periodicity grid is coarse, so each centre is
refined by band-passing the region at that block's own measured frequency and
taking an iterated, isotropic energy centre of mass over a disc. A box filter is
not used, because it biases a block rotated ~45° to the sampling grid.

**Identity assignment.** Blocks are ordered along the strip and matched to the
definition's groups, validated by correlating measured against nominal
frequency. When that correlation is below 0.5 the order is abandoned for a
greedy nearest-frequency match in definition order, on the coarse frequency
estimate. A block whose frequency contradicts its assigned identity (by more
than 0.35 lp/mm) is dropped rather than reported. On the blue prints this
fallback mislabels G1.1 and G1.2; that is parked, unchanged, pending a physical
check — see DESIGN.md, *Parked: the line-pair strip on the blue prints*.

**SD metric.** Standard deviation inside the 12.6 × 12.6 mm ROI — the guide's
constancy metric. The ROI is placed at `roi_angle_offset_deg` (45°) to the strip
axis, so its corners meet the nub markers on the two frame lines and the strip's
centre line, as the guide specifies. The ROI orientation and the profile
direction are independent: the square follows the guide's placement, the profile
follows the printed lines.

**Manual correction.** The ROI is the handle. When it is moved, the profile is
rebuilt at the new centre and its direction re-measured there, so the reported
numbers always come from where the square is. A profile whose direction the user
set by hand is marked and no longer overridden by the FFT.

**Linearity.** The pattern's frequency and direction come from a 2-D FFT of a
10 mm patch, whose peak must exceed 8× the band median. The profile is sampled
along that direction in ~0.07 mm steps, averaged ±4 mm along the lines,
detrended, and line peaks detected. The run of peaks is selected against the
**measured median gap** (±18 %) rather than the nominal: block-edge artefacts
produce short spurious gaps that a nominal-only window admits, dragging the
fitted pitch down by several percent. A uniform grid `pos_i = p0 + i·pitch` is
then least-squares fitted with one MAD-based outlier pass.

Reported: measured pitch and lp/mm, deviation against nominal, per-line
residuals in micrometres and their RMS, and the raw profile for plotting.

> Across all six reference scans the pitch deviations are stable to about 0.1 %:
> +0.8 %, −2.0 %, +1.4 %, −2.4 %, +1.9 % for 2.0/1.6/1.4/1.2/1.1 lp/mm. This is
> reproducible print geometry, not measurement noise.

## 6. Low contrast — `analysis/lowcontrast.py`

- Block centre and angle come from the smoothed bright blob (PCA of the pixel
  covariance, angle snapped to the nominal −45° ± 25°).
- The rigid 4 × 2 circle grid is shifted by the median offset of the three
  strongest matched-filter responses (dark disc: annulus mean − disc mean, ±2.5
  mm search).
- Per circle: an object ROI of Ø 7 mm, and a background **annulus around that
  same circle** (Ø 12 → 16 mm). A concentric ring is local to its own object and
  cannot collide with a neighbour or pick up the block's brightness gradient.

`CNR = (μ_obj − μ_bg) / √(σ_obj² + σ_bg²)` — the guide's formula, signed;
circles are darker, so values are negative.

**Geometry** measured on the reference scans: circles at u = ±10, ±30 mm and
v = +9.4 / −10.2 mm in block coordinates; block 84.5 × 44.5 mm at −45.1°.

**Labelling.** Circles are L1…L8 in design order, a boustrophedon starting
top-right, confirmed by measurement: contrast rises monotonically along that
path, from about 0.33 % to 1.37 % at 77 kV. No nominal contrast values are
claimed.

**Insert orientation.** The two builds in use differ by a half turn of the
insert, which maps every disc position onto another (the disc designed as
L(i+4) sits where L(i) is expected), so the ROIs land on real discs either way.
Which way round it is fitted is decided from the contrast order: Spearman's ρ
between design level and |CNR| under both labellings, trusted when the better
one leads by at least 0.6 — on the 33 reference scans every confident scan
leads by ≥ 1.14 and the single weak exposure by 0.19. With fewer than six
measured discs, or a smaller lead, the reading is *undetermined* and the discs
are read as drawn. A value saved for the phantom with its measuring points, or
set by hand in Stage C, is used instead of this reading; the reading is still
made and a confident disagreement is flagged (`conflict`). A block turned end
for end by hand (more than 90° from the drawn angle) relabels every ring by
itself and is taken out first, so the orientation always describes the insert.
ROI ids stay tied to block positions; each result row carries the
`design_level` and `disc` it is read as, sorted by design level.

**Order check.** `ordering_ok` requires at least four measured discs and
ρ(design level, |CNR|) ≥ 0.6. A rank correlation rather than a count of rising
neighbours: adjacent discs differ by less than the noise between exposures, so
counting pairs warned on three reference scans correlating at 0.88–0.93.
Correctly placed grids sit at ≥ 0.857; the one grid with nothing to hold on to
at −0.262. A failed order check gives **warn**.

**Close-up for placing** (`block_view`, `view_markers`). The block is sampled at
4 px/mm in its own frame, flattened by subtracting an 8 mm Gaussian, smoothed at
1 mm and windowed to the 1st–99th percentile of its interior. Each disc's
visibility is its matched-filter response relative to the strongest disc of the
same exposure: ≥ 0.5 clear, ≥ 0.2 faint, otherwise at the limit. The close-up is
for looking; CNR is always measured on the original pixels.

## 7. Uniformity — `analysis/uniformity.py`

Square outlines are re-detected per scan using three line-centre probes per side
of each square, taking the median. The ROI is 30 × 30 mm centred in each square.

`SNR = μ / σ` and `ΔSNRᵢ = (SNRᵢ − SNR_avg) / SNR_avg`; the test passes if
|ΔSNRᵢ| ≤ 20 %.

## 8. Wedge — `analysis/wedge.py`

- The x-centre comes from the dark frame minima at three heights, taking the
  median. Step boundaries come from gradient-magnitude peaks along the wedge
  axis, each searched ±3 mm around the measured boundaries stored in the
  definition. The steps are about 19 mm with a ~26 mm bottom step; these are
  measured, not assumed.
- Step ROIs are a **fixed** `roi_w_mm` × `roi_h_mm` (10 × 10 mm) centred in
  every step. The printed steps are not equal heights, so sizing each ROI to its
  own step would average a different area per step and make the same step
  incomparable between phantoms. Each step reports its measured height and
  whether the fixed ROI fits inside it.
- Reported: mean and standard deviation per step S1…S7 (positional labels only),
  monotonicity, dynamic-range ratio (S1/S7), the axis profile, and a
  least-squares fit of mean against step index with R² and per-step residuals as
  a percentage of span.

**Pass criteria.**

| Outcome | Condition |
|---|---|
| **Fail** | Response is not monotonic, or any step is saturated |
| **Warn** | R² below `wedge_r2_min` (default 0.85) |
| **Pass** | Otherwise |

R² is not the pass criterion because the response is reproducibly S-shaped:
across all six reference scans R² is 0.918–0.953 with a near-identical residual
pattern (−13, −1, +9, +10, +7, +2, −15 % of span) and step-to-step ratios from
1.03 to 2.0. The printed steps are not equal increments of attenuation. A
log-linear fit is worse (R² ≈ 0.77–0.81), so this is not a missing exponential.
Requiring R² > 0.95 on a linear fit would fail a sound detector because of the
phantom's own geometry. The per-step means are what get trended against the
baseline.

**Saturation** is tested against the detector's full scale — 2^`BitsStored` − 1,
or the next power of two above the image maximum when the tag is absent — not
against the image's own extremes, since being the brightest object in one image
is not saturation. A near-zero ROI standard deviation is the second signature of
clipping.

## 9. Statuses and constancy

Fixed tolerances follow the guide: ±2 % of SID for field alignment, |ΔSNR| < 20 %
for uniformity. Everything else is a **constancy test** against the baseline of
the same protocol signature, with a default band of ±20 % shown in trends.

Absolute values in processed radiographs are not proportional to dose. Comparing
across protocol signatures is possible but the interface keeps the groups
separate deliberately.

### Reasons

Every test returns a `reasons` list alongside its status, and the interface and
the report render it next to the result. A status on its own tells an operator
that something is wrong but not what to do about it, which is the difference
between a usable QA tool and a number to be ignored.

| Field | Where |
|---|---|
| `reasons` | `linepairs`, `lowcontrast`, `uniformity`, `wedge` |
| `reason` (per group) | each row of `linepairs.rows` |
| `dimension_reasons`, `field_reasons` | `geometry` |

A reason states the measured value, the limit it was compared against, and —
where the two are distinguishable — whether the cause is more likely the
detector or the ROI placement, naming the step where it can be corrected. A
passing test explains why it passed, so a pass that rests on a mis-placed ROI
is still visible. Reasons are generated from the same numbers as the status;
they are not a separate judgement and cannot disagree with it.

### Acquisition quality — `quality.py`

Every test below assumes the image holds a phantom that was exposed sensibly.
When it does not, the measurements do not fail loudly; they come out meaningless
and quietly. The gate asks that question once, from signals that need no
knowledge of what the phantom contains, and records its verdict on the analysis.

| Check | Signal | Limit | Reference scans | Broken field exposures |
|---|---|---|---|---|
| `saturation` | share of pixels on the image's own maximum | ≤ 0.05 | 0.0000–0.0014 | 0.70 (over-ranged) |
| `clipping` | ratio of the fitted transform's singular values | ≤ 1.010 | 1.001–1.004 | 1.051 (edge off detector) |
| `landmarks` | central ruler lines located, of four | ≥ 3 | 4 | 0 (over-ranged) |
| `recognition` | score of the accepted placement | ≥ 5.5 | 7.33–7.89 | 3.79–3.84 |
| `placement_margin` | lead over the runner-up placement | ≥ 0.40 | 0.89–1.83 | 0.12–0.22 |

Saturation is measured against the image's **own** maximum rather than the
detector's full scale, because vendor processing rescales and what matters is
that a large part of the image has been flattened onto one value.

The checks overlap deliberately: saturation and lost ruler lines both catch an
over-ranged exposure, stretched registration and a thin placement margin both
catch a clipped one. Every broken exposure in the field set trips at least two,
so no single signal has to be perfect.

Limits come from the 33 HQ reference scans and are verified against all of them;
the field exposures only ever confirm that the gate fires. Each scan's measured
values are recorded in `tests/hq_benchmark.json`, so tightening a limit produces
a diff naming every reference scan it would newly reject, and
`tests/test_quality_gate.py` additionally asserts that each limit keeps a margin
rather than merely clearing the worst reference scan.

The verdict is computed during registration rather than at upload, because the
useful signals come from the fit — and because re-registering by hand is exactly
when it should be reconsidered: manual corners can rescue a scan the automatic
fit had mangled.

A refused exposure is still analysed. It is barred only from becoming a baseline
or a stored layout — see `blocks_reference_use()`.

### The time budget

`compute_all()` and `propose_all()` take an optional `Deadline`. Between the
five tests — not inside them — they check whether the budget is spent; once it
is, the remaining tests are recorded as `status: "error"` with `timed_out: true`
and a reason naming what was not measured. The analysis therefore ends in a
stored failure rather than an open request.

The limit is `PHANTOMQA_ANALYSIS_TIMEOUT_S` (default 120 s, `0` disables it).
The browser learns it from `/api/auth` and waits 60 s longer, so a bounded
analysis reports its own failure instead of being cut off by the page. The
timeout applies only to the measuring calls: a 7.5 MB upload over a field link
legitimately takes minutes, and a blanket timeout would be a worse failure than
the one it guards against.

Two honest limitations:

- **The check is cooperative.** A single numpy call cannot be interrupted
  part-way without a separate process, which would change how this is deployed.
  Across 38 scans no individual test took longer than about four seconds, so
  the budget is spent between checks rather than inside one — but a pathological
  image could overshoot the limit by however long one test takes.
- **It is not what fixed "stuck at Computing…".** That screen came from the
  results page throwing while drawing, after the server had already finished in
  a tenth of a second. This is a backstop for a different, so-far-unobserved
  failure: an analysis that genuinely runs long.

### What could not be measured

Some quantities do not exist for some images. SNR needs a standard deviation to
divide by, CNR needs noise around the disc, and the wedge's R² needs a response
that varies. On a saturated exposure none of those is available — not as zero,
but as nothing at all.

Every test therefore reports on two lists:

| Field | Meaning |
|---|---|
| `rows` | objects that **were** measured. Every numeric field holds a real number |
| `not_measured` | objects that could not be, each with its `id` and a `reason` |

The rule is absolute: a row never carries an empty value. A caller that finds a
row can format every number in it without checking, and one that wants to know
what is missing looks in `not_measured`.

This was not always so. An unmeasurable value used to be carried as `NaN`,
which is corrosive in three separate ways: every comparison against it is
silently false, so `nan < tolerance` reads as "within tolerance"; aggregates
built on it (`max(0.0, nan)`) quietly produce a good-looking number; and it
serialises to `null`, which then breaks whichever consumer formats it. All
three happened at once — uniformity reported **pass** on an image with no
signal while all five of its own rows said fail, and the printed report
answered 500.

A test that could measure nothing reports status `not measured` with a reason,
and `overall_status` counts `not measured` as a warning, so such a scan can
never come out as passed. That is kept apart from `not applicable`, which only
X-ray field alignment reports, when no field edge is in the image: that test
does not apply, so it is skipped in the overall verdict, and the verdict carries
a note instead (`pipeline.verdict_notes`, e.g. "X-ray field alignment not
checked (no field edge found in the image)"). The overall verdict itself stays
one of `pass`, `warn`, `fail`, `error` — or `n/a` when nothing at all could be
judged.

Analyses stored before the two words were separated carry `n/a` for both
meanings. They are not recomputed; `n/a` keeps its old rank between `pass` and
`warn`, so re-reading one reproduces the verdict it was stored with.

---

## Validation

The test suite runs with `python -m pytest tests -q`.

### Synthetic ground truth

| Check | Result |
|---|---|
| Sub-pixel edge location | < 0.3 px |
| Peak centroids | < 0.15 px |
| ROI statistics | exact |
| TLS and affine round-trips | exact |
| Rectangle detection, 0°–12° rotation | < 1 px corner error |
| Grating pitch recovery, including a 6° direction mismatch | < 1 % |
| Line-block detection with impostors present | exactly 5 blocks, centres within 1.5 mm, impostors never selected |
| Strip displaced by 8 mm | tracked to < 2 mm |
| Grating angles at 30/60/120° | reported in phantom coordinates to < 8° |
| Synthetic collimation edges at 25/40/60 mm | found to < 2 mm; gradients and short probes rejected |

### Reference scans

Six scans in three orientations (0°, 90°, 180°):

- Rotation resolved correctly in every case; none mirrored; landmark residual
  RMS 0.13–0.55 mm.
- Tape pitch 5.00 ± 0.02 mm; mean side 299.35–300.44 mm; central-line
  separations 260 ± 1 mm.
- All five line groups detected in all six scans, grid residual RMS ≤ 0.03 mm.
- Wedge monotonic in every scan; uniformity |ΔSNR| < 4 %; low-contrast CNR
  monotone across L1…L8.

### Repeatability

- Line-group pitches agree across all six scans to about 0.1 %.
- Block centres repeat to 0.4–0.9 mm within a phantom variant.
- A 90°-rotated copy of a scan reproduces pitch, dimensions and group
  frequencies within the same tolerances — as do the real acquisitions at 0°,
  90° and 180°.

### Limitations

- Field-edge detection is limited by the acquisition, not the algorithm; see
  section 4.
- Mark-to-edge distances carry ±0.5 mm of processing-dependent uncertainty.
- The reference set contains two physically different phantom units, whose
  internal features differ by up to 12 mm and whose strip angles differ by about
  2°. Runtime measurement absorbs this, but the stored nominal coordinates are a
  mid-point rather than design intent. A third variant differing by more than
  the 28 mm search pad would need the seeds updated.
- The low-contrast circle grid is placed from the definition with a small
  matched-filter shift, not detected per circle: the individual discs are too
  faint to localise reliably in a single scan. Manual adjustment in step C is
  the fallback.
