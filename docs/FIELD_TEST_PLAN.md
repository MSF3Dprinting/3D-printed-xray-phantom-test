# Development plan — 2026-09-21, approved and implemented

Everything built after the first field test. Each step says where it comes
from — one of your requests, or my own review of the scans — what you will see,
which numbers move, and what it costs on a slow connection. The original plan
of 2026-09-20 and its findings follow below it, kept as the record of how we
got here.

## Status — 2026-09-22

**Steps 1 to 7 are implemented.** Approved on 2026-09-21 with parallel agents
allowed: five steps were built in parallel in separate copies, merged, then
read by two independent reviewers and checked against the plan by a third
agent, which found four plan items marked done that were incomplete. All seven
review findings and all four gaps were fixed. Full suite: **1314 passed, none
skipped**, in the project folder. Reference benchmark: the only change is 33 ×
field alignment "n/a" → "not applicable" and 6 × overall "n/a" → "pass" on the
original scans; no measured number moved.

| Step | Result |
|---|---|
| 1 · "n/a" split | Done. The six original scans now read "pass". "Not measured" counts as a warning, so a broken exposure can never read "pass". Stored records keep their old wording (your decision: new and re-run analyses only). |
| 2 · Insert orientation per phantom ID | Done. Saved with the measuring points, replayed on later scans, "check the phantom ID" warning on disagreement, a switch in the marking step. |
| 3 · Comparison with pictures | Done. 42–54 kB per scan; one shared window per detector/protocol group (a single window across detectors turned the other detector's pictures black); pictures embedded in the page; capped at 12 columns — the reference plus the most recent — with a note. |
| 4a · Lighter reports | Done. 1.72 MB → 0.39 MB on the original scans. |
| 4b · Lighter viewer picture | Done. 0.9 MB PNG → 93–113 kB JPEG. |
| 4c · Compressing uploads in the browser | **Not started — waiting for your decision.** |
| 5 · Exposure index and date | Done. Identity bar, report, History and CSV; older records filled in from their stored files at start-up. |
| 6 · User guide and design notes | Done. |
| 7 · Second field-test checklist | Done: docs/FIELD_TEST_CHECKLIST.md. |

**Plan items found incomplete and now finished:** the explicit "use these
measuring points for future scans" choice (every confirm was still overwriting
the stored layout); correctable picking for the low-contrast block corners and
field edges; the administrator override for the quality gate; re-run from
History.

**Review findings fixed:** workers starting together after an upgrade could
crash on "duplicate column"; the exposure backfill never retried if interrupted;
comparison size unbounded and charts full-colour; a status fallback missing in
the picture table; report wording for an undecided orientation with turned
rings; the signed-off lock advice ("withdraw first") no longer unlocked anything
— it now points to Re-run; finalised records looked editable in the marking
step; the close-up did not follow the block; the results table named discs
differently from the chart and report; and, found during the merge, the
close-up picture was cached under the edit counter and could show an old
picture after undo.

**Known, not changed (candidates for a later round):** "Recompute the numbers
only" and "Re-check the measuring points" both reopen at step C; earlier
revisions are not restorable from the page once a re-run has produced results;
a second re-run can be started while the first is still on its way; the
"Use anyway (administrator)…" button shows even where no administrator password
is configured (it explains on click); records analysed before this round keep
their old quality wording; the comparison report does not show exposure values;
a comparison that includes scans never drawn before decodes each one (about
2 s per scan) the first time. The page code has no JavaScript runtime in the
tests — **a manual pass in a browser is advised before the field test**.

## Done since the field test

