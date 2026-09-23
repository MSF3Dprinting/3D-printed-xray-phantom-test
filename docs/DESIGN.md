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

## The ROI is the handle; everything else follows it

A measurement region is rarely a single shape. A low-contrast circle has a
background ring and an object outline; a line-pair square has a profile line
that samples across the printed lines. Only one of them is what the user grabs.

When an ROI is moved or rotated, every shape attached to it is updated with it,
found by id prefix rather than a hard-coded list so a new companion cannot be
forgotten. The server returns all of them and the interface redraws all of them.

The rule this enforces: **what is drawn is what is measured.** A companion left
behind would put the displayed region in one place and the number it produced in
another, which is worse than an obviously wrong result because it looks right.

For the same reason the line-pair profile is derived from its ROI rather than
stored independently: the square is the handle, so the profile is rebuilt at the
new centre and its direction re-measured against the pattern actually there. A
direction the user sets by hand is marked as such and no longer overridden.

## A rigid group moves as one

The eight low-contrast circles are not independent measurements that happen to
sit near each other — they are a fixed grid printed inside one block. When the
block is found in the wrong place, all eight are wrong by the same amount, and
correcting them one at a time is eight chances to introduce a different error.

So the block itself is a handle: drag it, set its angle, or click its four
corners, and the circles are re-derived from the block frame. The four-corner
path mirrors manual phantom corners in registration, because it solves the same
problem — position and rotation are both wrong and clicking the corners states
both at once. It mirrors them in the page too: the same correctable controls,
and nothing sent until Apply (see *Manual correction is a first-class path*).

Placing the block discards the automatic grid-shift refinement. That refinement
was fitted to the previous placement; carrying it over would drag the circles
back off the objects the user just aimed at.

"Turn 180°" is a block placement like any other: it adds 180° to the block
angle, which moves every ring onto the disc opposite while each ring keeps its
id. It used to be the way to correct the insert's orientation. It no longer
names any disc — see *The insert orientation is remembered with the phantom* —
and the orientation reading subtracts a turned block out, so the button and the
insert setting cannot cancel or double each other.

## Edits are serialised, not last-write-wins

Geometry is one JSON blob per analysis. Read-modify-write on a shared blob loses
whichever edit was read first, and the symptom is confusing rather than obvious:
an ROI springs back to where it was, and moving a *different* ROI appears to fix
it — because that request re-read fresher state.

Every geometry edit therefore runs inside a single `BEGIN IMMEDIATE`
transaction (`Store.mutate_geometry`), which takes the SQLite write lock before
reading. Overlapping edits queue instead of overlapping. The cost is a held
write lock for the duration of one edit, which is microseconds of JSON work; the
benefit is that the geometry the user sees is the geometry that was saved.

The undo snapshot is written inside that same transaction, not after it. A
snapshot taken in a second transaction would reintroduce exactly the lost-update
class this exists to prevent, one level up.

## Corrections are undoable, and the history is on the server

An operator adjusting eight ROIs by hand will misplace one. Before, recovering
meant re-uploading the scan — a disproportionate price for a slip, and one that
also cost the analysis id, the audit trail and any labels already entered.

Stage C therefore has Undo, Redo and two Resets, backed by a snapshot per edit
in `geometry_history`. Three decisions worth stating:

**The stack lives on the server, not in the page.** The browser resyncs from the
server whenever a request fails or the analysis is reopened — precisely the
moments an operator most wants to undo something. A stack in the page would be
empty then.

**Snapshots are whole, not deltas.** One low-contrast block placement rewrites
25 ROIs and a re-propose rewrites everything, so a delta scheme would have to
encode whole subtrees anyway. ~120 kB of geometry compresses to ~50 kB, and the
depth cap bounds the cost. Snapshot 0 — the untouched automatic proposal — is
pinned and never trimmed, because it is what "reset to auto-detected" returns
to. Returning to a stored snapshot rather than re-running detection also means
the reset is exact rather than merely likely to agree.

**A reset is an edit, not a rewind.** It appends a new state, so pressing it by
mistake is itself undoable. A rewind would silently destroy the work it replaced.

## A phantom's measuring points are remembered, per phantom

Two phantoms built to the same drawing are not the same object. Measured on the
reference scans, repeat scans of one build reproduce the internal feature
positions to 0.2–1 mm, while two builds differ by 3–4 mm — a tenfold margin, and
the DICOM header is identical between them. The difference is assembly, and it
does not drift, so re-correcting it on every scan is work the software should be
doing.

So the measuring points confirmed in Stage C can be stored against the phantom
label and replayed on the next scan of that phantom. These decisions carry the
safety:

**Millimetres in the phantom's own frame, never pixels.** Registration resolves
that frame from the phantom's asymmetric content, so a scan taken with the
phantom turned 90° or flipped lands on the same millimetre coordinates —
measured agreement is ≤ 0.1 mm on the reference scans. Pixel coordinates are
re-derived per scan. This is the whole reason a stored layout survives the
phantom being placed differently every time.

**Applied on top of a fresh detection, never instead of one.** The per-pattern
`detected` flags an operator reads in Stage B are only evidence if they came
from this scan. Building the geometry from the layout alone would bypass exactly
the detection whose failures reveal a mis-registered scan.

**Refused when it disagrees with that detection.** The gate compares the layout
against the scan's own proposal on the orientation-bearing patterns only — line
pairs, the low-contrast block, the wedge ends. Uniformity and the corner
dimensions are excluded because they are symmetric under all eight candidate
orientations and would vote "fine" for a layout landing on the wrong side of the
phantom. So would the registration residual, since the four rulers are
identical. A real assembly difference measures 3–4 mm; a wrong orientation
measures a hundred.

**It dies with the label.** Deleting or renaming the last analysis carrying a
phantom name deletes that name's layout, inside the same transaction that counts
the remaining analyses. Without this, a layout would outlive every scan that
justified it and be silently inherited by an unrelated phantom that happened to
be given the same name — which is indistinguishable, from the operator's chair,
from marks coming back from a deleted scan.

**It is unwound with the analysis that wrote it.** A profile row keeps the
previous layout beside the current one (`prev_layout_json` and friends). When
the analysis that stored the current layout is discarded or deleted,
`Store._unwind_profile` puts the previous one back, or removes the row when
there was none. The mistake an operator throws away must not survive on
everyone else's scans as the phantom's default.

**Stored only when the operator says so.** Confirming Stage C used to store the
layout every time. The field audit log shows what that did: one phantom's layout
rewritten six times in twelve minutes, three of those from exposures nothing
could be measured in, each one silently becoming where the next operator
started. Stage C now asks — *Use these measuring points for future scans of
phantom …* — and the page always sends the answer as `save_profile`. The box
starts ticked while the phantom has no stored layout, or when the stored one
came from this same analysis, because re-confirming after nudging a mark must
keep updating it; it starts unticked once another analysis has stored one,
because replacing that is a decision. Where it cannot be offered the step says
why instead: no phantom named, the record finalised or signed off, or an
exposure that failed the image-quality check (which an administrator can
overrule — see the privilege levels below). A request without `save_profile`,
from a page cached before the question existed, gets the same rule on the
server (`_layout_save_default`), so an old page can still store a phantom's
first layout but can no longer replace one somebody else confirmed. That rule
is applied a second time inside the write transaction
(`save_phantom_profile(replace_others=False)`): two old pages confirming two
scans of one phantom at the same moment would otherwise both read "nothing
stored yet", and the second would quietly replace the first. A declined save
writes nothing — not the layout, not the `prev_*` slot — so discarding that
analysis has nothing to unwind.

