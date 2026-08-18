# User guide

## Signing in

Open the application URL and sign in with the shared user account. The session
lasts 12 hours by default.

Deleting and validating additionally require the **administrator password**,
which is entered at the moment of the action rather than at sign-in.

---

## Running an analysis

The wizard has seven steps. Nothing is measured from geometry you have not
confirmed, and you can move back at any point.

Throughout, the image viewer supports mouse-wheel zoom and drag to pan. Overlay
layers can be toggled individually below the image.

The **W** (width) and **C** (centre) sliders are **relative to the image's own
value range**, shown as a percentage with the resulting absolute values beside
them. Detectors differ in bit depth — 12-bit on one unit, 14-bit on another — so
a fixed absolute scale would white out an image from the wrong device entirely.
**auto** returns to the automatic window.

### Upload — choose the file and identify the scan

Choose the scan file and fill in the identification, in either order. Nothing is
uploaded until you press **Upload & analyse**.

| Field | Purpose |
|---|---|
| **Site** | Groups analyses for trending. Required for grouped comparison |
| **Phantom** | Identifies the physical phantom. Required for grouped comparison |
| Operator | Optional |
| Notes | Optional |

Previously used values appear as suggestions, and the last values are remembered
for the rest of the session so a batch of scans need not be retyped. A warning
appears if both Site and Phantom are empty, because such an analysis will not
appear in any grouped trend.

Labels can be changed later at any time — see *Correcting the identification*.

**A file that has already been analysed.** Every upload is fingerprinted with
its SHA-256. If the same file is uploaded twice the program stops and shows the
existing record instead of quietly creating a second one, because a duplicate
would appear twice in History and count twice in every trend. Three choices are
offered:

| Choice | Effect |
|---|---|
| **Open the existing analysis** | Go to the record that already holds this file |
| **Analyse again anyway** | Deliberately create a second record — for a re-analysis after a code or definition change |
| **Cancel** | Do nothing |

Re-uploading a file whose record was deleted is allowed and creates a new one.

### A — Registration

The program locates the phantom, resolves its orientation (any rotation,
mirrored or not) and reports the fitted outline, rotation, scale and the
per-side landmark residuals.

Check that the red dashed outline follows the phantom edge. If detection failed,
choose **Manual corners** and click the four phantom corners yourself; the order
does not matter.

### B — Patterns

Every detected pattern is outlined and labelled: the five line-pair groups with
their frequencies, the low-contrast block, the wedge steps, the uniformity
squares, the rulers and the field edges.

Confirm each label sits on the correct object. Zoom in — a mislabelled group or
a 180° mix-up must be caught here.

Two ways on from here:

- **Verify measuring points →** continues to step C, where every ROI can be
  moved and rotated by hand. This is the way on whether or not the automatic
  labels are right: a wrong pattern is corrected in step C, not by confirming
  it here.
- **Back to registration** returns to step A, for when the phantom outline
  itself was found wrongly and everything downstream inherited the error.

### C — Measuring points

The actual measurement ROIs are shown. Click an ROI's centre dot to read its
mean, standard deviation and pixel count.

**Moving.** Drag the centre dot. Everything attached to that ROI moves with it —
the low-contrast background ring and object outline, and the line-pattern
profile line — so what you see is always what is measured. Adjusted ROIs turn
orange, and both the automatic and the manual position are kept in the audit
trail.

**Rotating.** Select an ROI and use the angle slider in the details panel at the
bottom, the ±1° buttons, or the `[` and `]` keys. This matters when a phantom
differs from the definition and the automatic orientation is wrong.

For a line-pattern group, rotating the square also sets the profile direction by
hand, overriding the automatic one. Move the square first, then rotate only if
the automatic direction did not follow the pattern.

If a radiation-field edge was not detected automatically, select a side and
click the visible field edge on the image.

> **A phantom that differs from the definition.** Automatic placement is seeded
> from the stored phantom geometry, so on a different build — or a different
> X-ray unit — some patterns will be found in the wrong place. That is expected.
> Correct them here by dragging and rotating; every measurement is taken from
> where you put the ROI.

#### Reading the overlays

