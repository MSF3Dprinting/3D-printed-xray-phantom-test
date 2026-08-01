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

Throughout, the image viewer supports mouse-wheel zoom, drag to pan, and the
**W** and **C** sliders for window width and level. Overlay layers can be
toggled individually below the image.

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

### C — Measuring points

The actual measurement ROIs are shown. Click an ROI's centre dot to read its
mean, standard deviation and pixel count. Drag the dot to adjust it; adjusted
ROIs turn orange, and both the automatic and the manual position are kept in the
audit trail.

If a radiation-field edge was not detected automatically, select a side and
click the visible field edge on the image.

#### Reading the overlays

| Marking | Meaning |
|---|---|
| Red dashed square | Detected phantom outline |
| Yellow squares on the diagonal strip | Line-pair group ROIs, aligned to the measured strip axis |
| Pink dashed circle | Low-contrast object outline (10 mm) |
| Pink solid circle | Low-contrast measurement ROI (7 mm) |
| Pink dotted ring | Local background ring for that same circle |
| Green squares | Uniformity ROIs |
| Orange rectangles | Wedge step ROIs (S1 top to S7 bottom) |
| Blue lines from each side | Ruler and field-edge probes |

Dragging a low-contrast circle moves its ring and outline with it.

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