| Your request | What was done |
|---|---|
| 1 · Delete before finalising | Discard with confirmation only; gone from History, trends and exports; the phantom's previous measuring points come back if the discarded scan had replaced them. |
| 2 · Re-run any analysis | "Re-run analysis" on the same record, three starting points; free before finalising, admin password + reason after; previous results kept and restorable. |
| 3 · Mouse and mis-clicks | Left places, middle drags the view; corners no longer submit on the 4th click — drag, undo, then "Apply corners". |
| 4 · Discs hard to see | Close-up of the disc block (straightened, flattened, windowed to itself, 20 kB) with its own contrast slider and a visibility ring per disc; the dark print's insert orientation read from the contrast (32/33 reference scans, 1 honestly undecided); disc-order check replaced (8 false warnings gone, no measured number changed). |
| 5 · Scans flagged as the same | Checked before uploading; the dialog shows both files; "Continue this analysis" is the main action; re-exported scans noted. |
| 6 · Stuck on "Computing…" | Time limit with a failed result instead of a hang; report never 500; a scan with no signal can no longer pass; bad exposures flagged at upload. |
| Slow connection | Compression, window/level in the browser, cached image, duplicate check before sending, upload progress with time left and Cancel. |
| MONOCHROME1 (approved 2026-09-21) | Flipped about the detector's range instead of the brightest pixel. The three readable field scans come out identical to the last pixel; only the two completely faulty ones move (by 66 and 95). Nothing needs re-running. |

## Steps, in proposed order

### Step 1 — "n/a" stops hiding "pass" *(from my review; approved in the original plan)*
- **Today:** the overall result is the worst of the test results, and "n/a" counts as worse than "pass". X-ray field alignment is "n/a" on every scan so far, because no field edge is in the image — so **all six original scans show "n/a" although every test passes.** Worse, the same word is used for two different things: *does not apply* (field alignment) and *could not be measured* (corners not found, discs unreadable, a flat wedge, patterns that could not be placed).
- **Change:** two words instead of one. **"not applicable"** — only field alignment with no field edge in the image; it does not lower the overall result, which reads e.g. "pass — field alignment not checked". **"not measured"** — everything that should have been measured and was not; it counts like a warning, so a bad exposure can never read "pass". Simply ranking "n/a" below "pass" would have let exactly that happen.
- **You will see:** the new wording on the History badge, results page, report and CSV. The six original scans become "pass". The blue-print scans stay fail/warn because of the parked line-pair problem below.
- **Numbers:** none change — only the overall verdict and its wording. Benchmark: 6 overall verdicts n/a → pass.
- **Connection:** none. **Size:** small.

### Step 2 — Disc orientation saved per phantom ID *(your decision, 2026-09-21)*
- The close-up in the marking step says how the insert is fitted ("as drawn" / "turned half round — read from the contrast").
- Saved with the phantom ID when you choose "use these measuring points for future scans" — the same explicit save as the measuring points today, and never from a scan that fails the quality check.
- Later scans of that phantom use the saved value instead of deciding again, so even a very weak exposure is read the right way round. If a scan's contrast clearly says the opposite, the saved value is still used and the scan is flagged: "this looks like the other print — check the phantom ID".
- A switch in the marking step changes it; every change is logged; past analyses are not rewritten unless re-run.
- **Numbers:** none on the reference scans. **Connection:** about 100 bytes more on a request the close-up already makes.

### Step 3 — Comparing scans with pictures *(your item 7)*
- A table in the comparison report: one column per scan; rows for the whole phantom, line pairs, wedge, low contrast (the same flattened close-up as in the marking step) and uniformity.
- Every picture straightened to the phantom's frame, so scans taken at 0°, 90° or 180° line up. One window for all scans, taken from the first or baseline scan, so a brighter picture really is brighter; a switch per row windows each picture on its own. Key numbers under each picture; click to enlarge. A scan that could not be registered gets a labelled empty cell, not a gap.
- **Pictures are embedded in the report**, so a saved copy contains them (your decision; nothing is e-mailed from the app).
- **Connection:** about 60–70 kB per scan (the prototype measured 52 kB for all five regions) — a 10-scan comparison about 0.7 MB, roughly 10 s at 512 kbit/s. Each scan's pictures are made once on the server and kept, so reopening a comparison costs no rendering.
- The line-pair pictures simply show the strip as it is; they do not depend on the parked question.

