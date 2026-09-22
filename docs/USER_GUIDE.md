# User guide

## Signing in

Open the application URL and sign in with the shared user account. The session
lasts 12 hours by default.

Some actions additionally require the **administrator password**, which is
entered at the moment of the action rather than at sign-in: validating an
analysis, deleting a finished one, re-running one that has already been
finalised, and letting an image that failed the quality check become the
reference scan or set a phantom's measuring points. Throwing away unfinished
work never needs it — see
[Throwing away an unfinished analysis](#throwing-away-an-unfinished-analysis).

---

## Running an analysis

The wizard has seven steps. Nothing is measured from geometry you have not
confirmed, and you can move back at any point.

### Working with the image

| To… | Do this |
|---|---|
| Zoom | Turn the mouse wheel; the image zooms around the pointer |
| Move the view (pan) | Drag with the **middle** button, drag with the **right** button, or hold **Space** and drag with the left button. Outside the placing modes a plain left drag on the image pans too |
| Place a point, move a measuring area | **Left** button only |
| See the whole image again | **fit** above the image |

The middle and right buttons never place or move anything, so a middle click
meant as a pan cannot drop a corner by accident. The browser's right-click menu
is switched off over the image, because it would cover the point being placed.

Overlay layers can be toggled individually below the image.

The **W** (width) and **C** (centre) sliders are **relative to the image's own
value range**, shown as a percentage with the resulting absolute values beside
them. Detectors differ in bit depth — 12-bit on one unit, 14-bit on another — so
a fixed absolute scale would white out an image from the wrong device entirely.
**auto** returns to the automatic window.

Moving a slider changes the picture at once, without waiting for the
connection. A moment after you stop, a sharper copy of the picture at the new
window replaces the preview. The picture on screen is only for looking at:
every number is measured on the original scan on the server.

### Upload — choose the file and identify the scan

Choose the scan file and fill in the identification, in either order. Nothing is
uploaded until you press **Upload & analyse**.

| Field | Purpose |
|---|---|
| **Site** | Groups analyses for trending. Required for grouped comparison |
| **Phantom** | Identifies the physical phantom. Required for grouped comparison, and the name under which its measuring points and low-contrast insert setting are saved |
| Operator | Optional |
| Notes | Optional |

Previously used values appear as suggestions, and the last values are remembered
for the rest of the session so a batch of scans need not be retyped. A warning
appears if both Site and Phantom are empty, because such an analysis will not
appear in any grouped trend.

Once a file is chosen, the box under the drop zone shows its **name, size and
modified time**. X-ray units often export every scan under the same name
(`003_0000.dcm`), so the modified time is what tells two of them apart before
you spend minutes sending the wrong one.

Labels can be changed later at any time — see *Correcting the identification*.

#### Is this scan already here?

Before anything is sent, the browser works out the file's fingerprint and asks
the server whether it already holds that exact file. The question costs a few
hundred bytes; sending the file costs minutes on a slow link.

If the file is already here, a panel **This scan is already here** shows both
sides:

| Left: the file you chose | Right: the analysis already here |
|---|---|
| file name, size, modified time | a small picture of the stored scan, the analysis id, file name, size, site / phantom, operator, when the exposure was taken, when it was uploaded, how far it got, and its result |

Three choices:

| Button | Effect |
|---|---|
| **Continue this analysis** (the main one) | Opens the analysis that already holds this file, at the step it had reached. Nearly always what you want |
| **Analyse the file again as a separate record** | Sends the file and creates a second record — only for a deliberate re-analysis. It appears twice in History and counts twice in every trend |
| **Cancel** | Does nothing. Escape or a click outside the panel mean the same |

The check before sending needs a secure (https) connection and does not look
inside zip files. Where it cannot run — a plain-http address on a local network,
or a zipped CD export — the server still checks the file once it has arrived,
and the same panel appears then; only the transfer time was not saved.

A different file carrying the **same exposure** — for example a scan exported
twice from the archive — is not refused, but after the upload a note names the
other analysis made from that exposure. If it is a re-export of the same scan,
discard one of the two, or it counts twice in every trend.

Re-uploading a file whose record was deleted or discarded is allowed and creates
a new one — with no geometry, no edit history and no stored layout carried over
from the old record.

#### While the file is sending

A progress bar stays on screen until the upload ends:

> Sending 3.1 MB of 7.5 MB (41%) · about 1 minute left

The time left is averaged over the whole transfer so far, so it does not jump
about when a field link stalls for a moment. When every byte has gone the bar
reads **Sent — the server is reading the scan…**; decoding and locating the
phantom take a few seconds more.

- **To stop an upload**, press **Cancel** beside the bar. Nothing is stored and
  the file stays selected, so trying again costs nothing but the press. Cancel is
  greyed out once the file has been sent, because stopping would no longer save
  anything.
- **If the connection drops**, the page says so: nothing was stored, and the scan
  can be sent again.

A zipped CD export can hold several images. Each becomes its own analysis; the
first opens, and the others are in History.

### A — Registration

The program locates the phantom, resolves its orientation (any rotation,
mirrored or not) and reports the fitted outline, rotation, scale and the
per-side landmark residuals.

Check that the red dashed outline follows the phantom edge. If detection failed,
place the corners yourself — see below.

If this step opens with a red panel, the exposure itself failed the image-quality
check — see [When the image itself is the problem](#when-the-image-itself-is-the-problem).

#### Placing the corners by hand

1. Press **Manual corners…**. The controls appear under the image, so the
   picture stays in view while you work.
2. **Left-click** each of the four phantom corners, going round the square —
   clockwise or anticlockwise, starting at any corner. A click only counts if
   the mouse does not move while the button is down; a left press that travels
   is a drag, not a point.
3. Zoom and pan as usual while placing: wheel to zoom, middle-drag, right-drag or
   Space+drag to pan.
4. **To correct a point**, drag it with the left button. Or press on it once to
   select it (a white ring appears) and nudge it with the arrow keys — one screen
   pixel per press, ten with Shift held.
5. **Undo last point** (or Backspace) removes the most recent point; **Start
   over** removes all four; **Cancel** (or Escape) leaves the registration as it
   was.
6. Nothing is sent until you press **Apply corners** (or Enter), which becomes
   available once all four are placed.

The phantom is then registered again from your corners and step A shows the new
outline. The image-quality check is repeated as well — manual corners can rescue
a scan the automatic fit had mangled. Press **Confirm registration ✓** to carry
on; the patterns are detected afresh.

The same controls appear, and work the same way, whenever you put points on the
image: for the corners of the low-contrast block and for a field edge in step C.
Points you have placed but not applied are dropped if you move to another step,
and nothing changes.

### B — Patterns

The step opens with one line: how many patterns were found, and which were not.
That is normally all you need — the check that matters is on the **image**, not
in this panel. **Detection detail** opens the full table for anyone who wants
the measured values.

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

**Moving.** Drag the centre dot with the left button. Everything attached to
that ROI moves with it — the low-contrast background ring and object outline,
and the line-pattern profile line — so what you see is always what is measured.
Adjusted ROIs turn orange, and both the automatic and the manual position are
kept in the audit trail.

**Rotating.** Select an ROI and use the angle slider in the details panel at the
bottom, type an exact angle in the box beside it, or use the ±1° buttons and the
`[` and `]` keys. This matters when a phantom differs from the definition and
the automatic orientation is wrong.

**Undoing.** Every change to a measuring point can be undone — **↶ Undo** and
**↷ Redo** at the top of the step, and the count on the button says how many
steps are available. That includes moving the low-contrast block and changing
the low-contrast insert setting. The history is kept on the server with the
analysis, so it survives closing the analysis and coming back to it. A slip
never costs more than a click, and never costs a re-upload.

**Starting again.** Two resets, because "start again" is ambiguous once a
phantom has a stored layout:

| Button | Goes back to |
|---|---|
| **Reset to auto-detected** | This scan's own automatic detection, exactly as it was proposed — not a re-detection. The low-contrast insert setting goes back to what this scan's own contrast says |
| **Reset to stored layout** | The measuring points confirmed for this phantom previously, with its saved insert setting (only shown when there is one) |

A reset is itself an undoable step, so pressing one by mistake does not destroy
an afternoon's work.

For a line-pattern group, rotating the square also sets the profile direction by
hand, overriding the automatic one. Move the square first, then rotate only if
the automatic direction did not follow the pattern.

If a radiation-field edge was not detected automatically, press the button for
that side under **Manual field-edge placement** and left-click the visible field
edge on the image. The click only puts down a point, shown in blue as *new … edge*:

- **to move it**, click again somewhere else, drag it with the left button, or
  press on it once and nudge it with the arrow keys;
- **Apply edge** (or Enter) under the image sets the edge; nothing changes
  before that;
- **Cancel** (or Escape) leaves the field edges as they were.

Zooming and panning work as usual while you place it.

> **A phantom that differs from the definition.** Automatic placement is seeded
> from the stored phantom geometry, so on a different build — or a different
> X-ray unit — some patterns will be found in the wrong place. That is expected.
> Correct them here by dragging and rotating; every measurement is taken from
> where you put the ROI. You only have to do it once per phantom — see
> [Measuring points remembered per phantom](#measuring-points-remembered-per-phantom).

#### Measuring points remembered per phantom

Two phantoms built to the same drawing are not the same object: on the reference
scans the printed internal features sit several millimetres apart between two
builds, while repeat scans of one build reproduce to a few tenths of a
millimetre. That difference is assembly. It does not drift over time, so
correcting it on every scan is wasted work.

So step C can keep them. Just above **Measuring points confirmed ✓** it asks:

> ☑ *Use these measuring points for future scans of phantom MSF-01*

With the box ticked, pressing **Measuring points confirmed ✓** stores the
positions against the **Phantom** name on the analysis — together with which
way round its low-contrast insert is fitted (see below). The next scan you
upload with that same name starts from them instead of from automatic
detection, and the step says so at the top:

> *Measuring points: stored layout for phantom MSF-01 — saved 2026-08-14 09:12
> by S. Tkac · 31 measuring area(s)*

With the box unticked, the step is confirmed and this analysis is measured with
the points you see, but nothing is stored for later scans.

**Ticked or not to begin with.** The box starts **ticked** for the first
analysis of a phantom — there are no stored points yet — and when the stored
points came from this same analysis, so correcting a mark and confirming again
updates them. It starts **unticked** once another analysis has stored points
for the phantom: replacing somebody else's points changes where every later
scan starts, so it has to be your decision. The hint under the box says which
case you are in, and when the current points were stored and by whom.

**When it is not offered**, the step says why in the same place:

| You see | Why | What changes it |
|---|---|---|
| *These measuring points cannot be kept for future scans* | No phantom is named on the analysis | Add the phantom in the identity bar |
| *These measuring points will not be used for future scans of phantom …*, with the checks that failed | The image did not pass the quality check | An administrator can press **Use anyway (administrator)…** — see below |
| *These measuring points cannot be stored for future scans* | The analysis is finalised or signed off | Re-run the analysis |

**Use anyway (administrator)…** opens a panel listing the checks that failed.
It needs the **administrator password** and a **written reason**, and pressing
**Confirm the step and store them** confirms step C and stores the points in one
go. The reason and the failed checks are written to the audit log. On an
installation with no administrator password configured, nobody can do this.

Things worth knowing:

* **Name the phantom.** The name is the only thing that identifies it — the
  DICOM header of two different phantoms on the same X-ray unit is identical.
  Use the same spelling every time; `MSF-01` and `msf-01` are two phantoms as
  far as the software is concerned, and it will warn you when you create a
  layout for a name that differs from an existing one only in spelling.
* **The phantom may lie differently on the detector each time.** The positions
  are stored in the phantom's own frame, not in image pixels, so a scan with the
  phantom turned 90° or flipped replays them correctly.
* **A stored layout is never applied blindly.** It is laid on top of a fresh
  detection of this scan, and if the two disagree about where the phantom's
  patterns are — which is what a mis-registered scan looks like — the layout is
  refused and the step tells you why. Check step A in that case.
* **Field edges are never stored.** Collimation belongs to the exposure, not to
  the phantom.
* **A poor exposure stores nothing on its own.** A scan that failed the
  image-quality check cannot set the measuring points for future scans unless
  an administrator approves it, as described above.
* **It is not permanent.** **Reset to auto-detected** ignores it for this scan;
  confirming step C again with the box ticked replaces it; discarding the
  analysis that stored it brings back the one stored before (an analysis that
  left the box unticked stored nothing, so discarding it changes nothing); and
  it is deleted automatically when the last analysis carrying that phantom name
  is deleted, discarded or renamed.

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
block and all eight follow. In the block's own panel in step C:

| Action | How |
|---|---|
| **Move** | Drag the block rectangle itself (not a circle) |
| **Fine rotation** | Type the angle in the **fine angle** box — the image previews as you type, Enter or **Apply** commits, `[` and `]` nudge by 1°, Escape drops the preview |
| **Redefine completely** | **Click 4 block corners…**, then left-click the block's four corners on the image, in any order. The block does not move until you press **Apply corners** under the image |
| **Turn end for end** | **⟲ Turn 180°** — see [Turn 180° and the insert setting](#turn-180-and-the-insert-setting-are-different-things) |

The four-corner method works exactly like manual phantom corners in step A, with
the same controls under the image: drag a corner or nudge it with the arrow keys
to correct it, **Undo last point** (or Backspace), **Start over**, **Cancel** (or
Escape), and zoom and pan while you place them. Nothing is sent until **Apply
corners** (or Enter). It is the one to use when the block is both shifted and
rotated: the centre and the angle are both fitted from the four points. Once
applied, **↶ Undo** at the top of the step takes the placement back like any
other change.

Any of these marks the block and all eight circles as manually adjusted, and
discards the automatic grid refinement — that refinement was a correction to the
*old* placement and would otherwise pull the circles back off the objects. The
first commit after detection can therefore settle the circles up to about 2 mm
from the preview.

#### The low-contrast close-up

The discs are a fraction of a percent in contrast, and the window that suits the
whole phantom is far too wide to show them. Under the block's controls, **The
block, close up** shows the block on its own: straightened, with the block's
own brightness gradient removed, lightly smoothed, and windowed to the block
itself. The discs usually show at once, without touching W and C. The picture is
about 20 kB, so it arrives quickly even on a slow link.

- **contrast** — a slider from 20 % to 300 %. It works on the picture already
  in the browser, so hunting for the faintest disc costs nothing on the
  connection.
- **Coloured rings** mark where each disc is expected from the block's
  placement:

  | Ring | Meaning |
  |---|---|
  | green | clearly visible |
  | amber | faint |
  | red | at the limit of visibility |

  Each disc is judged against the strongest disc on the same exposure, so the
  colours describe this phantom, not the dose.
- **The summary line** under the picture counts them, e.g. *6 clearly visible ·
  1 faint · 1 at the limit of visibility*. On an exposure with no signal in the
  block at all it says *this exposure carries no signal in the block*.
- **Refresh** draws the close-up again. Press it after moving, turning or
  re-cornering the block, so the close-up shows the new placement.

The close-up is for placing and judging; the numbers in step E are always
measured on the original scan.

#### Which way round the low-contrast insert is fitted

The phantom exists in two builds whose low-contrast insert differs by a half
turn. Nothing in the picture shows it — every ring lands on a real disc either
way — but it changes which disc is which, and therefore whether the contrast
rises in the designed order.

Under the close-up, **Low-contrast insert: as drawn / turned half round** shows
the setting used for this analysis, and in brackets where it came from:

| Shown | Meaning |
|---|---|
| *(read from the contrast)* | Worked out from this scan: the discs' contrast rises in the designed order one way round and not the other |
| *(saved for this phantom)* | Taken from the phantom's saved measuring points. This scan's contrast is still checked against it |
| *(set by hand)* | Someone chose it on this analysis |
| *(could not be read from the contrast, so taken as drawn)* | The exposure was too weak, or too few discs could be measured, to decide |

**It is saved per phantom ID.** When you press **Measuring points confirmed ✓**
with **Use these measuring points for future scans** ticked, the insert setting
is stored with the measuring points — the message then ends *Its low-contrast
insert is kept as drawn* (or *turned half round*). With the box unticked it is
not stored either. Every later scan with the same Phantom name is read that
way, even an exposure far too weak to decide by itself. A scan that could not
decide never overwrites a setting saved from one that could.

**To change it**, click the other choice. Nothing moves: every ring stays on
its disc; only the design level each disc is read as changes. The change is
recorded in the audit trail and can be undone with **↶ Undo**. It applies to
this analysis straight away, and becomes the phantom's saved setting only when
you confirm step C with the box ticked. Analyses already measured are never
changed by it — re-run them if they need it.

**"Check that the phantom ID is right."** When the setting is saved for the
phantom (or set by hand) and this scan's contrast clearly says the opposite, the
setting is still used, and a warning appears under the close-up — and again in
step E and in the printed report:

> *The discs' contrast order looks like the other build of this phantom (insert
> turned the other way). Check that the phantom ID is right.*

The likeliest cause is a scan filed under the wrong phantom name — one build
typed in as the other. Check the **Phantom** in the identity bar first and
correct it with **Edit** if it is wrong. Only if the name is right and this
phantom really is fitted the other way, change the setting and confirm step C
with **Use these measuring points for future scans** ticked, so the corrected
setting is saved. Left as it is, step E will usually also say
that |CNR| is not in design order.

#### Turn 180° and the insert setting are different things

**⟲ Turn 180°** turns the block outline end for end, so every ring moves onto
the disc opposite — L1's ring to where L5's disc is, L2's to L6's, and so on. All
eight still sit on real discs, so nothing looks wrong on the image.

It no longer changes which disc is which. That is the insert setting's job, and
the insert setting takes a turned block into account, so the two can never
cancel each other out or correct the same thing twice. Step E names every disc
by the design level it actually carries whichever way the rings were turned.

So if step E reports that **|CNR| is not in design order**, look at the insert
setting under the close-up first — and at the phantom name — rather than at
Turn 180°. The fine angle box and the four corners are for everything else.

### D — Dimensions

Two lines at the top say whether the phantom measures the size it should and
whether the X-ray field is centred. If both are green, carry on. **Measured
dimensions** opens the corner measurements, the ruler pitches, the scale
cross-check and the per-side field deviations.

When no edge of the X-ray field is inside the image — which is how exposures are
normally taken, with the field wider than the phantom — the field line reads
*not checked — no field edge was found on any side*. That is not a fault; see
[The verdict words](#the-verdict-words).

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

The results open with the **overall verdict**, followed by a **summary table**:
one row per test, its verdict, and the single number that verdict rests on.
Below it, a line naming anything that needs attention.

When a test did not apply to this image, the verdict says so in a note beside
it, for example:

> **Overall: pass**
>
> pass — X-ray field alignment not checked (no field edge found in the image)

and the line below reads *Every test that applies to this image passed.*

Each test's full detail — every ROI, every measured value, the charts, and the
reason behind the verdict — is in a collapsible section beneath. **Anything that
did not pass is already open**, so you never have to go looking for the part
that matters. Nothing has been removed; it is one click away.

All tests are computed from the confirmed geometry:

- **Line patterns** — standard deviation per group, plus the intensity profile
  across each group with the fitted line grid, measured pitch against nominal
  frequency, and per-line residuals in micrometres.
- **Wedge** — mean per step S1…S7, monotonicity, dynamic-range ratio, saturation
  flags, and the linear-fit R² as a shape descriptor.
- **Low contrast** — CNR per circle L1…L8 in design order, read with the insert
  setting from step C, with a check that the contrast rises in that order.
- **Uniformity** — SNR per square and ΔSNR against the mean, tolerance ±20 %.
- **Field alignment** — deviation from each side's central long line, tolerance
  ±2 % of SID.

#### The verdict words

Each test carries one of these words:

| Word | Meaning | Effect on the overall verdict |
|---|---|---|
| **pass** | Within its limits | — |
| **warn** | Outside a soft limit, or something needs a look | Overall at least warn |
| **fail** | Outside its limit | Overall fail |
| **error** | The software could not run the test — a fault, or the time limit was reached | Counts like fail |
| **not applicable** | The test does not apply to this image. Only X-ray field alignment uses it, when no edge of the X-ray field is in the image | Left out. The verdict carries a note instead: *X-ray field alignment not checked (no field edge found in the image)* |
| **not measured** | The test applies, but nothing in the image could be measured — a saturated exposure, discs or squares with no signal, a flat wedge, measuring areas that could not be placed | Counts as **warn**, so an exposure nothing could be measured on never reads "pass" |

The overall verdict is always one of **pass**, **warn**, **fail** or
**error** — or **n/a** in the rare case where nothing at all could be judged.

**Older analyses still show "n/a".** Analyses measured before the two words
above were told apart carry **n/a** for both meanings — often as their overall
verdict even when every test passed, because the old rule let "n/a" outrank
"pass". They are deliberately not rewritten, so nothing already finalised or
signed changes under anyone's feet. To see an older analysis in the new words,
re-run it with **Recompute the numbers only** (see
[Running an analysis again](#running-an-analysis-again)).

The same words and the same note appear on the results page, in the History
table and in the printed report.

#### Why a test passed, warned or failed

Every test states its reasoning next to its result, so a bad result can be
acted on rather than merely noted. A green box gives the reason the test
passed; an amber box lists what went wrong, in the test's own terms, with the
measured value, the limit it was compared against, and where to go to correct
it; a grey box explains a test that did not apply. Line patterns additionally
give a per-group reason so it is clear *which* group is at fault.

Most reasons distinguish a genuine detector problem from a placement problem —
for example, a low-contrast ordering failure says the grid may not be on the
printed objects, or that the insert setting disagrees with the contrast, and
points back to step C; a non-monotonic wedge says a step ROI may be sitting on
a boundary. Read the reason before repeating the exposure.

#### "Not measured" is not the same as "passed"

A measurement needs something to measure. On an exposure that came out far too
bright, the phantom is one flat white slab: a uniformity square with no
variation in it has no signal-to-noise ratio, and a low-contrast disc with no
noise around it has no contrast-to-noise ratio. These are not values of zero,
they are absent values.

Such a test reports **not measured**, names each square or disc it could not
measure and says why, and never reports a pass. Earlier versions carried the
missing number along instead, which had two visible consequences: uniformity
could report **passed** on an image holding no signal at all, and opening the
printed report for such a scan answered *Internal Server Error*. Both are
fixed; a test that measured nothing now says so on the page and in the report.

If you see this, the exposure is the thing to correct — check the technique and
repeat it. Nothing about detector performance can be concluded from an image
where the measurement never happened.

#### When an analysis takes too long

An analysis never hangs. The server gives each measuring step a time limit —
two minutes unless the installation sets another — and when the limit is
reached it stops and stores a **failed result** instead of leaving the page on
"Computing…":

- every test it did not get to is marked **error**, with the reason *the
  analysis passed its time limit … so the … test was not measured*;
- the overall verdict is therefore **error** (or **fail**, if a test measured
  before the limit failed), so nobody can mistake it for a pass.

The scan stays stored. Re-run the analysis later (**Recompute the numbers
only**); if the limit is reached again on the same scan, report it with the
analysis id. Scans normally take seconds, so this is not expected to happen.

The page itself waits a minute longer than the server does. If even that passes
— usually a connection that has stopped answering — the step says *The server
did not answer within … s. The scan is stored — nothing needs re-uploading —
so you can try again, or open it later from History.*

If a step ever fails to draw, it is replaced by **This step could not be
shown**, with **Try again** and **Go to history**. Nothing has been lost; the
scan and everything measured so far are on the server.

#### When the image itself is the problem

Before anything is measured, the software asks a simpler question: **can this
exposure be measured at all?** It checks

| Check shown | What it looks at |
|---|---|
| Pixels pinned at the maximum | How much of the image sits on a single value |
| Squareness of the fitted phantom | Whether the fitted outline had to stretch — which means part of the phantom is off the detector |
| Central ruler lines found | How many of the four central ruler lines were located |
| Confidence the phantom was recognised | How well the chosen placement matches the phantom |
| Lead over the next-best placement | How clearly it beat the next-best placement |

If an exposure fails, step A opens with a red panel naming each check that
failed and what it measured, and the History row is badged **image**. The
analysis still runs — the numbers may be useful for showing what went wrong —
but two things are withheld unless an administrator approves them:

- it **cannot become the reference scan** for its phantom;
- it **cannot set the measuring points** (or the insert setting) that future
  scans of that phantom start from.

That second one is not hypothetical. During the first field test a phantom's
stored measuring points were overwritten three times inside twelve minutes from
exposures in which nothing could be measured, so every operator who analysed
that phantom afterwards began from marks taken off a blank image.

**When an administrator approves it anyway.** Sometimes the exposure that
failed is still the best there is — the only one possible that day, looked at
by someone who knows what they are looking at. Where step C would offer to keep
the measuring points, and in step F under **Reference scan**, the page then
lists the checks that failed and offers **Use anyway (administrator)…**. The
★ in History leads to the same panel. The panel asks for the **administrator
password** and a **written reason**; both the reason and the failed checks are
written to the audit log, and the **image** badge stays on the History row. On
an installation with no administrator password configured, nobody can approve
it.

The limits are set from the reference scans and sit far from anything a normal
exposure produces, so a good scan is not expected to trip them. If one does,
that is worth reporting: read the panel, which says exactly what was measured
and what the limit was.

Note on wording: the panel says what it observes ("most of this image sits on a
single value") rather than guessing whether the exposure was too high or too
low. The two look alike once the image has been rescaled, and the detector's own
exposure index is the reliable guide — it is shown in the identity bar, see
[Exposure index and deviation index](#exposure-index-and-deviation-index).

#### If two people open the same analysis

Everyone shares one login, so two operators can have the same analysis open at
once — and the software cannot tell them apart.

Their work no longer overwrites silently. If a measuring point is moved while
you have that analysis open, your next change is **refused rather than
applied**, the page reloads to show the current points, and a message stays on
screen explaining that your change did not take effect. Make it again on the
points you can now see.

This only protects measuring-point edits. Two people working on genuinely
different analyses never interfere.

#### If the window is closed or reloaded

The scan is on the server from the moment the upload finishes, so nothing is
lost and **the file never needs uploading again**.

When you come back, the upload page offers *"You have an unfinished analysis on
this computer"* with the file, phantom, operator and the step you had reached.
Press **Continue this analysis** to pick it up. Nothing is fetched or
recomputed until you press it, so a reload is always a safe way out of a page
that is misbehaving.

Below it, **Unfinished analyses** lists work that was started and never
measured — by anyone using this installation, since everyone shares one
account. That is deliberate: a scan uploaded on one machine can be continued on
another, which is how the analysis and the exposure often end up in different
rooms.

Two details worth knowing:

- The banner is remembered **by the browser**, not by the server. Two operators
  on two machines each see only their own. On a *shared* machine you may see a
  colleague's — which is why it names the file and the operator. **Not mine /
  hide** only removes the note from this browser; the analysis is untouched and
  still in History.
- An analysis interrupted while measuring reopens at **step D**, one press
  before the measurement, rather than starting it again by itself.

### F — Save and export

The analysis is stored with its full audit trail. The step shows its **analysis
id** ("Analysis … stored") — note it whenever you report a problem. From here
you can:

- set the **validation** state
- mark it as the **reference scan** for its phantom (see below)
- **Finalize** it
- **re-run** it
- open the **printable report**, or download **CSV** or **JSON**
- **verify the source file** against its recorded SHA-256

#### Finalising

**Finalize** declares the numbers finished. It needs results — an analysis with
nothing measured cannot be finalised — and pressing it twice keeps the time of
the first press. Marking an analysis as the reference, or recording a
validation ruling, finalises it as well.

Once finalised, the measurements are locked: see
[What finalising and signing off lock](#what-finalising-and-signing-off-lock).
To rework a finalised analysis, re-run it.

#### Running an analysis again

Any stored analysis with results can be measured again — **the scan never needs
uploading a second time**, and the record keeps its id, labels and dates, so a
re-run does not double-count in trends the way a fresh upload did. There are two
ways in, and they open the same panel:

- **Re-run analysis…** in step F of an open analysis;
- the **re-run…** link on the analysis's row in History, without opening it
  first. The link is only there on rows that have results. The panel names the
  analysis (its id, site / phantom and file) so you can check it is the right
  row.

Once the re-run has started, the analysis opens at the step the re-run reopened
(see the table below).

Choose how much to keep:

| Starting point | Keeps | Reopens at | Use when |
|---|---|---|---|
| **Recompute the numbers only** | registration and measuring points | step C | the software or the phantom definition changed, or to get an older analysis in the new verdict words |
| **Re-check the measuring points** | the registration | step C | a mark was on the wrong object |
| **Start again from registration** | nothing — the phantom is detected afresh | step A | the phantom outline was found wrongly; manual corners can be used afterwards |

Nothing is measured until you go through to step E again. For **Recompute the
numbers only**, confirm steps C and D without changing anything and the new
numbers appear in step E. Confirming step C stores the measuring points for the
phantom again only if **Use these measuring points for future scans** is ticked
— it starts ticked when the phantom's stored points came from this analysis,
and unticked when they came from another one.

Before an analysis is finalised a re-run needs nothing. Afterwards it rewrites
numbers somebody declared done, so the panel asks for the **administrator
password and a written reason** (at least five characters), both recorded in the
audit log. The panel warns you beforehand if the analysis was finalised, signed
off or is the reference scan. This is the same from History as from step F; on
an installation where no administrator password has been set up, the re-run of
such an analysis is refused with a message saying so.

Two things a re-run does deliberately:

- **It reopens the record.** The finalised stamp is cleared, and any validation
  ruling is withdrawn — a ruling was given for particular numbers, and those
  numbers are about to be replaced. The approver has to be asked again.
- **It keeps the reference flag.** Clearing it would leave the phantom with no
  reference at all while the re-run is in progress, and every other scan
  comparing against nothing.

**Undo re-run.** The previous state is kept. Until the re-run has produced new
results — a dropped connection, a change of mind — the identity bar says the
re-run is unfinished and offers **Undo re-run**, which puts back the old
results together with the ruling and the finalised stamp exactly as they were.
Once step E has produced new results the page no longer offers this; the
earlier states stay stored with the analysis (up to five, the original always
kept).

A re-run is refused if the stored scan no longer matches the fingerprint
recorded when it was uploaded — new numbers must not come from a file that is
not the original.

---

## Throwing away an unfinished analysis

For the everyday mistake — the wrong phantom typed in, a mis-set exposure, an
attempt abandoned half way — an analysis that nobody has committed to yet can be
thrown away **without a password**.

To throw away an unfinished analysis:

1. Press **Discard…** in the identity bar above the wizard (or the **delete**
   link on its row in History).
2. A panel shows the file name, when it was uploaded, and whether it was
   measured. If it is the only analysis of its phantom, it also says that the
   phantom's stored measuring points will go with it.
3. Press **Discard** to confirm, or **Cancel** (Escape or a click outside also
   cancel).

What it does:

- The analysis, **its scan file on the server** and its measuring-point history
  are removed. It disappears from History, trends, exports and the duplicate
  check. Analysing the scan again means uploading it again, so correct labels,
  corners or measuring points instead (**Edit**, **Manual corners**, step C)
  when that is what you actually want.
- **The phantom's stored measuring points are put right.** If this analysis was
  the one that stored them, the ones stored before it come back; if there were
  none before, or it was the phantom's last analysis, they are removed and the
  next scan of that phantom starts from automatic detection. The message after
  discarding says which happened.
- The removal is recorded in the audit log.

**Unfinished** means: not finalised, not signed off and not the reference scan.
Once any of those applies the **Discard…** button is gone, and removing the
analysis is a deletion that needs the administrator password — see
[Deleting an analysis](#deleting-an-analysis). If someone finalises the analysis
while your discard panel is open, the discard is refused and the message says
why.

---

## Correcting the identification

Once an analysis is open, an identity bar above every wizard step shows Site,
Phantom and Operator with an **Edit** button. Labels can be corrected at any
point — during the wizard, after the results are computed, or later from the
**label** link in the History table. Every change is recorded in the audit
trail, and the analysis appears immediately in the matching filters and trends.

The second line of the identity bar shows when the scan was acquired and the
detector's exposure values; the third its validation state.

---

## Exposure index and deviation index

Most detectors write into each scan how much radiation reached them. These
values are shown beside every analysis — **as the detector wrote them; the
software judges nothing from them**. They are there so that a scan whose
numbers look wrong can be checked at a glance for an exposure that was off.

| Value | Meaning |
|---|---|
| **Exposure index (EI)** | How much radiation reached the detector, by the detector's own measure. Higher means more |
| **Target exposure index** | The exposure index the detector was set up to expect for this kind of exposure |
| **Deviation index (DI)** | How far this exposure was from the target. **0** is on target; **+1** is about a quarter more radiation, **−1** about a fifth less; **+3** is about double, **−3** about half. Minus means under-exposed, plus over-exposed |
| **Sensitivity (S value)** | The Fuji's own number. On a Fuji a higher S value means less radiation reached the plate |

Where they appear:

| Place | What |
|---|---|
| Identity bar | *Acquired … · Exposure index 263 (target 250) · Deviation index +0.2* — hover for the explanation |
| History | The **exposure** column: *EI 263 · DI +0.2* |
| Printed report | All four values in the Identification box |
| CSV exports | Four columns at the end of the long export (`exposure_index`, `target_exposure_index`, `deviation_index`, `sensitivity`); four rows at the bottom of the wide export |

The deviation index is shown to one decimal with its sign, because the sign is
the point. A value the detector did not write reads **not recorded** — never
zero. The Philips unit writes only the exposure index, so its target and
deviation index read "not recorded".

Analyses stored before these values were shown have them filled in
automatically from their stored scan files; nothing needs to be done.

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

**A signed-off analysis is locked.** Recording a ruling also finalises the
analysis, so the measuring points, the registration and the results cannot be
changed — step C shows a lock notice naming who signed it, and every change is
refused. Validation means a person took responsibility for a specific set of
numbers, so those numbers cannot quietly become different ones.

**To rework a signed-off analysis, re-run it** (administrator password and
reason). The re-run withdraws the ruling itself, because it was given for the
numbers about to be replaced. Withdrawing the ruling on its own returns the
analysis to *pending review* but does not unlock it: the analysis stays
finalised. (The lock notice in step C still suggests withdrawing the validation
first; re-running is the way that works.)

Set it from the identity bar (**Set…**), from step F, or from the **validate**
link in the History table. A ruling can be changed or withdrawn later; every
ruling and reversal is written to the audit log with the previous state.

The decision appears at the top of the printable report, in the comparison
report, as a column and filter in History, and in both CSV exports.

---

## What finalising and signing off lock

| Action | Unfinished | Finalised, signed off, or reference scan |
|---|---|---|
| Correct site, phantom, operator, notes | yes | yes |
| Move measuring points, the block or field edges; change the insert setting; undo, redo, reset | yes | no — refused with a message pointing to re-run |
| Manual corners, re-detect the patterns, confirm step C (and store the phantom's measuring points), measure again in step E | yes | no |
| Re-run | yes, free | administrator password and reason; reopens the record |
| Discard (no password) | yes | no |
| Delete | — (use Discard) | administrator password and reason |
| Mark or remove as the reference scan | yes, once it has results (marking an image that failed the quality check needs the administrator password and a reason) | yes (the same) |
| Record or withdraw a validation ruling | administrator password | administrator password |

An analysis with results opens at step F, and step F has no way back to the
earlier steps: **Re-run analysis…** (or **re-run…** on its row in History) is
the way into them. If you do reach step C
of a finalised analysis some other way — a colleague finalised it while you had
it open, say — it looks the same as before (unless it is signed off), but every
change is refused with a message saying it was finalised and to use **Re-run
analysis** instead.

The **Discard…** button disappears once the analysis is protected, the next
time the analysis is opened.

---

## History and trends

The History tab works from either a **filter** or a **manual selection**:

- filter by Site, Phantom, Protocol or Validation state; the counts beside each
  option show how many analyses match
- or tick individual rows — as soon as anything is ticked, the ticked rows take
  precedence over the filter, and a note states which is in effect

Each row shows the two dates, site, phantom, source file (⚠ for a
reduced-precision upload, an **image** badge for an exposure that failed the
image-quality check), the **exposure** values, the protocol, the step reached,
the **status** — with the verdict's note underneath when a test did not apply,
e.g. *X-ray field alignment not checked (no field edge found in the image)* —
the validation, the reference star, and the links **open · re-run… · report ·
label · validate · verify · delete**. **re-run…** is only shown on analyses that
have results — see [Running an analysis again](#running-an-analysis-again).

### Two dates, kept apart

The table carries both:

| Column | Where it comes from |
|---|---|
| **acquired** | The scanner's own clock, from the DICOM header |
| **uploaded** | This server's clock, when the file arrived |

They are not interchangeable. A detector's clock can be unset, reset during
service, or simply wrong, and nothing in DICOM records a timezone. When the
header carries no usable date the acquired cell reads **unknown ⚠**; when it
carries one that cannot be right — a year before 2000, or a time after the
upload — it is shown with a ⚠ as well, and the count of such rows appears beside
the filter. The acquisition column is never quietly filled in from the upload
clock.

**Order by** switches the table between the two. Acquisition order is the
default and is what you want normally, because re-analysing an old scan should
not disturb the sequence. Upload order is what you want when a unit's clock is
suspect: on a large dataset it is the only ordering that is certainly real.

The trend chart has the same choice in **Date axis**, and marks points with no
trustworthy acquisition date in amber so a run of them cannot be mistaken for a
real chronology.

Three outputs follow the current selection:

| Output | Contents |
|---|---|
| **Comparison report** | A visual comparison of the whole selection — see below |
| **Trend chart** | One metric over one phantom's scans — see below |
| **CSV export** | *Long*: one row per metric per analysis, for pivot tables. *Wide*: one row per metric, one column per analysis, for reading drift directly. Both carry site, phantom, operator, acquisition time, upload time, whether the acquisition time is trustworthy, and the detector's exposure values |

### The trend chart

Collapsed by default — open **Trend of a single metric** when you want it.

The chart draws nothing until you have said which scans belong together:
pick a **Phantom** in the filter, or tick rows in the table. That is not a
limitation but the point — two phantoms differ by design, so a line drawn
across them would show their assembly differences as if a detector were
drifting.

Once a phantom is selected: pick the metric, hover any point for its exact
value, date and scan, and switch the **Date axis** to the upload date when the
scanner's clock is suspect. The reference scan is the highlighted point, the
dashed band is ±20 % of it, and amber points carry no trustworthy acquisition
date. With a long series (a hundred scans and more) the date labels thin out
automatically so they stay readable — the hover readout always has the exact
date.

### The comparison report

Built mainly from plots, and equally suited to one phantom over time or to many
phantoms side by side. Only analyses with results are included. Every visual
identifies entries by **Site / Phantom**, with the date appended only when the
same phantom appears more than once. A key at the top maps each label to its
full record.

- **Status grid** — every test against every analysis, including a validation
  row
- **The scans side by side** — a table of pictures, see below
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

#### The scans side by side

One column per scan, one row per test area:

| Row | Shows |
|---|---|
| Whole phantom | The whole face, for orientation and gross problems |
| Line-pair strip | The strip as printed; enlarge it to see the lines |
| Wedge | The step column laid on its side — S1, the top of the phantom, at the left |
| Low-contrast block | The same close-up as in step C |
| Uniformity squares | The five squares side by side (TL, TR, C, BL, BR) in one window |

Every picture is **straightened to the phantom's own frame**, so scans taken at
0°, 90° or 180°, or face down, line up column by column. Each column is headed
by the site / phantom, the acquisition date and the overall result, with a tag
on the reference scan. Under each picture are that test's result and its key
numbers — taken from the stored results, not measured from the picture.

**One window per detector and protocol.** In every row except the low-contrast
one, the pictures of scans from the same detector and protocol are shown with
one shared window, taken from the reference scan when the selection contains it,
otherwise from the first scan — so a brighter picture really is brighter. The
column the window comes from is tagged *shared window taken from here*. A scan
that is the only one from its detector and protocol keeps its own window and
says so, because on another detector's window it would come out black or white
and look like a fault that is not there. The note at the left of each row says
which window is in use.

- **To window each picture on its own**, tick *window each picture on its own*
  at the left of the row — useful when one scan is much darker than the rest.
  Untick it to go back to the shared window. Each row has its own switch.
- **The low-contrast row** is always windowed to each picture itself, as in
  step C: compare which discs you can see, not how bright the pictures are.
- **To enlarge a picture**, click it. Click anywhere or press Escape to close.
  The enlargement uses whichever window the row is showing.
- A scan with no picture gets a **labelled empty cell** saying why — for
  example *could not be registered — no picture* — never a gap, so the columns
  cannot shift and be compared wrongly.

The pictures are **part of the page**: saving the comparison from the browser
(*Save page as…*) keeps every picture, and the saved copy works without the
server, windows and enlarging included. Nothing is e-mailed from the
application; a saved copy is how a comparison travels. When printed, the table
gets a landscape page of its own.

The pictures cost about 60–70 kB per scan — a ten-scan comparison about
0.7 MB. The first time a scan appears in a comparison the server needs a few
seconds to draw its pictures; after that they are kept, and are only redrawn
when that scan's measuring points or registration change.

---

## Comparing like with like

Pixel values in processed radiographs are not proportional to dose, so all tests
are **constancy tests**: each scan is compared against a reference.

### The reference scan

A reference belongs to **one phantom on one protocol**.

The phantom half matters because two phantoms built to the same drawing are not
the same object — on the reference set here, two builds have internal features
several millimetres apart, and both are perfectly valid. A site running two
phantoms on one machine needs a reference for each. Marking one phantom's
reference therefore never disturbs another's.

The protocol half stays because comparing across kV, detector or processing is
meaningless whatever the phantom. Each analysis records a **protocol
signature** — detector model, kV, pixel spacing and processing family — and the
comparison report prints it so a mixed series is visible.

Set or remove a reference from step F, or with the ★ in the History table:

| Symbol | Meaning |
|---|---|
| ★ | This is the reference for its phantom on its protocol. Click to remove it |
| ☆ | Click to make this the reference; whatever held the role for that phantom stands down |

Marking a reference also finalises that analysis. A reference can always be
removed, leaving that phantom with none until another is chosen — a reference
picked from a scan that later turns out to be poor must not be permanent.
Reduced-precision analyses and analyses without results cannot be references.
An exposure that failed the image-quality check can become one only with an
administrator's approval: **Use anyway (administrator)…**, the administrator
password and a written reason — see
[When the image itself is the problem](#when-the-image-itself-is-the-problem).

Because each phantom has its own, a trend chart covering several phantoms
contains several references, and the ±20 % band is then not drawn: it would be a
band around an arbitrary one. Filter to a single phantom to see it.

---

## Source file integrity

Every analysis records a SHA-256 fingerprint of the file it was computed from
and keeps a copy of that file. See
[MAINTENANCE.md](MAINTENANCE.md#source-file-integrity) for what it detects and
how to check it.

---

## Deleting an analysis

An unfinished analysis is thrown away with **Discard** — see
[Throwing away an unfinished analysis](#throwing-away-an-unfinished-analysis);
the **delete** link in History does exactly that for such an analysis.

For an analysis that has been finalised, signed off or made the reference scan,
the **delete** link opens a panel that shows exactly what is about to be
destroyed — the id, the site and phantom, both dates, the source file name and
the result — and asks for two things:

1. a **reason**, at least five characters, recorded in the audit log next to who
   did it and from where. It is the only record of why the data went;
2. the **administrator password** — not your everyday login.

Deleting removes the record, the stored source file, the measuring-point edit
history and the kept earlier states. The phantom's stored measuring points are
put right exactly as for a discard: restored to the previous ones if this
analysis had stored them, removed if it was the last analysis of that phantom —
and the panel warns you before you confirm when that is about to happen.

The panel stays open until the server accepts. A wrong password or a missing
reason is shown in the panel itself, so a deletion can never appear to have
happened when it did not. On an installation with no administrator password
configured, finished analyses cannot be deleted at all, and the page says how
an administrator can enable it.

> Typing the analysis id back is no longer required. It protected nothing that a
> copy-paste did not satisfy, while its refusal was a message that cleared
> itself after a few seconds — which is how a failed deletion could be mistaken
> for a successful one.

---

## Working over a slow connection

The application is used over links of about 512 kbit/s. What costs time, and
what does not:

| Action | Traffic |
|---|---|
| Uploading a scan | The whole file — a 7.5 MB scan takes about two minutes at 512 kbit/s. The progress bar shows how far it has got |
| Checking whether a file is already here | A few hundred bytes, before anything is sent |
| Opening an analysis | A picture of about 0.1 MB. The browser keeps it for a day, so opening the analysis again costs nothing |
| Moving the W / C sliders | Nothing while you move them; one sharper picture after you stop |
| The low-contrast close-up | About 20 kB; its contrast slider costs nothing |
| The printed report | About 0.4 MB |
| The comparison report | About 60–70 kB of pictures per scan, plus its charts |
| Re-running an analysis | No upload — the scan is already on the server |

The two things that used to cost the most on a field link — sending a file only
to be told it was already there, and deleting and re-uploading a scan to correct
a mistake — are now a few hundred bytes and a **Discard** or **Re-run**
respectively.

---

## Reduced-precision mode

A JPG or PNG upload is accepted but marked *reduced precision*: 8-bit lossy
data, no acquisition metadata, and scale derived from phantom geometry alone.
Such analyses are excluded from baselines by default and are best used for a
quick look rather than for trending.