## The insert orientation is remembered with the phantom

The two builds in use differ by a half turn of the low-contrast insert. The
eight discs are laid out so that a half turn maps every disc position onto
another (`FLIP_SHIFT = 4`: the disc designed as L(i+4) sits where L(i) is
expected), so the rings land on real discs either way and nothing in the
picture shows which build it is. What changes is the meaning of the numbers.

**Read from the contrast when nothing is kept.** The Spearman correlation
between design order and |CNR| is computed under both labellings; the better
one wins when it leads by at least 0.6 (`ORIENTATION_MARGIN`). Over the 33
reference scans every confident scan separates by at least 1.14 and the one
weak exposure (no disc above |CNR| 0.6) by 0.19, so the threshold sits in an
empty gap. Below the margin the reading is `undetermined` and the discs are
read as drawn (or as the rings were turned by hand).

**Stored as a property of the phantom.** A layout profile may carry
`lowcontrast_insert: {"flipped": bool}` (`layout_profile.extract_layout`). It is
written only when Stage C is confirmed and the layout is stored — same gate: the
operator chose to store it, phantom named, image-quality check passed or
overruled by an administrator — and it is read afresh from the confirmed
rings by `lowcontrast.read_orientation` (`_insert_orientation_to_store`), not
taken from the note made when the patterns were proposed, because the block may
have moved since. `layout_profile.insert_to_store` records only a decision
(source `measured`, `stored` or `user`); an `undetermined` reading carries the
previous layout's value over, or writes nothing, so one weak exposure cannot
erase what a good one established. An absent key is how every layout saved
before this behaves: each scan decides for itself.