| Marking | Meaning |
|---|---|
| Red dashed square | Detected phantom outline |
| Yellow squares on the diagonal strip | Line-pair group ROIs, set 45° to the strip so their corners meet the nub markers and the strip centre line |
| Short yellow line through each square | The profile sampled for pitch and linearity; it crosses the printed lines |
| Pink dashed circle | Low-contrast object outline (10 mm) |
| Pink solid circle | Low-contrast measurement ROI (7 mm) |
| Pink dotted ring | Local background ring for that same circle |
| Green squares | Uniformity ROIs |
| Orange rectangles | Wedge step ROIs (S1 top to S7 bottom) |
| Blue lines from each side | Ruler and field-edge probes |

Dragging a low-contrast circle moves its ring and outline with it.

#### Repositioning the whole low-contrast block

The eight circles sit on a fixed grid inside the block, so when the block as a
whole is in the wrong place there is no need to drag eight circles. Move the
block and all eight follow. Three ways, in the block's own panel in step C:

| Action | How |
|---|---|
| **Move** | Drag the block rectangle itself (not a circle) |
| **Rotate** | **Set block angle…** and type the angle in degrees |
| **Redefine completely** | **Click 4 block corners…** then click the block's four corners on the image, in order around the rectangle |

The four-corner method is the same idea as manual phantom corners in step A and
is the one to use when the block is both shifted and rotated: the centre and the
angle are both fitted from the four clicked points.

Any of the three marks the block and all eight circles as manually adjusted, and
discards the automatic grid refinement — that refinement was a correction to the
*old* placement and would otherwise pull the circles back off the objects.

### D — Dimensions

Verification of the millimetre calibration before any measurement depends on it:

- corner-mark distances: four sides and both diagonals
- per-ruler mark pitch and the linearity of the 5 mm marks
- central-line separations
- a three-way scale cross-check: measured tape pitch against both DICOM pixel
  spacings, including the implied magnification
- field-edge deviation per side, in mm and as a percentage of SID

Enter the **SID** here (default 1000 mm). Confirming this step locks the
calibration used by every later number.

**Unusable rulers are excluded.** The mm/px scale comes from the measured 5 mm
mark pitch, so a ruler that was not actually found would corrupt every length in
the report. A ruler whose marks are not evenly spaced — residual RMS above
`ruler_linearity_rms_max_mm`, 0.5 mm by default — is dropped from the scale and
named in the reasons. If fewer than two usable rulers remain the scale cannot be
cross-checked, and the step says so: treat the dimensions as indicative and fix
the ruler probes in step C before relying on them.

### E — Analysis

All tests are computed from the confirmed geometry:

- **Line patterns** — standard deviation per group, plus the intensity profile
  across each group with the fitted line grid, measured pitch against nominal
  frequency, and per-line residuals in micrometres.
- **Wedge** — mean per step S1…S7, monotonicity, dynamic-range ratio, saturation
  flags, and the linear-fit R² as a shape descriptor.
- **Low contrast** — CNR per circle L1…L8 in design order, with an ordering
  sanity check.
- **Uniformity** — SNR per square and ΔSNR against the mean, tolerance ±20 %.
- **Field alignment** — deviation from each side's central long line, tolerance
  ±2 % of SID.

#### Why a test passed, warned or failed

Every test states its reasoning next to its result, so a bad result can be
acted on rather than merely noted. A green box gives the reason the test
passed; an amber box lists what went wrong, in the test's own terms, with the
measured value, the limit it was compared against, and where to go to correct
it. Line patterns additionally give a per-group reason so it is clear *which*
group is at fault.

Most reasons distinguish a genuine detector problem from a placement problem —
for example, a low-contrast ordering failure says the grid may not be on the
printed objects and points back to step C, and a non-monotonic wedge says a
step ROI may be sitting on a boundary. Read the reason before repeating the
exposure.

### F — Save and export

The analysis is stored with its full audit trail. From here you can:

- mark it as the **baseline** for its protocol signature
- open the **printable report**, or download **CSV** or **JSON**
- **verify the source file** against its recorded SHA-256
- set the **validation** state