### Step 4 — The rest of the slow-connection work *(your low-speed instruction)*
- **4a · Reports:** one report is 1.7 MB, 1.33 MB of it a single embedded overlay picture → about 0.4 MB. No number changes.
- **4b · Viewer picture:** the background image when an analysis opens, 0.9 MB PNG → about 0.1 MB JPEG. Measurements always run on the original scan on the server; the picture is only for looking, and disc placement uses the separate lossless close-up.
- **4c · Optional, last:** compress the scan in the browser before uploading — 7.5 → 2.9 MB measured, about 2 minutes → 45 s at 512 kbit/s. It touches the upload path, so only if you want it once 4a and 4b are in.

### Step 5 — Exposure index and acquisition date shown *(from my review; helps with your item 6)*
- All three detectors write the standard exposure index; the Carestream and Fuji also write the target exposure index and the deviation index; all write the acquisition date and time. None of it is shown today.
- Shown beside each analysis (identity bar, report, History, CSV), so an underexposed scan carries e.g. "deviation index −4.8" next to its result. Shown only — nothing is judged from it yet. "Not recorded" where a detector does not write it.

### Step 6 — User guide and design notes
Everything above written up for the operators and for maintenance.

### Step 7 — Ready for the second field test
Full suite, benchmark, and a one-page checklist of what to try in the field.

## Parked — on the to-do list, nothing changes until decided