**Replayed as a kept decision, not copied.** `apply_layout` sets the new scan's
`geometry.lowcontrast.orientation` to `{flipped, source: "stored", confidence:
None}`. `KEPT_SOURCES = ("stored", "user")` are used as they stand — that is
what lets a very weak exposure be read the right way round at all — but the
contrast order is still measured, and a confident disagreement sets
`conflict: true` and the message `ORIENTATION_CONFLICT` ("… Check that the
phantom ID is right") in the close-up, the step E reasons and the printed
report. The kept value still wins: the likeliest cause is a scan filed under the
wrong phantom ID, and quietly following the scan would hide exactly that.

**Set by hand as an ordinary edit.** `POST /api/analyses/{aid}/lowcontrast_orientation`
(`{flipped, expect_seq}`) writes source `user` through `Store.mutate_geometry`,
so it takes an undo snapshot, honours `expect_seq`, is refused on a finalised or
signed-off record (`_require_unsigned`) and is audited. Nothing moves; the
response carries the orientation summary so the page updates the close-up
without fetching its picture again. It reaches the phantom's stored layout only
when Stage C is confirmed with the layout stored, and no analysis already
measured is read again because of it.

**A turned block is taken out first.** Detection only ever places the block
within ±25° of the drawn angle and four clicked corners land there too, so a
block more than 90° from the drawing can only come from "Turn 180°" or a layout
saved after it (`lowcontrast.rings_turned`). That relabels every ring by itself.
The measured reading corrects for it, so `flipped` always describes the insert;
the discs are read shifted when exactly one of the two half turns applies
(`reads_shifted` = `flipped` XOR `rings_turned`). ROI ids stay tied to their
positions on the block — every stored layout, baseline and trend is keyed on
them — and each result row carries the `design_level` and `disc` it is read as.

**Evidence is re-read, only the decision is stored.** `propose` stores
`orientation_state` (decision, source, confidence) so Stage C can show it before
anything is measured; the evidence is measured again whenever it is needed,
because the rings it was read under can be moved. "Reset to auto-detected"
returns to the scan's own reading (snapshot 0 predates the replay); "Reset to
stored layout" replays the saved value.

**The order check is a rank correlation.** `ordering_ok` needs at least four
measured discs and ρ ≥ 0.6 (`ORDER_MIN_RHO`). It replaced "five of seven
neighbouring pairs rise", which is the wrong instrument: two adjacent discs
differ by less than the noise between exposures, so it warned on three
reference scans that correlate at 0.88–0.93. Correctly placed grids sit at
0.857 or above; the one grid with nothing to hold on to sits at −0.262.

## The close-up is named by what it shows

Stage C shows the low-contrast block on its own (`lowcontrast.block_view`):
sampled at 4 px/mm in the block's frame, an 8 mm Gaussian subtracted to remove
the illumination gradient, smoothed at 1 mm, windowed to the 1st–99th
percentile of the block's interior. About 20 kB as PNG, against 0.9 MB for the
full render. Two routes, so the picture can be cached apart from what changes
around it:

- `GET …/lowcontrast_view` — JSON: size, one marker per disc with its
  visibility (matched-filter response against the strongest disc of the same
  exposure: ≥ 0.5 clear, ≥ 0.2 faint, else at the limit), the orientation
  summary, and `key`.
- `GET …/lowcontrast_view.png?key=…` — the picture.

The picture is a function of the stored pixels, the registration and the block
placement, so `_block_view_key` digests exactly those: the file's SHA-256, the
registration transform, `ALGO_VERSION`, and the block centre and angle rounded
to 4 decimals (16 hex characters). The edit counter was tried first and is
wrong: it steps back on undo and forward again onto a different edit, and it
restarts at 0 when the phantom is registered again, so one counter value could
name two placements — and the cache served the old picture under the new rings.

**Computed once per placement.** The page always asks for the JSON and then for
the picture it names, and both routes used to run `block_view` — about 30 ms
each time the block moved. The JSON route now encodes the PNG from the view it
has just computed and keeps it in the per-worker image cache under
`(aid, "lcview", key)` (`_keep_block_png`, the one place it is encoded), so the
picture request that follows is a lookup: measured B→C 130 → 100 ms, and each
block nudge 154 → 123 ms. Those figures are from `run_app.py`, one process
answering both requests. Under gunicorn the lookup happens only when the same
worker answers both: each worker has its own cache, and nginx opens a new
connection to gunicorn for every request, so any worker may take the picture
request. One that does not hold the picture renders it exactly as before this
change, and the markers worker has then paid one extra PNG encode (a few
milliseconds at most) and one slot of its cache for nothing.

The PNG route first checks that the record still exists (one indexed `SELECT`;
a delete served by another worker drops this worker's copies and answers 404).
A picture this worker holds under the requested `key` is then sent as it is,
with `Cache-Control: private, max-age=86400`, without reading the record: the
key digests everything the picture depends on, so the bytes kept under it are
that picture, and re-deriving the key would only repeat what the name says. A
key it does not hold — evicted, kept by another worker, or never issued — gets
the picture rendered from the record as it is now: `private` when the key names
that placement, otherwise `no-store`, so bytes are never stored under the name
of a placement they do not show. The middleware lets that header stand. A `seq`
parameter from pages loaded before this change is accepted and ignored.

The page fetches the JSON again when the edit counter moves and asks for the
picture by key, so undoing back to an earlier placement finds it in the browser
cache, and changing the insert setting — which moves nothing — re-keys the copy
in hand instead of fetching it. After the block is moved the page reloads the
close-up through `refreshBlockView`, one load at a time: a nudge during a load
only notes that one more is wanted, so a run of quick nudges fetches the
close-up of where the block ended up rather than one for every step, and only
the newest load may draw (`S.blockViewGen`), so a late answer can never put an
older placement — or another analysis's close-up — back on screen. The contrast
slider re-maps the downloaded picture through a 256-entry table and costs no
traffic.

## Two dates, kept apart

The acquisition time comes from the scanner's clock; the upload time comes from
this server's. Neither carries a timezone, and a detector's clock can be unset,
reset during service, or wrong. Storing one date and falling back to the other
made those cases indistinguishable after the fact, which on a large dataset
loses the real order irrecoverably.

Both are now stored, exported and displayed separately, and an acquisition time
that is absent or impossible is labelled rather than replaced. Ordering still
falls back to the upload time so nothing drops out of a listing — but the
fallback is visible, and either date can drive the table and the trend axis.

## The same file is not analysed twice by accident

Uploads are fingerprinted with SHA-256, and a file already present is refused
with the existing record rather than silently duplicated: a duplicate shows
twice in History and counts twice in every trend, quietly distorting the
statistics it feeds.

The refusal offers three choices — continue the existing record (the primary
action), deliberately analyse the file again as a separate record, or cancel —
as three buttons rather than a two-way confirm. With two buttons, "cancel" would
have to mean "create the duplicate", so dismissing the dialog would cause the
exact outcome the check exists to prevent. Deliberate re-analysis remains
available because it is legitimate after an algorithm or definition change,
though re-running the existing record is now the better way to get new numbers.

**Asked before sending, not after.** Seven weeks of the field audit log show no
false positive — every refusal was a byte-identical re-upload — but each one
cost the operator two minutes of a 512 kbit/s link to learn it. The browser
therefore hashes the chosen file (`crypto.subtle`) and asks
`POST /api/upload_check` first. Nothing acts on that hash — it can only reveal
records the same login can already read, only hashes actually asked about are
answered, at most 32 — and the real check still runs on the received bytes, so a
browser that cannot hash (plain http, where `crypto.subtle` does not exist) or a
zip, whose members are only hashed on the server, falls through to the old
refusal with the same dialog.

**Both sides shown.** "Already analysed" answered a question nobody asked. The
dialog shows the chosen file (name, size, modified time — two exports called
`003_0000.dcm` differ only in that) next to the stored record (a 200 px JPEG
thumbnail, id, labels, operator, both dates, progress, result), so the operator
can see whether it is the thing they meant and what became of it.

**Same exposure, different file.** A re-export from the archive has a new hash
but the same SOP Instance UID. That is only remarked on after the upload,
never refused: a UID can legitimately repeat on a misconfigured detector.

## Manual correction is a first-class path

Automatic placement is seeded from the stored phantom definition, so a
different phantom build will place some patterns wrongly. That is expected and
cannot be fully engineered away — the software cannot know a layout it has never
seen.

What it can do is make correction reliable: every ROI can be moved and rotated,
the measurement follows, and the change is recorded in the audit trail with both
the automatic and the manual position. Detection quality determines how much
work the user does, not whether the result can be trusted.

Corrections are also **undoable** and **remembered per phantom**, so the cost of
a mis-detection is paid once rather than on every scan.

**Points put on the image are proposals until applied.** Three things ask for
clicks on the image: the phantom's corners (Stage A), the low-contrast block's
corners and a field edge (Stage C). All three go through one table in the page
(`PICKERS` in `app.js`) and one strip of controls under the image: only a
left click that does not move places a point; middle-drag, right-drag and
Space+drag pan and the wheel zooms while picking; a placed point can be dragged
or nudged with the arrow keys; the last one can be taken back; and nothing is
sent until Apply (or Enter). A one-point job — the field edge — re-places its
point on the next click; a four-point job ignores clicks once full. The block
used to be placed on the fourth click and a field edge on the first, so a
misclick — the field team's main complaint about clicking — went straight to
the server and could only be undone afterwards. Leaving the step drops points
nobody applied. The endpoints did not change: Apply sends exactly what the
click used to.

Values the operator types belong in fields on the page, not in browser prompts.
A prompt cannot show the record being acted on, cannot validate before sending,
and cannot display the server's refusal — a mistyped angle became `NaN` and
silently nudged the geometry, and a refused deletion looked like a successful
one.

## Fixed ROI sizes where results are compared

Wedge step ROIs are a fixed size for every step rather than fitted to each
step's height. The printed steps are not equal heights, so per-step sizing would
average a different area in each one and make the same step incomparable between
phantoms. Each step reports its measured height and whether the fixed ROI fits.

## Display scaling relative to the image

Window width and centre are expressed as a fraction of each image's own measured
value range, not as absolute pixel values. Detectors differ in bit depth — 12
bits on one unit, 14 on another — so a control calibrated to one blanks out an
image from the other entirely.

The slider is previewed in the browser: the eight-bit picture on screen is
re-mapped through a 256-entry table from the window it was rendered with to the
one asked for, instantly and without traffic. The exact render is fetched once
the slider has been still for 400 ms and replaces the preview. Every pause used
to fetch a fresh megabyte — about fifteen seconds at 512 kbit/s — which is most
of why finding the low-contrast discs was reported as painful.

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

## Two words for "no answer", and a verdict that says what it skipped

One word, `n/a`, used to cover two opposite situations: a test that **does not
apply** to the image (X-ray field alignment when no field edge is in the
picture, which is how every exposure so far has been taken) and a test that
**should have been measured and was not** (corners not found, discs unreadable,
a flat wedge, patterns that could not be placed). And the overall rule ranked
`n/a` above `pass`, so all six Philips reference scans read `n/a` although every
test passed — and a baseline was set from such an analysis in the field.

Simply ranking `n/a` below `pass` would have been worse: a scan on which nothing
could be measured would then read "pass". So the two meanings got two words:

| Per-test status | Written by | In the overall verdict |
|---|---|---|
| `not applicable` | only `geometry.field_status`, when no field edge was found on any side | skipped |
| `not measured` | dimensions without corner marks, low contrast with no measurable disc (or a disc missing), uniformity with a square missing, a wedge with no R², any test with no measuring areas (`pipeline.compute_all`) | counts as `warn` |
| `error` | a test that raised, or was not reached before the time limit | ranks with `fail` |

`pipeline.overall_status` takes the worst of `status`, `field_status` and
`dimension_status` across the tests with the rank pass < n/a < warn < fail =
error. `not applicable` is skipped; `not measured` and any word it does not know
count as `warn`, because ranking an unrecognised word as a pass is how an
unmeasured test would slip through. The overall vocabulary therefore does not
grow — it stays `pass`, `warn`, `fail`, `error`, and `n/a` only when nothing at
all was judged — so History filters, exports and every stored record keep the
words they had.

**The verdict names what it skipped.** A "pass" that silently left a test out
reads as though that test passed too, which on a signed report is a false
statement. `pipeline.verdict_notes` turns each `not applicable` into a phrase
("X-ray field alignment not checked (no field edge found in the image)") that
the results page, the printed report and History print beside the verdict.
Only `not applicable` produces a note; a `not measured` test already pulls the
verdict down and is explained wherever the warning is.

History gets the notes without the results travelling: `Store.list_all`, given
`status_fields`, has SQLite pick the `(test, key)` statuses listed in
`pipeline.STATUS_FIELDS` out of `results_json` with `json_extract`, one parse
per row, in the listing's own `SELECT` — the results themselves are about
130 kB per analysis, and only the note travels. It used to be a second query
(`Store.result_statuses`, by id). But most columns the listing reads (status,
labels, validation, exposure values) are stored after `results_json`, so
reaching them already makes SQLite walk every overflow page of each row's
results, and the second query walked them all a second time. Reading the
statuses in the same pass saves about 25 ms at 200 analyses, with no change to
the schema. A results blob that is not valid JSON yields no statuses for its
row. On an SQLite built without its JSON functions the combined query fails
and is run again without the statuses, so History lists everything with no
notes rather than failing: the note is a courtesy, History is not. The notes
are exactly the ones the two queries gave; a test keeps the old query as the
reference, over legacy `n/a` rows, damaged blobs and the missing JSON
functions.

**Stored analyses are not rewritten.** Records measured before the split keep
`n/a` for both meanings; nothing already finalised or signed changes its wording.
`n/a` keeps its old rank between `pass` and `warn`, so re-reading one reproduces
the verdict it was stored with, and a re-run produces the new words. In the page
a status chip's class is the status with every non-letter removed (`na`,
`notapplicable`, `notmeasured`); `not measured` is coloured like a warning and
`not applicable` in the grey of the old `n/a`, the same in the report and the
comparison report.

## A reference per phantom, and it can be given up

A constancy test needs something to be constant against. That reference used to
be scoped to the protocol signature alone, which quietly assumed one phantom per
machine. It is not true here: the reference set contains two builds whose
internal features sit millimetres apart, both valid, and a site can run both on
one unit. Marking the second one's reference silently demoted the first one's,
so one of the two phantoms could never be trended at all.

The scope is now the phantom AND the protocol. The protocol half stays because
pixel values in processed radiographs are not proportional to dose, so comparing
across kV, detector or processing is meaningless whatever the phantom was.

A reference is also removable. It was not, and a reference is a judgement — a
scan chosen before anyone noticed it was poor should not be permanent just
because the flag was one-way. Removing leaves the phantom with none, which is a
legitimate state and the one every phantom starts in. Setting and clearing are
both audited, and both name the phantom, because the operator needs to see that
the decision was scoped to one phantom rather than to everything on the machine.

Finalising an analysis no longer touches the flag. It is pressed more than once,
and a second press must not undo a decision made deliberately somewhere else.
The reverse does hold: marking a reference finalises the analysis, because a
scan every later scan is judged against is a decision, not a draft. An exposure
that failed the image-quality check cannot become a reference on an ordinary
user's say-so — only by an administrator's override, with a written reason (see
the privilege levels below).

## Detail is one click away, not on the page

The wizard is used by people who are not medical physicists. A step that opens
with four dense tables asks them to decide which numbers matter, which is the
one thing they cannot do — and the risk is not confusion but false confidence:
a table nobody reads is indistinguishable from a table that says everything is
fine.

Steps B, D and E therefore lead with a verdict in words — what was found, what
needs attention, and which test — and fold the measurements behind a native
`<details>`. Native because it needs no script, survives the re-render each
stage does, is keyboard-accessible, and prints expanded, so the printable report
stays complete even when the screen is not. In step E anything that did not pass
opens itself, so the detail a reader needs is never the detail they have to go
looking for.

Nothing is removed. The same numbers are one click away for whoever wants them,
which is what makes the summary safe to trust.

## Results are stored, not recomputed

An analysis keeps the numbers produced by the algorithm and definition current
when it was computed, together with both version stamps.

A QA record that changed silently under a software update would be worthless,
especially one that has been signed off. Because the source file is also kept,
results can be recomputed deliberately with `manage reanalyze` when an
improvement warrants it.

## Re-running rewrites the record, and keeps what it replaced

A re-analysis stays on the **same record**. The alternative — copying to a new
one that supersedes the old — was considered and rejected: every consumer that
selects on "has results" would need a second condition, and any one of them
missed would double-count a single exposure in a trend. That is precisely what
the duplicate check exists to prevent, and precisely what re-uploading produced.

Rewriting a finished record is only defensible because the previous state is
kept. `analysis_revisions` holds one compressed snapshot of the registration,
geometry and results, plus the ruling, the finalised stamp and the reference
flag, so an interrupted re-run can be put back exactly — including the
signature that was given for those numbers. Five are kept per record, and the
first is pinned: the numbers as originally computed are the ones worth keeping
longest.

The snapshot and the state change happen in one transaction, so an analysis is
never found half-reworked. Registration runs **before** that transaction opens:
it is seconds of CPU, and holding the write lock for it would block every other
operator's edits.

A re-run reopens the record — finalised stamp cleared, ruling withdrawn —
rather than carrying them across. A ruling that outlived the numbers it was
given for would be worse than no ruling at all. The reference flag is the one
thing kept, because clearing it would leave the phantom comparing against
nothing until the re-run finished.

`Store.begin_rerun` knows two cases, not three: "registration" replaces the
transform, drops the geometry and its undo history and reopens at Stage A;
"points" and "results" keep both and reopen at Stage C. The difference between
the last two is recorded in the revision and the audit trail but not acted on —
the operator walks C → D → E either way, and confirming C stores the phantom's
layout again only if the box is ticked — which it is by default when the stored
layout came from this analysis, and is not when it came from another. `POST …/rerun/cancel` restores the newest revision only
while the record has no results; once the re-run has produced new numbers the
earlier revisions stay in `analysis_revisions` but nothing in the interface
restores them.

The re-run is offered in two places, Stage F and each History row that has
results, and both open the same panel and send the same request. The History
listing carries two extra fields for this, both cheap: `has_results` (whether
`results_json` is set — not the results, which the listing never reads) and
`protection` (from `Store.protection`, the same list the opened record carries).
So a row asks for the administrator password in exactly the cases Stage F does,
and the server makes the final decision either way. After the re-run the page
opens the record through the usual rule for choosing the opening step, which
reads the step `begin_rerun` just wrote — the step the endpoint reports.

## Unfinished work can be thrown away; finished work cannot

"Finalised" is a real state (`finalized_at`, `finalized_by`), set by Finalize,
by marking a reference, and by recording a ruling — the first time is kept on
later presses. `Store.protection` is the one definition of "protected":
finalised, signed off, or the reference. Everything that decides on it — the
page's Discard button, the discard and delete endpoints, the re-run gate, the
History row's re-run — asks that one function, so they cannot disagree.

**Discard** (`POST …/discard`, `{confirm: true}`) needs no password: the field
audit log shows the same file deleted and re-uploaded four times in seventy
minutes because clearing a mistake needed the administrator, and on an
installation without an administrator password it could not be cleared at all.
It is a hard delete through the same `Store.delete` as the administrator path,
with the protection check **inside** its write transaction, so a colleague
finalising the record between dialog and confirmation wins. It is audited as
`event=delete` with `"mode":"discard"`, so one grep still finds everything that
ever removed data. Once a record is protected, the administrator delete applies
unchanged.

**Finalised locks the measurements.** `_require_unsigned` refuses every path
that would change the registration, the geometry or the results — re-register,
propose, ROI move and rotate, block placement, insert orientation, field edges,
undo, redo, reset, confirming Stage C, compute — on a finalised record as well
as on a signed-off one, and its message points at "Re-run analysis", which keeps
what it replaces. Editing straight through a finalised record used to drop its
results silently and take it out of every trend. Labels stay editable.

## An edit declares the state it was made from

Measuring-point edits were already serialised — each runs in its own write
transaction, so two overlapping edits cannot corrupt the stored geometry. But
serialising only decides **which one silently overwrites the other**. With a
single shared login two operators can hold the same analysis open, and the
audit log shows them doing it. The second one drags a point, the first one's
correction disappears, and nothing says so.

So an edit may carry `expect_seq`, the state of the measuring points it was
computed from. It is compared **inside** the same write transaction that
performs the edit — checking beforehand would leave exactly the race it closes,
since both edits could pass a check made outside the lock and then both write.
A mismatch raises, the request answers 409, and the page reloads so the
operator sees the current points before redoing their change.

It is optional by design. `manage reanalyze` and the command line overwrite
deliberately, and an older browser must not be locked out of editing. What the
declaration buys is that a *client which sends it* can no longer destroy work
it never saw.

This is optimistic concurrency, not locking. Locking would need an owner, and
there is no per-user identity to own anything.

## A reload restores identity, not activity

The browser remembers which analysis was open — an id and a few words to
describe it — and nothing else. The reloaded page lands where it always did,
fetches nothing, and offers a banner. The record itself lives on the server,
which stays the single source of truth.

The temptation is to restore the operator to exactly where they were. That is
the one thing this must not do. Drawing step E starts a measurement, so a
record interrupted there would begin measuring again the instant it was
restored — and since reloading is the operator's way out of a page that is
misbehaving, an automatic resume would take that escape away and turn it into a
loop. So resuming is a button, never automatic, and a record interrupted while
measuring reopens one step earlier.

The note lives in the browser, not on the server. That is not a privacy
mechanism: there is a single shared account, and the software genuinely cannot
tell two operators apart. It simply means two people on two machines keep
independent memories, while two people on one machine share it — hence the
banner naming the file, phantom, step and operator, and hiding it destroying
nothing. For work handed between machines, which the audit log shows happening,
the server-side list of unfinished analyses is the answer; it is everyone's
list and says so.

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

## Comparison pictures: drawn once, kept on disk

The field asked to compare the images themselves, not only their numbers.
`thumbnails.py` makes five pictures per scan — whole phantom, line-pair strip,
wedge, low-contrast block, uniformity squares — and `comparison_report.py` lays
them out as a table, one column per scan.

**Sampled in the phantom frame.** Each picture is sampled through the
registration transform (`thumbnails._sample`) rather than cut out of the image,
so scans taken at 0°, 90° or 180°, or face down, come out the same way up and one
region lines up column after column. The scan is block-averaged to roughly the
target resolution first (`_Source`), which is the cheap anti-aliasing filter
and keeps noise — the most expensive thing to hold in a JPEG — out of the
picture. The line-pair corridor runs through the groups' **design** positions,
so a group mis-placed on one scan shows as exactly that; it shows the strip as
it is and does not depend on the parked line-pair question below.

**The window travels with the picture.** Each picture is encoded with its own
generous window (0.2–99.8 percentile) and carries the scan values that 0 and 255
stand for (`lo`, `hi`). The page re-maps every picture of a row onto one shared
window with a 256-entry table, so a brighter picture really is brighter without
a second copy crossing the link. The shared window is per protocol signature —
the same rule that scopes a baseline (`_window_sources`): the group holding the
selection's reference scan takes that scan's window, any other group its own
reference's or its first scan's; a scan alone on its protocol keeps its own
window, because on another detector's window it comes out black or white. The low-contrast picture is the
Stage C close-up, normalised to itself (`window: "self"`), and is never re-mapped.

**Budget.** JPEG at quality 75, or PNG where that comes out smaller. All five
pictures of a reference scan come to 42–54 kB; the 70 kB budget per scan and
0.8 MB per ten scans are asserted in `tests/test_comparison_pictures.py`.

**Disk cache.** Drawing needs the decoded scan — seconds and tens of megabytes
— so the pictures are kept under `data/thumbs/<analysis-id>/<key>.json`. The
key digests everything they depend on (`thumbnails.cache_key`):
`PICTURE_VERSION`, the algorithm and definition versions, the file's SHA-256,
`geometry_seq`, the registration transform and the block centre and angle.
The sequence number alone is not enough: it restarts at 0 when a re-run starts
from registration and repeats after an undo followed by a new edit. The
transform and block are what the pictures are drawn from, so they are in the
key; the sequence rides along so any measuring-point change re-renders.

- Written to a temporary file and renamed into place, so another worker sees
  the whole file or none of it; after a successful write every older `.json`
  in that directory is removed. A failure to write costs a re-render next time,
  never the page.
- A region that failed to draw is shown as a labelled gap and the set is not
  written, so the next report tries again.
- Invalidation is by key: an edit, a re-registration, a re-run, a new algorithm
  or definition version, or a bumped `PICTURE_VERSION` (bump it whenever the
  same inputs would now draw a different picture).
- `Store.delete` — both discard and administrator delete — removes the
  directory with the record; a comparison that finds its record deleted while it
  was drawing removes what it just wrote (`thumbnails.forget`). `Store.thumbs_dir`
  returns None for an id that is not a plain token, because the directory is
  removed wholesale.
- Derived data: not backed up, rebuilt on demand (see MAINTENANCE.md).

Scans decoded for pictures are not put in the worker's scan cache
(`_scan(aid, cache=False)`): a ten-scan comparison would otherwise pin some
70 MB per scan in every worker that served it, with nothing to release it. And
whatever goes wrong with one scan becomes labelled empty cells for that scan
(`thumbnails.unavailable`); the report never fails because of a picture.

## Comparison charts are encoded while the next is drawn

Each chart of the comparison report is drawn with matplotlib and then reduced to
a 64-colour palette PNG (`_b64`), about 25 ms of PIL work per chart that the
next chart used to wait for. During `build_comparison_report` that palette step
(`_palette_b64`) runs on two helper threads of the report's own, while the
calling thread draws the next chart; each chart holds a placeholder in the page
until the page is composed, and the placeholders are then replaced by the
finished pictures. Two reference scans went from 5.2 s to 4.6 s; the drawing is
the larger part and stays on one thread, because matplotlib is not safe to use
from several.

The page is byte for byte the one encoding each chart in turn gives — the same
encoding, only earlier — and a test builds it both ways and compares. A chart
whose palette step fails fails the report with that chart's error, the first in
drawing order, exactly as in turn, even when the page went on to fail somewhere
else afterwards. The helper threads end with the report however it ends (the
pool is a `with` block per request), and the batch is found through a context
variable set only for the duration of the call, so a chart drawn anywhere else
is encoded where it stands. A placeholder carries a random string drawn afresh
for every report, so nothing typed onto the page — a site or phantom label — can
pass for one.

## One inline script, admitted by its hash

The comparison report is meant to be saved from the browser and read with no
server behind it — nothing is e-mailed from the application — so its one script
(the shared row windows and the click-to-enlarge lightbox) is inline. The
Content-Security-Policy everywhere else is `script-src 'self'`. For
`/api/comparison_report.html` alone the middleware adds `'sha256-…'` of that
exact script text, `COMPARISON_SCRIPT_CSP`, which `comparison_report.py`
computes from `_PICTURE_SCRIPT` at import — so editing the script can never
leave a stale hash behind that silently switches it off, and nothing else inline
can run on the page. `'unsafe-inline'` is never used.

Without the script the page still reads: every picture shows in its own window
and the row notes say so; the per-row switch stays hidden. A copy opened from
disk carries no CSP header and the script runs. The enlargement of a re-mapped
picture is a `data:` URL, which `img-src 'self' data:` already admits.

## Two privilege levels, with the administrator password per action

A user can run analyses and read everything, and can throw away their own
unfinished work. Deleting or re-running a protected analysis, validating, and
overruling the image-quality check require a separate administrator password,
entered at the moment of the action rather than at sign-in.

This lets an administrator work as an ordinary user and supply the credential
only when making a decision that an ordinary user must not make. Deletion — and
a re-run of a protected analysis — additionally requires a written reason, which
is what the audit log keeps: the password establishes the right to destroy or
rewrite data, the reason records why. The password is always checked before
the reason, so a missing reason cannot be used to learn whether a password was
right, and every outcome is audited. (`_require_admin` holds that sequence for
the re-run and the quality override; the delete and validation endpoints still
carry their own copies of it.)

**The image-quality check can be overruled, by an administrator only.** An
exposure that fails it (`quality.blocks_reference_use`) is still analysed, but
it may not become the reference scan or the phantom's stored measuring points
on an ordinary user's say-so. Almost always that is right; the exception is the
one exposure a site could make that week, looked at by someone who knows what
they are looking at. So `POST …/baseline` and the Stage C confirm accept
`admin_password` and `reason`, and `_quality_override` passes them through
`_require_admin` — password before reason, throttled, and refused outright where
no administrator password is configured, the same rule as delete. The check
runs before anything is written, so a mistyped password leaves Stage C
unconfirmed and the panel can simply ask again. A password sent for an exposure
that passed is ignored rather than checked: there is nothing to overrule, and
it must not count towards the lockout. The baseline refusal is a 409 carrying
`quality_refused`, the failed checks and `override_available` (false with no
administrator configured), and the Stage C confirm reports the same through
`profile_blocked_by_quality`, so the page can show what would be overruled
instead of parsing a sentence, and does not offer a password field nobody can
fill in. An override is recorded with the reason and the failed checks, both in
`logs/audit.log` (`"override":true`) and in the record's own trail; the
record's quality verdict and its **image** badge stay as they were. It answers
the quality check and nothing else: a finalised or signed-off record still
cannot store its layout.

The reason replaced a requirement to type the analysis id back. That guard was
theatre — a copy-paste satisfied it — and it had a cost: its refusal appeared
only as a status message that cleared itself, so a deletion that had not
happened could be taken for one that had. The confirmation panel now shows the
record being destroyed and keeps every refusal on screen until it is answered,
which is the property the id-typing was reaching for.

The approver's **name** is recorded separately from the password: a shared
credential establishes the right to sign off, not who did.

A ruling also freezes the analysis. Every path that would change the geometry,
the registration or the results is refused while a ruling stands. This became
necessary once a geometry edit correctly dropped results computed from geometry
that no longer exists: on a validated analysis that left the ruling, the name
and the date intact with the numbers gone, and the record then vanished from
every trend and export, all of which select on completed results. The reanalyze
CLI already skipped signed-off analyses; the web path now matches it.

Recording a ruling also finalises the record, and withdrawing a ruling does not
un-finalise it — someone still declared the numbers done. So withdrawing alone
no longer unlocks editing; the way to rework a signed-off analysis is a re-run,
which takes the administrator password and a reason, withdraws the ruling
itself and keeps the previous state. (The Stage C lock notice and the refusal
message for a signed-off record still say "withdraw the validation first"; that
wording predates finalising and is out of step with the behaviour.)

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
involved. The comparison report embeds its pictures and its one script too, so
a copy saved from the browser keeps both.

## Pictures for looking are lossy; numbers never come from them

Every byte crosses a field link at about 512 kbit/s, and the two heaviest
downloads were pictures nobody measures from.

- **The viewer's picture** is `image.jpg` (quality 75, progressive), about
  0.1 MB for a reference scan at 1600 px against 1.1 MB as PNG. `image.png`
  stays for anything that wants the exact eight-bit render; both come from one
  `_render_view`, so they cannot drift apart in window or size — and the size
  matters, because the page places every outline through the picture's width
  over the scan's. The low-contrast discs, where a few grey levels decide what
  an operator sees, are placed on their own lossless close-up.
- **The printed report** embeds the annotated overview as JPEG (quality 80
  with full colour resolution — chroma subsampling smeared the magenta disc
  rings into a blur) and its charts as 64-colour palette PNGs (the max-coverage
  quantiser keeps white white; JPEG was larger than PNG for every chart and
  blurs text). A reference report went from 1.7 MB to 0.39 MB with every number
  and verdict unchanged. `report._photo` re-encodes anything that is not already
  JPEG, so no caller can put the megabyte back.
- **Rendered scan images are privately cacheable** (`Cache-Control: private,
  max-age=86400`): the upload never changes and the URL carries everything that
  varies the picture, so the same URL is always the same bytes. Private because
  it is patient-adjacent imagery that must not sit in a shared proxy. Every
  other `/api/` response stays `no-store`; the close-up decides for itself (see
  above).
- **Text is compressed in the application** (`GZipMiddleware`), not in the
  proxy, because the proxy configuration is deployment, which this work does
  not change. Images, zips and DICOM are excluded — already packed.

## Uploads are packed in the browser and proven identical on arrival

At 512 kbit/s the transfer is nearly all of an upload's time, and most
detectors write their pixels uncompressed. The browser therefore packs the
chosen file with gzip — `CompressionStream("gzip")` on `file.stream()`, built
into the browser, so there is no library to download — and sends the packed
copy on the same `POST /api/analyses` with three more form fields: the
original's SHA-256 (`original_sha256`, the digest the duplicate pre-check
already took, reused rather than hashed twice), its size (`original_size`) and
its name (`original_name`), plus `encoding=gzip`. No new route, no new service,
nothing in the proxy. Any other `encoding` than empty, `identity` or `gzip` is a
400: guessing would mean storing bytes the server did not know how to read
back.

