# Design decisions

The reasoning behind the main architectural choices, for anyone modifying the
software.

---

## Fully automatic ingestion; no manual export

The DICOM files contain everything needed: lossless pixel data, pixel spacing,
kV, mAs, detector identification and the processing description. The application
reads them directly, so no number is ever transcribed from a viewer.

Manual transcription is the largest source of error and inconsistency in phantom
QA, and consistent ROI placement is something software does better than a
person.

## The program proposes, the user confirms

Nothing is measured from geometry the user has not approved. The wizard gates
registration, pattern identification, measuring points and dimensions before any
result is computed.

Full automation without verification would be faster but unverifiable; manual
measurement would be verifiable but inconsistent. The staged workflow keeps the
consistency of automation while leaving a person responsible for what is
measured.

Every displayed number is backed by an ROI drawn on the actual image, and any
ROI can be dragged, with both the automatic and the adjusted position retained.

## Constancy testing against a protocol signature

Pixel values in processed radiographs are not proportional to dose, so absolute
values cannot be compared across acquisition conditions. Each analysis therefore
records a **protocol signature** — detector model, kV, pixel spacing and
processing family — and comparisons are grouped by it.

Scans taken with different parameters, machines or processing are never silently
mixed.

## Scale anchored to the printed tape pitch

Three scale sources are cross-checked in every scan: the DICOM pixel spacings,
the measured 5 mm tape pitch, and the phantom's own dimensions.

The absolute scale is anchored to the **tape pitch**, not the metadata, because
the phantom face sits at a magnification of about 1.042 relative to the detector
plane. Trusting `ImagerPixelSpacing` alone would embed that error in every
dimension.

## No material assumptions

The phantom is experimental and not made of the materials a commercial phantom
would use, so the guide's nominal values — copper-equivalent thicknesses for the
wedge, percentage contrasts for the circles — are neither used nor displayed.

- Wedge steps are **S1…S7 by position**, and the fit is mean value against step
  index.
- Low-contrast circles are **L1…L8 in design order** of increasing measured
  contrast.
- Line-pair frequencies in the definition were measured from the reference
  scans, not taken on faith.

Two nominal values remain, both stated explicitly in the interface: the printed
5 mm tape pitch, used for scale, and the assumed 300 mm phantom side, for which
no drawing was available.

## Geometry measured per scan, not read from the definition

Line-pair block positions are detected in every scan; the stored coordinates are
search seeds only.

The reference set contains two physically different phantom units whose internal
features sit up to 12 mm apart. Stored coordinates cannot track that, and a
future phantom would differ again. Measuring per scan means the ROIs land
correctly regardless.

## Detectors report failure rather than guessing

Where a feature cannot be found reliably, the software says so and requests
manual placement, instead of returning a number that looks authoritative.

Field-edge detection is the clearest case: it requires a genuine intensity step
that a linear ramp cannot explain as well, with sufficient plateau on both sides
and away from the image border. On acquisitions where the phantom nearly fills
the detector there is no collimation edge to find, and the honest answer is "not
measurable".

## Pass criteria matched to the phantom, not to convention

The wedge response is reproducibly S-shaped because the printed steps are not
equal increments of attenuation. Judging the detector by the linearity of that
curve would fail a sound detector for a property of the phantom.

Pass and fail are therefore decided by **monotonicity and saturation**, with R²
retained as a shape descriptor carrying a soft warning threshold.

## Results are stored, not recomputed

An analysis keeps the numbers produced by the algorithm and definition current
when it was computed, together with both version stamps.

A QA record that changed silently under a software update would be worthless,
especially one that has been signed off. Because the source file is also kept,
results can be recomputed deliberately with `manage reanalyze` when an
improvement warrants it.

## Median of the selection as the comparison reference

The comparison report measures every deviation against the **median of the
selection**, not against the first entry.

When comparing several phantoms there is no meaningful "first", so
"change since the first scan" would be an arbitrary framing. For a single
phantom over time the median remains a sensible reference, so one layout serves
both cases.

Metrics that are themselves percentages, such as pitch deviation and ΔSNR, are
compared in percentage points, because their median sits near zero and a ratio
would be meaningless.

## Two privilege levels, with the administrator password per action

A user can run analyses and read everything. Deleting and validating require a
separate administrator password, entered at the moment of the action rather than
at sign-in.

This lets an administrator work as an ordinary user and supply the credential
only when making a decision that an ordinary user must not make. Deletion
additionally requires the analysis id to be typed back, so a misclick cannot
destroy a record.

The approver's **name** is recorded separately from the password: a shared
credential establishes the right to sign off, not who did.

## Trust the ASGI server for the client address

The application does not parse `X-Forwarded-For`. uvicorn already does, and only
when the immediate peer is listed in `forwarded_allow_ips`.

Parsing the header in the application as well would discard that trust boundary:
on a shared server, any process able to reach the loopback port could forge an
address per request and evade the login throttle.

For the same reason the sub-path comes from configuration rather than from
`X-Forwarded-Prefix` — a client-controlled header should not determine where the
application believes it is mounted.

## Shared throttle state

Gunicorn runs several worker processes, so the login and administrator throttles
are stored in SQLite rather than process memory. An in-memory counter would give
an attacker `max_attempts × workers` guesses.

## Self-contained, offline-capable output

Reports embed their charts and overlays as data URIs, and the frontend has no
build step and no external dependencies. The application runs without internet
access, and an exported report remains readable years later with no server
involved.

---

## Technology

Python 3.11+, FastAPI and uvicorn, numpy, scipy and scikit-image, pydicom with
pylibjpeg, matplotlib for report charts, and SQLite for storage. The frontend is
plain JavaScript with a `<canvas>` viewer.

SQLite in WAL mode is appropriate at QA-team concurrency. Should that change,
`store.py` is the only module that would need replacing.

---

## Open points

- **Phantom drawing.** No CAD or drawing was available, so `nominal_side_mm`
  (300.0) is assumed design intent. Everything else in the definition is
  measured. A drawing would allow dimensional deviations to be judged against
  design intent rather than against a self-derived reference.
- **Tolerances.** The guide's values are implemented (±2 % of SID, |ΔSNR| < 20 %).
  Constancy bands default to ±20 % and should be revisited once several months
  of data exist.
- **Per-user accounts.** There is one shared login and one administrator
  password. The approver's name is typed rather than authenticated. If
  attribution must be provable, per-user accounts are a genuine feature to add.