**Line pairs on the blue prints.** On all 27 blue-print scans the line-pair test fails or warns, and so does the overall result. Established from the pixels (bar frequency at each designed position, independent of the software's detection): on the blue prints the finest bars sit at the design's G1.1 end and the notched frame line on the opposite side; on the original phantom (`MSF^PHANTOM001`, folders 20260727 and 20260730, 6 of 6) and the field Fuji phantom (3 of 3 readable) the strip is in design order. Every registration is correct (4/4 corner markers, not mirrored, low-contrast block where designed). The G1.1/G1.2 swap in the results is the software's error: when the order does not fit, it falls back to a coarse frequency reading that cannot tell 1.1 from 1.2 lp/mm (1.07 read as 1.13, 1.23 as 1.27) and gives G1.2 the wrong block by 0.004. **Waiting for:** a look at a physical blue phantom next to `MSF^PHANTOM001`, same way up — are the finest lines at the same end? Until then, no line-pair code changes. Also parked: group 1.6 fails on 8 blue scans — not investigated.

## Dropped

- **Widening the disc search (±2.5 mm):** measured to make the dark print worse — faint discs drift to the edge of any larger search area. Placement was already on all 8 discs on all 33 reference scans. Agreed 2026-09-21.

## Decisions needed

1. **Approve this plan and its order.**
2. **Step 1, existing records:** recompute the overall verdict on analyses already stored, or apply the new wording only to new and re-run analyses? *Recommended: new and re-run only* — nothing already finalised or signed changes wording under anyone's feet.
3. **Step 4c** (compressing uploads in the browser): decide when steps 4a and 4b are done.

---

# Original plan after the first field test (2026-09-20) — approved

Status at the time: **approved; WP0 (the safety net) is complete, nothing
else is implemented.** The five WP0 items are struck through below; the audit
log has since been read and its findings are folded into sections 1 and 3.
Scope: the seven feedback items, the low-bandwidth requirement, and what the
review of the HQ and field scans turned up. Constraints kept throughout: no
change of architecture (FastAPI + static vanilla JS + SQLite, per-worker
caches), no change of deployment (gunicorn behind the reverse proxy on a
sub-path). Field-testing scans are used only to test behaviour — never for the
phantom definition, a stored layout, a baseline, or a threshold.

How this was produced: code reading, reproduction through the real HTTP
endpoints in throw-away data roots, and a benchmark of the current pipeline on
all 33 valid HQ scans (6 Philips + 27 Carestream; the 12 Leeds PIX-13 images
ignored as instructed) and the 5 field scans. Each claim below is marked
**[verified]** (reproduced or measured) or **[read]** (from code, not run).

---

## 1. What is actually going on

| # | Feedback | Root cause |
|---|---|---|
| 6 | Stuck at "Computing…", report = Internal Server Error | **The server never hangs** — on the saturated scans every endpoint answers 200 and compute takes 0.1 s **[verified]**. The result contains `cnr: null` for all 8 low-contrast discs; `app.js:2047` calls `row.cnr.toFixed(3)` on it and throws. That line is *outside* the `try/catch` (1982-1988), so the "Computing…" text is never replaced **[verified]**. The report dies the same way: `report.py:102-105` hands `None` to matplotlib → HTTP 500 **[verified]**. |
| 6b | (not reported) | On the fully saturated scan **uniformity reports PASS** ("worst 0.0 %"): every SNR is NaN and `max(0.0, nan)` stays 0.0 (`uniformity.py:87-93`) **[verified]**. Low contrast only says "warn". A scan with no signal must not pass anything. |
| 5 | Different scans flagged as the same | **No false positive exists.** Identity is the SHA-256 of the file bytes; the five field files are all different and the server accepts all five **[verified]**. The warning was a *true* duplicate of a record the operators could not see: after the "stuck" screen they reloaded, the page forgets the open analysis (`app.js:2992`), they uploaded the same file again → 409. The dialog then says "already analysed" about a draft, shows no file name or thumbnail, and two of the scans share name (`003_0000.dcm`), byte size and PatientName — so it *looks* wrong. **Confirmed against the field audit log**: all 8 refusals in 7 weeks were byte-identical re-uploads, and three file *names* carried genuinely different files that were each accepted without a murmur. The stuck chain is timestamped: 04:22:47 the server recorded `compute=fail`, the client went silent for 2.5 h, at 06:57:13 the same file was re-uploaded and refused **[verified]**. |
| 1 | Easy delete before finalizing | Delete has one heavy policy (admin password + reason; refused entirely when no admin password is configured) applied to everything, even an empty mistaken upload **[verified]**. And **"finalized" does not exist in the data**: Finalize changes no column, only adds an audit line **[verified]** — so there is nothing a lighter rule could key on. |
| 2 | Re-run any analysis | The server can already re-register/propose/compute from the stored file (no re-upload). The UI cannot reach it: a completed record opens on step F, which has no Back button, and the step bar is not clickable **[read]**. Today's only route is re-uploading 7.5 MB and "Analyse again anyway", which double-counts in trends **[verified]**. Every rework destroys the previous results with no copy kept. |
| 3 | Corners: left click only, middle = pan | `app.js:405` places a point on *any* mouse button; `app.js:360` switches panning off completely while picking; the 4th click submits immediately — no correction, no undo **[read, unambiguous]**. Same for the low-contrast block corners and field-edge picking. |
| 4 | Low-contrast points hard to see / W/C hard | Two separate things. (a) With the global window the discs are practically invisible; with a 1 mm smoothing + background flattening + window local to the block, 6–8 of 8 become obvious **[verified on field 004 and HQ]**. (b) **Every W/L slider pause re-downloads the whole image** — 0.9–1.1 MB, never cached (`app.js:181-188`, `main.py:204`) — i.e. ~15 s per tweak at 512 kbit/s **[measured]**. The automatic marks themselves land on the discs on all HQ scans. |
| 7 | Table with images in the comparison | Does not exist; today's comparison is 17 embedded charts (~0.6 MB per scan). A prototype of rectified thumbnails works: all scans shown in the same orientation regardless of 0/90/180° acquisition, all 5 test regions ≈ 52 kB per scan as JPEG **[prototyped]**. |

### Findings nobody asked about, from reviewing the scans

- **No scan can ever be "pass" overall.** X-ray field alignment is "n/a" on all 38 images, and the overall rule ranks "n/a" above "pass" (`pipeline.py:125-134`). All six Philips reference scans pass every test and still show **n/a** **[verified]**.
- **Line pairs fail or warn on all 27 Carestream HQ scans**, pass on Philips and Fuji **[verified]**. The ROIs sit correctly on the groups and the pitch is measured correctly, but in the blue-print phantoms the **line-pair strip is mounted end-for-end** (2.0 lp/mm group found 122 mm from its nominal place). The fallback matching then swaps the labels of the two coarsest groups (measured 0.932 / 0.816 mm against nominal 0.833 / 0.909 → "+11.9 % / −10.2 %"). Some orientations/doses also detect only 3 of 5 groups.
- **The dark-blue print's low-contrast insert is rotated 180°** — confirms your remark. Read rotated, its contrasts follow the same rising pattern as the light-blue print. The software cannot tell (its angle estimate is ambiguous by 180°) and its order check is lenient: it warns on 8 of 15 dark-blue scans and silently accepts 7. That invites inconsistent manual "Turn 180°" between operators.
- **Clipped scan 001 is accepted without warning** although the registration is stretched 5 % in one direction and every right-hand ROI is off its target.
- Fuji images are MONOCHROME1 and are inverted with each image's *own* maximum (`ingest.py:103`). ~~→ a scan-dependent offset in every mean value~~ **Corrected 2026-09-21:** on all three readable field scans the image maximum *is* the detector maximum (1023, on 77,000–100,000 pixels of unblocked beam), so the old rule was exact there; only the two completely faulty scans differed (by 66 and 95). Fixed anyway, as a guard for future exposures with no unblocked beam.
- Fuji CR headers carry no kV/mAs, so every CR scan gets the same protocol signature; ExposureIndex / Sensitivity / DeviationIndex are present but not extracted.
- ~~Running the test suite creates `data/phantom_qa.sqlite3` + `data/uploads/` in the working tree~~ — it did, and the log directory too; on a server that would have opened the live database. **Fixed in WP0.2.** Of 708 tests, **35 were skipping** because `tests/conftest.py` still pointed at the pre-move sample folder; all now run.


### What the field audit log added (7 weeks, 42 uploads, 25 distinct files)

- **Faulty exposures overwrote the shared phantom layout.** A layout is saved on essentially every Stage C confirm, and on field-test day the layout for `KTP-001` was rewritten **six times in twelve minutes** — including once from the clipped exposure and twice from the saturated ones. Anyone analysing in that window inherited measuring points derived from an unmeasurable image. This makes the quality gate (WP1.4) a data-integrity fix, not a convenience, and it adds decision **D15**.
- **Delete is already the workaround for the missing discard and re-run.** Recorded reasons include "Reverse pattern needs to be correcte in analysis", "Mark alighment correction", "Failed manual point detection", "Artifact in software - bug". On 2026-08-27 the same file was deleted and re-uploaded four times in 70 minutes, ~8 MB each time. WP2 removes that cycle.
- **Two people edit one analysis under the shared login**, from different addresses minutes apart, on at least three records. Password-free discard (WP2.2) must therefore show who touched the record and when, before it is thrown away.
- A baseline was set from an analysis whose overall verdict was `n/a` — a direct consequence of "n/a outranks pass" (WP6.2).
- Smaller: Finalize was double-clicked on three records (no busy guard); one record was recomputed six times in six minutes (Stage E recomputes on every render); renaming a phantom deleted a stored layout; three failed logins for a non-existent user `msfuser`.
- The server holds field exposures that are not in the local `Field testing/` folder (`11_0000.dcm`, `655_0000.dcm`, `6557_0000.dcm`), so the local set is a subset of what the field produced.

---

## 2. Work packages, in proposed order

### WP0 — Safety net first (small) — DONE

1. ~~`tests/conftest.py`: sample folder from `PHANTOMQA_SAMPLE_DIR`~~ — done. Loud red end-of-run summary when sample tests skip; `PHANTOMQA_REQUIRE_SAMPLES=1` turns a skip into a hard error for release runs.
2. ~~Data root overridable by env var~~ — done: `PHANTOMQA_DATA_ROOT` (unset = today's behaviour, so deployment is unchanged), used by `main.py`, `manage.py` and the test helper. The log directory was the same trap and is covered too. Two guard tests.
3. ~~**HQ regression benchmark**~~ — done: `tests/hq_manifest.py`, `tests/hq_benchmark.json`, `tests/test_hq_benchmark.py`. 33 scans, 25 ROI positions each. Six representative scans run in the ordinary suite (~45 s); `PHANTOMQA_HQ_FULL=1` runs all 33 (~90 s). Ten self-tests prove the comparison detects change. **Deviation from this plan: the manifest is committed**, so a regression shows up as a reviewable diff instead of being regenerated per machine. It holds derived numbers only, and a test asserts no identifying tags leak into it.
4. ~~Field scans as *negative* fixtures~~ — done: `tests/field_scans.py` and `tests/test_unusable_exposures.py`, with synthetic saturated / clipped / flat / no-phantom images so the checks also run where no scans exist. Guards: application code may not name the field drop, no other test may reach for it, and no field exposure's hash may appear in the reference benchmark. Two defects are recorded as `xfail(strict=True)` and will announce themselves when WP1 fixes them.
5. Baseline established: **760 tests pass, 0 skipped** with sample scans, reference scans and the full benchmark all enabled, and the suite no longer writes anything into the checkout.

### WP1 — Never stuck, never 500, never a false pass (item 6) — DONE
1. Front end: null-safe formatting everywhere in the results page; rendering moved inside the error handling so a failure shows a message with "Back / Retry" and always clears the busy state; one global handler for unexpected errors.
2. `report.py` / `comparison_report.py`: charts and tables tolerate missing values ("not measurable") instead of 500.
3. Analysis modules: a value that cannot be measured becomes a structured **"not measurable" → fail with a reason**, never NaN → pass. One sanitising choke point before JSON/DB.
4. **Input-quality check at upload** (numbers from HQ, large margins): saturation (HQ ≤ 0.07 % of pixels at max; saturated scans 97 %), registration anisotropy (HQ ≤ 1.0025; clipped 1.05), side rulers found (HQ 4/4; saturated 0), registration score and best-vs-second margin (HQ ≥ 7.3 / ≥ 0.89; clipped 6.5 / 0.12). Result: a clear banner — "image saturated / phantom cut off at the detector edge / phantom not recognised — repeat the exposure" — a quality badge in History, and such a scan **cannot become baseline or stored layout** without the admin password. Wording note: these Fuji images are over-ranged (EI 2478 vs target 876, S = 59), not under-exposed; the message will describe what is seen ("no usable signal"), not guess the cause.
5. Time limit, as asked: a cooperative deadline between analysis steps (default e.g. 120 s, configurable) that stores **"failed: timed out"** as the result; the browser request gets a matching timeout. Honest limitation: a single numpy call cannot be interrupted mid-way inside the current worker model; the steps are 0.1–4 s each, so the check between steps is sufficient. Registration during upload moved off the event loop so one slow scan no longer stalls other users of that worker.
6. Reload resumes the open analysis; the upload page lists unfinished analyses ("continue").

### WP2 — Record life-cycle (items 1, 2, 5) — DONE
1. Make **"finalized" real**: `finalized_at/by` columns, set by Finalize (refused when there are no results). One shared definition of "protected" = finalized, signed-off, or baseline.
2. **Discard** (item 1): new endpoint, confirmation only, works even with no admin password configured; refused once protected (then the existing admin delete applies, unchanged). State check and delete in one transaction. Hard delete → gone from History, trends, label counts, exports, duplicate check. Button inside the wizard and in History; no full-record download just to open the dialog (today 195 kB).
3. If the discarded analysis had stored the phantom's layout (happens at step C, *before* finalize), the previous layout is restored, else forgotten.
4. **Re-run** (item 2): "Re-run analysis…" from step F and History, same record (id, labels, dates, file kept), three starting points — numbers only / from measuring points / from registration. Free before finalization; **admin password + reason after**. Previous results kept as a revision (cap 5, original pinned) with "Discard re-run and restore". No upload, image reused: ~190 kB total instead of 7.5 MB.
5. Lock extended to finalized records and to the three endpoints that currently ignore the sign-off lock (`/confirm`, `/compute_preview` SID write, `/finalize`) **[verified gap]**.
6. **Duplicates** (item 5): browser hashes the file and asks the server *before* uploading (saves 7.5 MB ≈ 2 min per true duplicate; falls back silently where unavailable); dialog rewritten — shows both sides (file name, size, modified time, upload and acquisition time, stage, thumbnail 4–8 kB) with **"Continue this analysis"** as the primary action; SOPInstanceUID as a non-blocking "same exposure re-exported" hint; never the file name. Zip uploads handled per member. Chosen-file box shows size + modified time so the two `003_0000.dcm` can be told apart.
7. Small fixes found on the way: web compute does not stamp algorithm version; re-register leaves a stale status chip; cached image served after a delete on another worker.

### WP3 — Viewer and bandwidth (item 3 + cross-cutting) — mouse and picking DONE; bandwidth items 4–8 open
1. Mouse rules everywhere in the viewer: **left = place/drag, middle-drag = pan** (also Space+drag and right-drag for mice/touchpads without a middle button), wheel = zoom at cursor; browser autoscroll/context menu suppressed on the canvas.
2. Corner picking: panning and zoom stay available while picking; points are draggable handles with arrow-key nudge and "Undo last point"; **no auto-submit on the 4th click** — an explicit "Apply corners"; a magnifier loupe at the cursor. Same for low-contrast block corners and field edges.
3. Optional: server snaps rough clicks to the sub-pixel phantom edge (prototype started) — makes zooming on every corner unnecessary.
4. **Window/level in the browser** on the once-downloaded image: instant, zero traffic. When zoomed in, only the visible region is fetched at native resolution (small).
5. Image cacheable privately (upload bytes never change); lighter base image (JPEG/WebP ≈ 100–200 kB instead of 0.9 MB PNG — measured 0.06–0.15 MB at q70).
6. Compression of JSON/HTML/JS inside the app (nothing is compressed today): record 197 → 90 kB, app.js 127 → 37 kB, propose/compute ~95 → ~43 kB.
7. Upload: progress bar with time remaining and Cancel (today the status text vanishes after 6 s); optional in-browser gzip of the DICOM (measured 7.5 → 2.9 MB).
8. Reports: single report is 1.7 MB of which 1.33 MB is one embedded overlay PNG → JPEG/downscaled, ~0.4 MB.

Measured today at 512 kbit/s: upload 118 s · each image/W-L change 15 s · report 26 s · comparison 9 s per scan.

### WP4 — Low contrast (item 4) — validated on HQ only
1. **Enhanced block view** in step C: small crop of the block (tens of kB), flattened + ~1 mm smoothing + window local to the block, with its own instant W/L; ghost outlines of the expected disc positions; per-disc "detectable / at the limit" indicator.
2. Detection: fit the rigid 8-disc pattern with shift **and small rotation** (wider than today's ±2.5 mm, which the dark print reaches), with a confidence value; fall back to the stored layout when confidence is low.
3. **Insert orientation resolved automatically** (0° / 180°) from the contrast order, stored in the phantom's layout profile so it is constant per phantom; the order check made strict enough to be meaningful.
4. The measurement definition (CNR formula, ROI sizes) stays unchanged; acceptance = HQ benchmark: marks on discs on 33/33, orientation correct on 15/15 dark and 12/12 light.

### WP5 — Comparison with images (item 7)
Table with **columns = scans, rows = whole phantom + line pairs + wedge + low contrast + uniformity**, all rectified to the phantom frame, key numbers under each cell, click to enlarge, optional ROI overlay. Same window policy across scans (fixed from the first/baseline scan, with a per-image toggle). Thumbnails rendered server-side from the stored file, cached on disk under `data/`. Budget ≈ 60–70 kB per scan. Failed/unregistered scans get a labelled placeholder. Print layout checked.

### WP6 — Phantom build variants and verdict logic (from the scan review)
1. Line-pair strip: test both directions along the strip and keep the one whose measured pitches match monotonically (fixes 27/27 Carestream); assignment by refined pitch, not the coarse estimate; store the direction per phantom. Investigate the 3-of-5 detection cases and the high-dose "irregular spacing" cases against the benchmark.
2. Overall verdict: "n/a" no longer masks "pass" (e.g. "pass — 1 test not applicable").
3. MONOCHROME1 inversion from the bit depth, not the image maximum (changes stored means for CR scans → needs a decision on existing CR records).
4. Extract ExposureIndex / Sensitivity / DeviationIndex and acquisition dates; show them; include a usable signature for CR.

---

## 3. Decisions needed from you

| # | Question | My recommendation |
|---|---|---|
| D1 | What ends the "easy delete / free re-run" window? | Pressing **Finalize**. Additionally always protected: any validation ruling, baseline flag. |
| D2 | Existing records (Finalize never stored anything): which count as finalized after the upgrade? | Those with a ruling, the baseline flag, or a "finalized" audit entry. Everything else stays freely editable/discardable so the field-test clutter can be cleaned. |
| D3 | Discard = hard delete or a trash with grace period? | Hard delete (audit log keeps the record of it). A trash needs filters in ~12 queries and keeps 7 MB files. |
| D4 | Re-run of a **signed-off** record: one admin step that also withdraws the ruling, or withdraw first? | One step; the ruling is kept in the revision and must be given again on the new numbers. |
| D5 | No admin password configured: may finalized records be re-run / deleted? | No (as today for delete). Discard of unfinished records works regardless. |
| D6 | Should marking a scan as baseline imply Finalize? | Yes. |
| D7 | Bad-quality scan (saturated / clipped / not recognised): block analysis, or allow with a warning? | Allow analysis with a prominent warning and a History badge; **block baseline / stored layout** unless admin overrides. |
| D8 | Time limit for an analysis? | 120 s server-side, configurable; browser gives up slightly later and offers Retry. |
| D9 | Overall verdict when a test is "not applicable"? | "pass" with a note; n/a never outranks pass. |
| D10 | Line-pair strip reversed and low-contrast insert rotated in some builds — are both builds intended to stay in use? | Assumed yes (you confirmed both prints are valid) → auto-detect + store per phantom ID; each phantom ID keeps its own baseline. |
| D11 | Fix MONOCHROME1 inversion now (shifts mean values of CR scans already analysed in the field)? | Yes, now — only field-test records exist; re-run them afterwards. |
| D12 | Comparison images: loaded from the server (small, cached) or embedded so the saved HTML works offline? | Loaded by default + an "embed for saving/e-mail" option. |
| D13 | In-browser gzip of uploads (−60 % upload bytes; touches ingest)? | Yes, as the last step of WP3. |
| D14 | ~~Can you pull `logs/audit.log`?~~ | Done — it settled item 5: no false positive has ever occurred. |
| D15 | Should confirming step C keep overwriting the phantom's shared layout every time, as it does now? | No. Make it an explicit choice ("use these measuring points for future scans of this phantom"), default **on** for the first analysis of a phantom and **off** afterwards, and never offered for a scan that fails the quality gate. The audit log shows six overwrites in twelve minutes, three of them from unusable exposures. |

---

## 4. Order, size, and what is not yet verified

Suggested order: **WP0 → WP1 → WP2 → WP3 → WP4 → WP5 → WP6** (WP6.2 "n/a masks pass" is tiny and could ride with WP1). Rough size: WP0 S · WP1 M · WP2 L · WP3 L · WP4 M–L · WP5 M · WP6 M. Each package ends with the full test suite **including the sample-based tests** and the HQ benchmark; documentation (USER_GUIDE, DESIGN, DEPLOYMENT, MAINTENANCE) updated in the same package.

Not yet verified / open:
- Items 1, 2, 5 were each investigated in depth with reproduction, but the independent second review of those three reports did not run (the review was interrupted by the usage limit). I spot-checked their key claims; a second pass is cheap to add before WP2 starts.
- Front-end behaviour was established from the code and from server responses, not in a real browser — there is no JS test harness in the repo. A manual test checklist (throttled connection, HTTPS sub-path, plain-http LAN) accompanies WP1–WP3.
- Behaviour under several gunicorn workers was reasoned from code and simulated, not run multi-process.
- Line-pair detection failures at some orientations/doses (WP6.1) are diagnosed only as far as "3 of 5 groups found / spacing irregular"; root cause to be found inside that package.
- The enhanced low-contrast view was validated by eye on a handful of HQ and field scans, not yet across all 33.

Housekeeping note: the stray runtime files that the test suite used to leave in the working tree are gone, and WP0.2 stops them coming back.