**Decided per file, never worse than before.** Field projects meet a different
detector every time, so nothing is assumed about the detector. The first 1 MiB
is packed as a probe; unless it comes out at 90 % of its size or less, the file
is sent as it is at once. Otherwise the whole file is packed and kept only if it
too is at 90 % or less. A `.zip` (CD export) is never probed. Measured with gzip
level 6 on all 38 real scans, every one unpacking byte-identical:

| Detector | Transfer syntax | As it is | Packed | At 512 kbit/s |
|---|---|---|---|---|
| Fuji (field) | Explicit VR Little Endian, 10-bit | 7.5 MB | 2.9 MB (1.1 MB over-exposed; 2.2 MB average) | 118 s → 45 s |
| Carestream | Explicit VR Little Endian | 15.1 MB | 5.8–8.4 MB, 7.1 MB average | 236 s → 111 s |
| Philips | JPEG Lossless (compressed inside the file) | 7.4 MB | not packed: the probe reads 99 % | unchanged |

The probe predicts the whole file on every one of them (a test holds that with
the page's own thresholds). In a real browser (Edge 153) the Fuji packed in
0.16 s, the Carestream in 0.53 s, and the Philips was recognised in 34 ms. The
worst case for an unknown detector is no gain.

**Proven identical before anything else sees it.** `ingest.unpack_gzip` streams
the decompression (`zlib.decompressobj` in gzip-only mode, output taken in
1 MiB steps) and requires four independent agreements:

1. gzip's own CRC32 and length trailer, which zlib checks at the end of the
   stream — so a stream that never reaches its end is refused, not accepted as
   a shorter file;
2. exactly one gzip member and nothing after it (Python's `gzip` module would
   concatenate a second member: "the file plus whatever was appended");
3. the unpacked length equals `original_size`;
4. the SHA-256 of the unpacked bytes equals `original_sha256`, which was taken
   from the file on the operator's disk before any of our code touched it.

Only if all four agree does the upload continue, exactly as for a file sent as
it is: the stored file is the original, the recorded sha256 is the original's,
and the duplicate check and the analysis read the original — so the same file
sent packed or unpacked is recognised as the same file. Anything else is a 400
reading "The file was damaged on the way — nothing was stored. Please send it
again.", an audit line with `outcome=damaged` and the reason, and nothing
stored. Each check is guarded by a test that fails when the check is removed.
Unpacking and verifying takes at most 0.15 s for a Carestream scan and runs in
the threadpool, like the decode, because the endpoint is async.

**After a damaged refusal the page stops packing.** The server cannot tell
damage on the link from the browser's own packing going wrong, and packing
that goes wrong once would go wrong on every attempt — an operator could then
never upload from a browser that worked before packing existed. So this
refusal, and only this one, carries `damaged_transfer: true`, which the page
reads (like `quality_refused`) instead of parsing the English. On it the page
stops packing until it is reloaded, and the next attempt is exactly the
unpacked upload of before. Nothing is re-sent automatically: the whole file
again on a slow link is the operator's decision. A refusal that packing had
nothing to do with — a file that is not a scan, a size past the cap — carries
no flag and leaves packing on.

A malformed `original_size` gets the same audited 400 as a missing one: only
ASCII digits, at most fifteen of them (a petabyte), count as a size.
`str.isdigit()` alone also passes "²" and digit strings past `int()`'s limit,
either of which would otherwise escape as a 500 with no audit line.

**No fingerprint, no packing.** Over plain http `crypto.subtle` does not
exist, so there is no SHA-256. Without it the server could check only CRC32
and the length, which catch a damaged transfer but not a wrong file. The page
therefore does not pack without the digest, and the server refuses a packed
body that lacks it (400) instead of half-verifying it. Such a site keeps
exactly the upload it had; so does a browser without `CompressionStream`, and
any failure while packing falls back to the file as it is.

**Not packed when page and server share a computer.** Served by `run_app.py`
on the operator's own machine there is no link to spare, and packing a 15 MB
scan was measured at half a second of pure waiting, most of it with the page
frozen (upload 2.0 s → 1.4 s without it). So `packForUpload` sends the file as
it is when `location.hostname` is a loopback name — `localhost`, `127.x.x.x`,
`::1` (written `[::1]`) — *and* the server has said, in the `/api/auth` answer
the page already fetches at start, that it has no sign-in (`S.signInOff`, set
only from an explicit `auth_enabled: false`), and only then
(`servedFromThisMachine`, checked before the probe, so no "Preparing" phase
either). A LAN address or a server name is a network, however fast, and keeps
packing. The name alone is not enough: an SSH tunnel or a port forward to a
deployed server is opened at `localhost` too, and would send every scan
unpacked over the slow link. No sign-in is `run_app.py`'s default, a server
without sign-in must never be exposed to a network at all (`run_app.py` and
gunicorn both say so at start), and production turns sign-in on — so a tunnel
to a deployment keeps packing, and so does a page whose `/api/auth` has not
answered or failed. The one gap left is a server deliberately run with sign-in
off *and* reached through a tunnel, a setup gunicorn already reports as an
error at start. A local `run_app.py` with sign-in
turned on packs as before: slower on the same computer, never wrong. The
SHA-256, the `upload_check` pre-flight before sending, and the server's gzip
path are all unchanged: the upload is simply the unpacked one every browser
without `CompressionStream` has always made.

**Decompression bombs.** A few kilobytes can inflate to gigabytes, and the
body-size cap cannot see it because the body really is small. The output is
therefore bounded while it is produced, never afterwards: a declared size above
the upload cap (`max_upload_mb`) is a 413 before any work; output past the cap
is a 413; output past the declared size is refused as damaged — one step
(1 MiB) past the limit at most. A 30 s time budget is a backstop only: deflate's
work grows with what goes in and what comes out, both capped. The existing
declared-size check in the middleware and the measured body cap still apply to
what is sent.

**Progress and Cancel.** The bar gains a first phase, "Preparing the file… —
nothing is sent yet", during which Cancel stops the packing (the stream reader
is cancelled) and nothing is sent; the cancellation reaches the same branch as
an aborted transfer. Sending then reads "Sending 1.2 MB of 2.9 MB (41%, packed
from 7.5 MB)": the percentage and the time left are counted on the bytes that
actually cross the link.