---

## Correcting the identification

Once an analysis is open, an identity bar above every wizard step shows Site,
Phantom and Operator with an **Edit** button. Labels can be corrected at any
point — during the wizard, after the results are computed, or later from the
**label** link in the History table. Every change is recorded in the audit
trail, and the analysis appears immediately in the matching filters and trends.

---

## Validation

The measurements describe the phantom; a person still decides whether it is
accepted. That decision is recorded on the analysis.

| State | Meaning |
|---|---|
| **Pending review** | Nobody has ruled yet. The state of every new analysis |
| **Validated** | The phantom and this measurement are accepted |
| **Conditionally validated** | Accepted with reservations — state them in the comment |
| **Not validated** | Rejected — state why in the comment |

Recording a decision requires the administrator password, the **name of the
person approving**, and an optional comment. The name is stored separately from
the password: a shared credential establishes the right to sign off, not who
did.

Set it from the identity bar, from step F, or from the **validate** link in the
History table. A ruling can be changed or withdrawn later; every ruling and
reversal is written to the audit log with the previous state.

The decision appears at the top of the printable report, in the comparison
report, as a column and filter in History, and in both CSV exports.

---

## History and trends

The History tab works from either a **filter** or a **manual selection**:

- filter by Site, Phantom, Protocol or Validation state; the counts beside each
  option show how many analyses match
- or tick individual rows — as soon as anything is ticked, the ticked rows take
  precedence over the filter, and a note states which is in effect

Analyses are ordered by **acquisition time taken from the DICOM header**, not by
upload time, so re-analysing an old scan does not disturb the ordering.

Three outputs follow the current selection:

| Output | Contents |
|---|---|
| **Comparison report** | A visual comparison of the whole selection — see below |
| **Trend chart** | One metric across the selection, baseline starred, dashed band at ±20 % of baseline |
| **CSV export** | *Long*: one row per metric per analysis, for pivot tables. *Wide*: one row per metric, one column per analysis, for reading drift directly. Both carry site, phantom, operator and acquisition time |

### The comparison report

Built from plots rather than tables, and equally suited to one phantom over time
or to many phantoms side by side. Every visual identifies entries by **Site /
Phantom**, with the date appended only when the same phantom appears more than
once. A key at the top maps each label to its full record.

- **Status grid** — every test against every analysis, including a validation
  row
- **Where the differences are** — a ranked bar chart of the metrics that vary
  most across the selection
- **Per-pattern panels** — one small plot per object (per line-pair group, per
  low-contrast circle, per uniformity square, per wedge step), each analysis a
  labelled point coloured by site, with the selection median as a dashed
  reference. Whole-curve comparisons are included for the wedge response and the
  low-contrast CNR series
- **Deviation heatmap** per pattern — metrics that are effectively identical
  across the selection are omitted and counted rather than coloured
- **Numeric detail** — present but collapsed behind a toggle in each section

Deviations are measured against the **median of the selection**, not against the
first entry: when comparing several phantoms there is no meaningful first. For a
single phantom over time the median remains a sensible reference, so one layout
serves both.

Metrics that are themselves percentages, such as pitch deviation and ΔSNR, are
compared in percentage points rather than as ratios, because their median sits
near zero.

---

## Comparing like with like

Pixel values in processed radiographs are not proportional to dose, so all tests
are **constancy tests**: compare against the baseline of the same protocol.

Each analysis records a **protocol signature** — detector model, kV, pixel
spacing and processing family. Filtering by signature alongside Site and Phantom
keeps comparisons honest, and the comparison report prints each analysis's
signature so a mixed series is visible.

---

## Source file integrity

Every analysis records a SHA-256 fingerprint of the file it was computed from
and keeps a copy of that file. See
[MAINTENANCE.md](MAINTENANCE.md#source-file-integrity) for what it detects and
how to check it.

---

## Reduced-precision mode

A JPG or PNG upload is accepted but marked *reduced precision*: 8-bit lossy
data, no acquisition metadata, and scale derived from phantom geometry alone.
Such analyses are excluded from baselines by default and are best used for a
quick look rather than for trending.