**Audit.** Every upload line now carries `encoding` (`gzip` or `identity`),
`sent_bytes` (what crossed the link) and `bytes` (the original's size, as
before), so a slow upload can be explained afterwards.

## Exposure values: four columns and a one-time backfill

The detector's own account of the exposure (IEC 62494-1) is what tells an
operator at once that a scan was under- or over-exposed — the first thing to
rule out when its numbers look wrong, and the thing that would have explained
one of the first field test's puzzles. All three detectors in use write the
exposure index; the Carestream and the field Fuji also write the target, the
deviation index and a sensitivity.

`ingest.EXPOSURE_TAGS` — `ExposureIndex`, `TargetExposureIndex`,
`DeviationIndex`, `Sensitivity` — are read as **numbers** from the text the
detector wrote (`_dicom_number`: an int when it is one, a finite float
otherwise; blank, malformed or non-finite is absent, never zero). They are kept
in the stored header and copied into REAL columns `exposure_index`,
`target_exposure_index`, `deviation_index`, `sensitivity` (NULL = not
recorded), because History and the CSV exports never read the header blob. The
listing sends only EI and DI.

They are shown in the identity bar, History, the report's identification block
and both CSV exports (four trailing columns in the long one, four trailing rows
in the wide one, so existing spreadsheets keep every column where it was).
Indices are whole numbers and the deviation index has one decimal and its sign;
halves round away from zero, with the digits built by hand identically in
`app.js` (`exposureText`) and `report.exposure_text`, because Python's `round()`
and the browser's `Math.round()` would disagree on −4.75. **Nothing is judged
from them.**

**Backfill.** Records stored before the columns existed have the values in their
own uploaded file. `Store._backfill_exposure` runs once per database, in the
migration branch that has just added the columns. It commits first, so the file
reads do not hold the write lock the other starting workers queue on; reads each
non-image record's `data/uploads/<id>.bin` with `pydicom.dcmread(...,
stop_before_pixels=True)` — milliseconds a record, where decoding the image
would take seconds; adds only the keys the stored header lacks, fills the
columns, and writes with `WHERE meta_json IS <what was read>`, so a concurrent
change wins. It touches nothing else — no results, status, geometry, validation
or protection — which is why it may run over finalised and signed records: what
was signed is the measurements, and this transcribes more of the same file. A
missing or unreadable file is logged and that record shows "not recorded".

`AcquisitionDate` is now kept in the stored header as well.

## MONOCHROME1 turned about the detector's range

In a MONOCHROME1 image a bigger number is darker, so it is turned the other way
up on ingest. It used to be flipped about the brightest pixel of the image,
which made every value depend on the picture: the same object exposed with and
without some unblocked beam would come out offset, moving the uniformity SNR and
the wedge ratio for no physical reason.

`ingest._invert_monochrome1` flips about the detector's own range instead,
taken from `BitsStored` (else `BitsAllocated`) and `PixelRepresentation` —
0…2^bits−1, or the signed range — carried through the rescale:
`(lo + hi)·slope + 2·intercept − value`. When the header declares no bit depth,
or the stored values fall outside the declared range, it falls back to the old
rule, logs a warning, and records which rule was used in the stored header
(`_inversion`: `{"method": "bit depth", …}` or `{"method": "image maximum",
"reason": …}`), so an odd value can be traced later.

On the three readable field (Fuji) scans the image maximum *was* the detector
maximum — 1023 on 77,000–100,000 pixels of unblocked beam — so they come out
identical to the last pixel; only the two completely faulty exposures moved (by
66 and 95). Nothing needed re-running. The change is a guard for future
exposures with no unblocked beam in the picture.

## The local starter is not slowed by Windows power saving

Windows runs a process whose window is in the background on its power-saving
settings (EcoQoS: efficiency cores, low clock). The terminal running
`run_app.py` is in the background the whole time the operator works in the
browser, so every step ran throttled — registration to patterns took 2.3 s
instead of 0.94 s. At start `run_app._run_at_full_speed` therefore opts its own
process out: `SetProcessInformation(GetCurrentProcess(), ProcessPowerThrottling,
…)` with `PROCESS_POWER_THROTTLING_STATE{Version=1,
ControlMask=EXECUTION_SPEED, StateMask=0}` — execution speed is the setting
controlled, and its state is off. Nothing else on the machine changes, and the
setting ends with the process.

It is Windows only (`os.name == "nt"`) and never stops the server starting: on
a Windows too old to know the call (before Windows 8 kernel32 has no
`SetProcessInformation`), when Windows declines, or on any error, the server
simply runs as it always did. The starter says once, at start, which of the two
happened, so a slow session can be told apart from a throttled one. A server
deployment runs gunicorn, which never runs `run_app.py`, and nothing in
`phantom_qa` or `gunicorn.conf.py` reaches the call: a server's power policy is
its administrator's business.

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
- **One phantom definition at a time.** The application loads `msf_v1.json`.
  Supporting several phantom builds in one installation means selecting the
  definition per phantom — recording it on the analysis, and grouping trends by
  it as protocol signature already does.
- **Parked: the line-pair strip on the blue prints.** Nothing changes until
  it is decided; see below.

### Parked: the line-pair strip on the blue prints

**Symptom.** On all 27 blue-print (Carestream) reference scans the line-pair
test fails or warns, and so does the overall verdict. The six Philips scans of
the original phantom and the readable field Fuji scans pass.

**Evidence, from the pixels.** The bar frequency was read at each designed
group position, independently of the software's detection. On the blue prints
the finest bars sit at the design's G1.1 end of the strip and the notched frame
line on the opposite side. On the original phantom (`MSF^PHANTOM001`, folders
20260727 and 20260730, 6 of 6 scans) and the field Fuji phantom (3 of 3 readable)
the strip is in design order. Every registration is correct — four of four
corner markers, not mirrored, the low-contrast block where designed — so this
is not an orientation error of the software.

**What the software then does.** `linepairs._match_blocks_to_groups` assigns the
detected blocks to the groups in order along the strip. When the measured
frequencies do not track the nominal order (correlation below 0.5) it falls
back to a greedy nearest-frequency match in definition order, on the coarse
frequency reading. That reading cannot tell 1.1 from 1.2 lp/mm (1.07 read as
1.13, 1.23 as 1.27), and G1.2 is given the wrong block by a margin of
0.004 lp/mm. The result is the G1.1 / G1.2 swap and the ±10–12 % pitch
deviations on those scans — the software's error, not the detector's.

**Why nothing was changed.** The fix depends on a fact about the physical
object: whether the blue phantoms were really built with the strip end for end.
If they were, the strip direction belongs to the phantom and should be detected
and saved per phantom ID, as the low-contrast insert orientation now is;
changing the matching before that is known would move line-pair numbers on
27 reference scans on an assumption. The question waiting to be answered is a
look at a physical blue phantom next to `MSF^PHANTOM001`, same way up: are the
finest lines at the same end? (It is on the second field test checklist.) Until
then no line-pair code changes, and the benchmark pins today's numbers.

Also parked, not investigated: group 1.6 fails on 8 of the blue-print scans.
