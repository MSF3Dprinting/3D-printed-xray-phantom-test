# Next round — approved 2026-09-27, built in small steps

This round covers three requests of yours:

- **A. Exact pictures.** *"We are losing quality of image on screen … The
  compression could never lead to lose of visual details! Never in any part of
  the app. This is a critical qc tool we cannot compromise it."*
- **B. Field analysis.** *"after upload and registration offer … Field analysis,
  which will not offer user the steps … displays only the results … possible
  to open the full report and also return in steps to full analysis."*
- **C. PDF export.** *"add the export to PDF to every report and ensure there is
  correct formatting (like alignment, sliders not visible etc)."*

Your decisions (numbers as in the decision table of 2026-09-27) are quoted
where they apply.

## How we work (your instruction of 2026-09-27)

- **No background agents.** I build each step myself, in the foreground, in the
  project folder.
- **Each step takes at most 10–15 minutes on this computer**, tests included.
  The test times below were measured on this computer on 2026-09-27.
- After each step I report:
  - what changed;
  - the test result;
  - the numbers measured on the real scans;
  - whether anything is still running.

  You check if you want to, and say "go" for the next step.
- **Only measured numbers.** Every figure below says where it was measured.
  Anything not measured yet says *"measured in step N"*. It is never guessed.
- The scans exist only on this computer, which is why the building happens here.
- The standard scans for measuring are:
  - PH0730_03 (Philips);
  - CS000001 (Carestream);
  - F004 (field Fuji, usable).

  More scans are named where a step needs them. The faulty field scans are
  never used to define anything.

## What the plan is based on — measured

**The picture losses.** Measured 2026-09-27 on 9 real scans:

- Philips: PH0730_03, PH0727_05, PH0730_05;
- Carestream: CS000001, CS000004, CS000018, CS000036;
- field: F002, F004.

Seconds are bytes divided by 64 kB/s, which is the 512 kbit/s field link; they
were not timed on a real field link.

| What | Today | Measured lossless replacement |
|---|---|---|
| Viewer picture | JPEG quality 75 at 1600 px, 93–158 kB (1.5–2.5 s). On the Philips scans it keeps only 0.3–1.5 % of the finest line-pair group's bar contrast (2 lp/mm), so those bars are erased. On Carestream and field scans it keeps 13–28 %. | Lossless WebP at viewer size (about 1000 px): 144–323 kB (2.2–5.0 s). Decoded, every pixel equals the render. |
| Zooming in | Only enlarges the same 1600 px picture. The scan's own pixels are never fetched. | The scan's own pixels in 256 px tiles: 18–31 kB each, 15–25 ms of server work each. The scan's own pixels keep 99–103 % of the finest group's bar contrast. |
| Grey levels | Rounded down, so pictures are half a level too dark. Shrinking uses a sharpening filter (Lanczos), which draws faint halos at edges. | Round to nearest; shrink by averaging. No extra bytes. |
| Duplicate-dialog thumbnail | JPEG, 3.6–4.5 kB | Lossless WebP, 10–11.5 kB |
| Disc close-up | PNG, 17–21 kB (already lossless). Its contrast slider stretches 8-bit pixels in the browser, so only 87–171 of 256 grey levels remain. | Lossless WebP, 14–16.5 kB. Each contrast step is rendered by the server: 10–13 kB per step. |
| Printed report | Overview 145–185 kB JPEG. Charts reduced to 64 colours, 107–112 kB. Whole report 300–335 kB (4.7–5.2 s). | Overview 230–323 kB lossless. Charts 129–135 kB lossless WebP. Whole report 415–515 kB (6.5–8.0 s). |
| Comparison pictures | JPEG, 43–55 kB per scan. The browser stretches each picture onto another scan's window, which gives errors of 14–47 grey levels. | Stays JPEG at today's size (your decision 2), but the server renders each picture directly on the window shown. Same bytes. |
| Upload | Packed losslessly; all 38 scans came back byte-identical | No change |

Measured on this computer (2026-09-27):

- Encoding one 1000 px picture:
  - PH0730_03: WebP 318 kB in 1.24 s; PNG 374 kB in 0.94 s.
  - CS000001: WebP 309 kB in 1.00 s; PNG 335 kB in 0.34 s.
- This server can write WebP (Pillow reports it).
- This computer has PyPDF2 for reading PDFs, and Edge.

**Field analysis dry run.** Measured 2026-09-27 on all 33 reference scans and
the 5 field scans:

- The first scan of each set saved the points; every later scan ran
  automatically. 31 of 33 automatic runs finished.
- The 2 that stopped were the over-exposed field scans F003 and F003B. They
  stopped at the image-quality check, before anything was measured.
- All 31 gave exactly the same numbers as the step-by-step workflow.
- The server needed 1.2–3.1 s per run (median 2.4 s).
- The saved points were never changed.
- Registration on the 33 reference scans:
  - all 4 ruler lines found every time;
  - ruler-line error 0.11–0.55 mm. *Corrected 2026-09-28:* that is the
    root-mean-square of the four lines on each scan. The worst single line
    goes up to 0.90 mm — see step 19;
  - recognition score 7.33–7.89;
  - lead over the next-best placement 0.89–1.83.
- Points saved from the weak exposure CS000021 sat 2–5 mm off, and all 14 later
  scans got a low-contrast "warn".
- A wrong phantom ID was not detected in any of 834 wrong-phantom pairs.
- In that dry run the six Philips scans were filed under one phantom ID.
  **They are two phantoms (HmmEi and A8Tgg, your answer to question 7), so
  this round's dry run repeats it with two IDs.**

**Test times.** Measured on this computer 2026-09-27, per file:

- The files Part A touches: 557 tests in 6 min 16 s.
  - test_comparison_pictures 98 s, test_authorization 65 s,
    test_less_waiting 52 s;
  - test_block_view 29 s, test_bandwidth 28 s, test_lighter_downloads 25 s,
    test_correctable_picking 21 s, test_resume_after_reload 19 s,
    test_store_labels 16 s, test_duplicate_upload 15 s;
  - test_security, test_review_fixes, test_frontend_integrity and
    test_upload_progress about 1 s each or less.
- The files Part B touches: 352 tests in 7 min 57 s.
  - test_unusable_exposures 76 s, test_rerun 72 s,
    test_layout_save_choice 58 s, test_discard_flow 54 s,
    test_orientation_per_phantom 45 s, test_round3_journey 41 s;
  - test_phantom_layout 31 s, test_quality_gate 25 s, test_stale_state 24 s,
    test_verdict_wording 19 s, test_signed_off_is_protected 13 s,
    test_analysis_timeout 11 s;
  - test_baselines 5 s; test_schema_upgrade and test_insert_orientation under
    1 s.
- The whole suite, last run 2026-09-23: 1490 tests in 24 min 23 s.

## Part A — exact pictures *(first)*

**Step 1 — One way to make every picture of the scan**
- **From:** request A.
- **What:** a new module, `phantom_qa/imaging.py`, that everything else uses:
  - window → 8 bits, rounded to nearest;
  - shrinking only by averaging;
  - lossless WebP, or PNG as fallback;
  - a decoder for the checks.
- **Tests:** new `test_imaging.py` (12 tests).
- **Measure:** bytes and encoding time at 1000 px for PH0730_03 and CS000001,
  and that each picture decodes exactly.
- **Done when:** the tests pass and the measured numbers match the ones above.
- **Done 2026-09-28.** The 12 tests pass in 3.4 s. Measured at 1000 px with
  the viewer's default window, both pictures decoding exactly:

  | Scan | Picture | WebP | PNG | Today's viewer JPEG (1600 px) |
  |---|---|---|---|---|
  | PH0730_03 | 998×1000 | 325 kB, 1.37 s to encode | 382 kB, 0.97 s | 108 kB |
  | CS000001 | 837×1000 | 316 kB, 1.00 s | 343 kB, 0.33 s | 158 kB |

  Averaging down to 1000 px takes 0.13 s. The sizes are 2–3 % above those of
  2026-09-27. The difference is the new rounding and averaging, which change
  the picture slightly, so these values replace the older ones.

**Step 2 — Viewer picture exact: server side**
- **From:** request A, plus my measurements (grey levels, halos).
- **What:**
  - the viewer picture is made through step 1 as lossless WebP or PNG;
  - the JPEG address and its settings are removed;
  - every picture address carries a version number;
  - picture responses are kept by the browser for 30 days and marked "never
    changes, do not recompress". Today they are kept 24 hours, with no
    "do not recompress" mark;
  - ~~the server's text compression skips pictures. Today it compresses them
    too, for no gain~~ — **wrong, corrected 2026-09-28:** it already skips
    every `image/` type, and a test guards that. Lossless WebP falls under
    the same rule;
  - a deleted analysis still answers "not found" and drops its cached
    pictures.
- **Files:** `webapp/main.py`.
- **Tests:** test_bandwidth, test_lighter_downloads, test_authorization,
  test_security, and new `test_exact_viewer.py`. The existing files take about
  2 min.
- **Measure:** bytes and server time for the sizes a 1366×768 and a
  1920×1080 laptop ask for, at 100 % and 150 % screen scaling, on PH0730_03,
  CS000001 and F004.
- **Done 2026-09-28.**
  - The JPEG route is removed. The viewer and the duplicate-dialog thumbnail
    ask for lossless WebP, with the picture version in the address. The page
    learns the version at start, from the same call that brings the time
    limit.
  - The server lets the browser keep a picture for 30 days (private,
    immutable, no-transform) only when its address names the current version.
    Otherwise it sends "no-store, no-transform".
  - Only the lines needed to keep the app working changed in the browser
    code. The sizing to the screen is step 3.
- **Tests (2026-09-28):**
  - new `test_exact_viewer.py`: 38 tests, 60 s. The real Philips sample scan
    is checked too: every pixel of the full-size picture equals the stored
    value, windowed;
  - the 14 files that touch the picture routes, headers or page code: 509
    tests pass. The run took 5 min 36 s, plus 28 s to re-run the one file
    whose test needed its expectation widened to `.webp`.
- **Measured (2026-09-28).** The measurements went through the real route. The
  viewer's actual size on those laptops is measured in step 3 with Edge, so
  here each longest side was measured:

  | Longest side | PH0730_03 WebP / PNG | CS000001 WebP / PNG | F004 WebP / PNG |
  |---|---|---|---|
  | 1024 px | 344 / 404 kB, 0.59 s | 333 / 360 kB, 0.42 s | 317 / 345 kB, 0.36 s |
  | 1536 px | 835 / 954 kB, 1.16 s | 756 / 812 kB, 0.88 s | 772 / 821 kB, 0.75 s |
  | 2048 px | 1565 / 1747 kB, 1.92 s | 1350 / 1433 kB, 1.45 s | 1400 / 1470 kB, 1.34 s |
  | Full scan | 3170 / 3501 kB, 3.56 s | 2872 / 3024 kB, 2.82 s | 1528 / 1606 kB, 1.43 s |

  Times are the server's time for the WebP. WebP is 8–15 % smaller than PNG
  at 1024 px, not the 12–21 % measured on 2026-09-27 at other sizes. The code
  comments were corrected to the new figure. The duplicate-dialog thumbnail
  (200 px) is 10.5 kB as WebP.

**Step 3 — Viewer picture exact: browser side**
- **From:** request A, my measurements (blur at screen scaling), your
  decision 16.
- **What:**
  - the page checks once whether the browser shows lossless WebP, and asks
    for PNG if not;
  - it asks for a picture exactly as large as the viewer on this screen,
    rounded up in 128 px steps so the cache can reuse it, and never larger
    than the scan;
  - the viewer is sharp on laptops set to 125–150 % scaling;
  - there is no smoothing when enlarged: you see real pixels;
  - the duplicate-dialog thumbnail becomes lossless;
  - the resume banner says "in this browser".
- **Files:** `app.js`, `style.css`.
- **Tests:** test_frontend_integrity, test_duplicate_upload,
  test_resume_after_reload, test_less_waiting, test_upload_progress,
  test_exact_viewer. The existing files take about 1.5 min.
- **Check:** a headless Edge run confirms the page picks WebP.
- **You can check:** open an analysis; the picture is sharp.
- **Done 2026-09-28.**
  - **One change from the plan:** the size is rounded *down* to a 128 px
    step, not up. Rounded up, the picture is larger than the screen and the
    browser shrinks it, which is the softening this step removes. Rounded
    down, the fit view shows one picture pixel per screen pixel, placed on
    whole screen pixels, exactly as the server averaged it.
  - A viewer resized past a step asks for a new picture once resizing stops,
    and keeps the view where it was.
  - The viewer is now shown before its picture is requested. Measured on a
    hidden viewer, the picture would come at the smallest size and be
    downloaded twice.
  - A picture for an analysis that is no longer open is not drawn.
  - The WebP check uses a real one-pixel lossless picture: decoded, it is
    1×1 VP8L.
- **Tests (2026-09-28):** the 9 affected files, 259 tests, pass in 5 min 54 s.
  That includes the syntax check of the page code and 14 new structural tests
  in `test_exact_viewer.py`.
- **Measured in headless Edge (2026-09-28).** Each setting opened PH0730_03
  and F004. The Edge window was the screen minus 133 px for the taskbar and
  toolbars; that 133 px is an assumption.

  | Laptop screen | Viewer (CSS px) | Canvas (screen px) | Picture at fit | Size (PH0730_03 / F004) |
  |---|---|---|---|---|
  | 1366×768 at 100 % | 882×432 | 882×432 | 383×384 | 40 / 37 kB |
  | 1920×1080 at 100 % | 1436×744 | 1436×744 | 639×640 | 123 / 112 kB |
  | 1920×1080 at 125 % | 1052×529 | 1315×661 | 639×640 | 123 / 112 kB |
  | 1920×1080 at 150 % | 796×385 | 1194×578 | 511×512 | 75 / 69 kB |

  - At every setting: WebP was picked; one picture pixel per screen pixel;
    whole-pixel placement; no smoothing; no page errors; no failed requests.
  - The very first launch of headless Edge with a fresh profile laid the page
    out 40 px wide. Two repeats were normal. This is a quirk of the test
    browser, not of the app.
  - **What this means.** The viewer is wide but short, so at fit the phantom
    is only 384–640 px tall. The opening picture is therefore smaller than
    the old JPEG (108–158 kB) as well as exact.
  - **Until step 6, zooming in shows the large square pixels of this small
    picture.** The old 1600 px JPEG showed some lossy detail there. So nothing
    is deployed before step 6; the plan already commits only at the end of
    Part A.

**Step 4 — Window/level preview, labelled**
- **From:** your decision 1.
- **What:**
  - while you drag the slider, the quick preview shows the label
    *"Preview — exact picture loading"*;
  - the label goes away only when the exact picture for that window is on
    screen;
  - if loading fails, the page says *"Exact picture could not be loaded — try
    again"*;
  - a late answer for an older window is never drawn.
- **Tests:** test_frontend_integrity and test_exact_viewer, a few seconds.
- **You can check:** drag the slider and watch the label.
- **Done 2026-09-28.**
  - The label sits over the picture's top-left corner. It is announced to
    screen readers, and clicks pass through it to the picture.
  - A failed download shows the message in red, with a "Try again" button.
  - Picture requests are numbered, and only the newest answer is used.
  - An exact picture that arrives after the slider has moved on becomes the
    base of a new, labelled preview, instead of being shown as if it matched
    the slider.
- **Tests (2026-09-28):** test_exact_viewer, test_frontend_integrity and
  test_bandwidth: 109 tests pass, with 5 new ones for this step. The run took
  4 min 8 s; the same files took about 1.5 min earlier today, so the computer
  was busier.
- **Checked in headless Edge (2026-09-28, PH0730_03):**
  - moving the slider showed "Preview — exact picture loading" at once;
  - the exact picture replaced it after 0.62 s, and the label went;
  - a download made to fail on purpose showed "Exact picture could not be
    loaded — try again" with the button, while the preview stayed on screen,
    labelled;
  - "Try again" loaded the exact picture;
  - an answer for an older window, held back until a newer one was on
    screen, was ignored;
  - no page errors.

**Step 5 — Full detail on zoom: server side**
- **From:** request A.
- **What:**
  - a new picture address for 256 px tiles of the scan;
  - three levels: full size, ½ and ¼, the smaller ones made by averaging;
  - any window, lossless.
  - the setting `PHANTOMQA_DETAIL_DELAY_S` (default 1.0) reaches the page
    (your decision 9).
- **Files:** `webapp/main.py`, `config.py`.
- **Tests:** test_authorization, and test_exact_viewer, which checks:
  - full-size tiles stitched together equal the full-size render exactly;
  - every level decodes exactly;
  - the stored-picture headers;
  - "not found" after a delete.

  About 1.5 min.
- **Measure:** tile bytes and server time per level on PH0730_03, CS000001 and
  F004.
- **Done 2026-09-28.**
  - New addresses: `/api/analyses/{id}/tile/{level}/{x}/{y}.webp` or `.png`,
    with the window and the picture version.
  - Level 0 is the scan's own pixels. Levels 1 and 2 are the mean of
    exactly the 2×2 or 4×4 block of scan pixels each covers. Edge blocks the
    scan does not fill average what is there. So every level lies on the
    scan's own pixel grid.
  - The pieces and the overview share one window rule. The automatic window
    is worked out once per scan instead of for every picture.
  - The server keeps the levels of the last 4 scans it served.
  - Pieces are kept by the browser under the same version rule as the
    overview, and are not compressed a second time.
  - `PHANTOMQA_DETAIL_DELAY_S` (default 1.0 s; negative or unreadable values
    fall back to 1.0) reaches the page with the login state. It is described
    in `.env.example`.
- **Tests (2026-09-28):**
  - test_imaging and test_exact_viewer: 93 tests pass in 3 min 49 s. They
    include:
    - the level-0 pieces stitched back together equal the full-size picture
      exactly, both formats, two windows;
    - levels 1 and 2 of a scan with uneven sides equal the block means,
      calculated a different way, pixel for pixel;
    - one piece at full size and one at half size of the real Philips sample
      scan are exact;
    - missing pieces answer "not found"; a delete drops the levels;
    - the delay setting, including bad values.
  - test_authorization, test_security and test_block_view: 264 tests pass in
    3 min 22 s.
- **Measured (2026-09-28).** Every piece of each scan, WebP, through the real
  route in-process. "Server" is the whole request (checks, logging and
  encoding); the 15–25 ms of 2026-09-27 was the encoding alone.

  | Scan | Level | Pieces | Whole piece (median) | All pieces | Server per piece |
  |---|---|---|---|---|---|
  | PH0730_03 | 0 (full) | 144 | 14–33 kB (27.6) | 3.18 MB | 96 ms median |
  | | 1 (½) | 36 | 19–29 kB (26.5) | 0.72 MB | 89 ms |
  | | 2 (¼) | 9 | 20–23 kB (20.9) | 0.16 MB | 88 ms |
  | CS000001 | 0 | 120 | 9–36 kB (32.7) | 2.86 MB | 74 ms |
  | | 1 | 30 | 15–36 kB (31.6) | 0.73 MB | 53 ms |
  | | 2 | 9 | 22–31 kB (26.2) | 0.17 MB | 54 ms |
  | F004 | 0 | 63 | 0–37 kB (34.0) | 1.53 MB | 82 ms |
  | | 1 | 20 | 9–33 kB (29.8) | 0.35 MB | 62 ms |
  | | 2 | 6 | 18–26 kB (22.1) | 0.08 MB | 72 ms |

  - The first piece of a scan also makes its ½ and ¼ levels: 1.29 s
    (PH0730_03), 0.83 s (CS000001), 0.42 s (F004).
  - A whole piece is 0.3–0.6 s at 512 kbit/s. The centre piece as PNG was
    1–4 kB larger than a typical WebP piece.

**Step 6 — Full detail on zoom: browser, drawing**
- **From:** request A, your decision 9.
- **What:**
  - after the view has been still for the set time (1 s), the page fetches
    the tiles for what is on screen, at the level the zoom needs;
  - tiles are drawn at their exact scan position, so the measuring areas
    stay aligned;
  - a coarser tile is never drawn over a finer one;
  - a new window gives new tiles.
- **Tests:** test_frontend_integrity, test_exact_viewer, and
  test_correctable_picking (the outlines stay aligned). About 30 s.
- **You can check:** zoom into the line-pair strip on a Philips scan; the
  finest group's bars are visible.
- **Done 2026-09-28.**
  - Any change of the view (zoom, pan, fit, resize, a new picture) starts
    the stillness time again. Drawing for other reasons, such as the mouse
    moving, does not.
  - When the time is up, the page picks the level whose pixels are closest
    to the screen's without being coarser. It fetches the pieces that
    overlap the viewer and draws them in scan pixels, the coordinates of
    every outline.
  - At fit, and whenever the overview already has a pixel for every screen
    pixel, nothing is fetched.
  - Levels are drawn coarsest first. Pieces are keyed by the window, so a new
    window clears them, and none are fetched or drawn while a preview stands
    in.
  - Opening another analysis starts with no pieces. At most 600 are kept in
    memory; the browser keeps the rest on disk.
  - Not yet: at most 2 downloads at once, centre first, cancelling, and the
    "Loading full detail…" label. Those are step 7.
- **Tests (2026-09-28):** test_exact_viewer, test_frontend_integrity and
  test_correctable_picking: 122 tests pass in 3 min 13 s.
  - 7 new structural tests, one of which checks that the page and the server
    use the same piece size and number of levels.
  - The variants of each server test (formats, windows, sizes) now loop
    inside one test with one upload, instead of one test and one upload per
    variant. No check was dropped.
- **Checked in headless Edge (2026-09-28).** PH0730_03, page 1920×947 at
  100 % (viewer 1460×836, fit picture 767×768). Zoomed on the finest
  line-pair group, G2.0 (2 lp/mm):

  | Zoom (screen px per scan px) | Pieces on screen | Drawn after the zoom | Downloaded |
  |---|---|---|---|
  | 2 | 9, none fetched during the 1 s wait | 1.96 s (1 s wait + 0.96 s) | 280 kB |
  | 1 | 35 (9 already there) | 1.39 s | 973 kB in total |

  - Sharpness (mean difference between neighbouring screen pixels) over the
    group at zoom 2 went from 0.77 before the pieces to 3.17 after.
  - In the screenshots, the 2 lp/mm bars are not visible before and clearly
    visible after. The outline and its label stay in place.
  - No page errors.
  - At 512 kbit/s, the 280 kB would take about 4.4 s. That is a calculation
    from the bytes, not timed on a slow link.
  - The check tool now sets the page size directly. The first-launch quirk of
    the test browser had made the first attempt meaningless: a tiny viewer
    and one piece.

**Step 7 — Full detail on zoom: never blocks**
- **From:** request A. You asked that nothing locks while you zoom and pan.
- **What:**
  - at most 2 tile downloads at once;
  - the centre of the screen first;
  - tiles for areas you have left are cancelled;
  - a small *"Loading full detail…"* label is shown until the screen is
    covered.
- **Measure** in headless Edge:
  - the longest freeze of the page while zooming and panning (it must stay
    under 0.2 s);
  - the number of tiles and kB for a 2× zoom on the line-pair strip of
    PH0730_03 and CS000001.
- **Done 2026-09-28.**
  - Pieces download with `fetch`, so they can really be cancelled. They are
    decoded off the page's own thread (`createImageBitmap`, no colour
    conversion), and the browser cache is used as for any picture.
  - A queue feeds at most 2 downloads at once, sorted nearest the screen
    centre first.
  - Every change of the view cancels at once what is no longer on screen:
    running downloads are aborted, waiting ones dropped. Pieces already here
    stay.
  - A piece that fails is asked for again after the stillness time, 3 times
    at most. Then the viewer says "Full detail could not be loaded — zoom or
    pan to try again".
  - The note over the picture says "Loading full detail…" (blue) while part
    of the screen still shows enlarged overview pixels. The exact-picture
    messages take priority over it.
- **Tests (2026-09-28):** test_exact_viewer (page tests) and
  test_frontend_integrity: 98 tests pass in 47 s, 6 of them new. The slow
  server tests were not re-run; the server did not change in this step.
- **Measured in headless Edge, network slowed to 512 kbit/s and 150 ms
  (2026-09-28).** Page 1920×947, viewer 1460×836.

  | Situation | Pieces | Downloaded | Screen covered after the zoom | At most at once | First two to arrive |
  |---|---|---|---|---|---|
  | PH0730_03, 2× on the line-pair strip | 12 | 366 kB | 8.1 s (incl. 1 s wait) | 2 | ranks 1 and 2 nearest the centre |
  | CS000001, same | 8 | 274 kB | 6.3 s | 2 | ranks 2 and 1 |

  - **Never blocks.** No page task over 50 ms, and the longest gap between
    screen frames was 17 ms (one frame at 60 per second).
  - That held through real mouse-wheel zooming (8 steps) and then 3 s of
    panning while 40 pieces were downloading or waiting. The running
    download was cancelled; the waiting pieces for the old view were
    dropped from the queue (not counted separately).
  - "Loading full detail…" was shown while pieces were missing, and gone once
    the screen was covered.
  - No page errors.
  - **What it means on a field link:** looking at the line-pair strip at 2×
    takes 6–8 s to fill in completely the first time; afterwards it comes
    from the browser's cache. Step 8 (preloading the strip and the disc block)
    is meant to take that wait away in the step-by-step workflow.

**Step 8 — Preload the line-pair strip and disc block**
- **From:** your decision 8.
- **What:**
  - in the step-by-step workflow only, full-size tiles of those two areas
    download quietly after an analysis opens;
  - they have lower priority than what is on screen, and the same limit of
    2 downloads at once.
- **Measure:** tiles and kB on PH0730_03, PH0727_05, CS000001, CS000018 and
  F004. The "about 0.5 MB" said on 2026-09-27 was an estimate; this step
  replaces it with measured values.
- **Done 2026-09-28.**
  - **Server:** the analysis record now carries `detail_regions`, boxes in
    scan pixels:
    - one around each of the 5 line-pair groups (the group plus 4 mm);
    - one halfway between each two neighbouring groups, so the strip is
      covered as a band;
    - one around each of the 8 discs (the 16 mm background ring plus 4 mm).

    The boxes come from the placed measuring points once they exist, and
    before that from the phantom definition through the registration.
  - **Page:** once the picture of an analysis opened in the step-by-step
    workflow is on screen, the full-size pieces of those boxes download in
    the background.
    - One piece at a time, and only while nothing downloads for the screen.
    - A piece still waiting in the preload that comes on screen moves to the
      front.
    - Moving the view never cancels the preload.
  - A full-size piece that has arrived now serves every zoom level. It is
    drawn over coarser ones, and it counts as detail present at any level.
- **Tests (2026-09-28):** test_exact_viewer and test_frontend_integrity: 112
  tests pass. Among them are 9 new ones for this step, including one on the
  real Philips sample: each box, worked out before any measuring points
  exist, holds the group placed later.
- **Measured (2026-09-28), preload cost:**

  | Scan | Pieces | Size | At 512 kbit/s |
  |---|---|---|---|
  | PH0730_03 | 16 | 494 kB | 7.7 s |
  | PH0727_05 | 13 | 402 kB | 6.3 s |
  | CS000001 | 18 | 609 kB | 9.5 s |
  | CS000018 | 16 | 483 kB | 7.5 s |
  | F004 | 11 | 399 kB | 6.2 s |

  - **Coverage:** after the measuring points were placed, every disc and
    every line-pair group lies inside a box, except one on CS000018. There,
    G1.4 was placed at (−102.2, −4.5) mm: beyond the end of the strip, about
    10 mm to its side. G2.0 was detected beyond the other end, and G1.2 and
    G1.1 were not detected at all. This is the open line-pair question on
    the blue prints (the software does not place those groups where the bars
    are), not a gap in the preload. The line-pair code is untouched.
  - Before the halfway boxes were added, G1.6 on CS000018 also fell outside,
    by up to 7.2 mm. The band fixed that for 1 piece more on 3 of the 5 scans.
- **Checked in headless Edge at 512 kbit/s (2026-09-28, PH0730_03, page
  1920×947):**
  - the picture was on screen 4.0 s after opening;
  - the preload (16 pieces, 494 kB) finished 15.0 s after opening, with no
    label at fit;
  - zooming 2× afterwards:

  | Where | Pieces on screen | Already preloaded | Whole screen sharp after |
  |---|---|---|---|
  | Line-pair strip at G1.4 | 9 | 7 | 2.2 s (2 more pieces, 61 kB) |
  | Finest group G2.0 | 12 | 6 | 4.1 s (6 more, 165 kB) |
  | Disc L4 | 8 | 8 | at once |

  - Without the preload (step 7), 2× on the strip took 8.1 s. At 2× the
    screen shows about 100 × 60 mm of phantom, more than a group, so the
    surroundings still load; the group or disc itself is sharp at once.
  - No page errors.

**Step 9 — Disc close-up**
- **From:** request A, my measurements.
- **What:**
  - it is labelled *"Processed view — smoothed to make the discs visible"*;
  - each contrast step is rendered exactly by the server and kept by the
    browser;
  - it is lossless.
- **Files:** `webapp/main.py`, `app.js`.
- **Tests:** test_block_view and test_exact_viewer, about 40 s.
- **Measure:** bytes per contrast step on PH0730_03 and CS000001.
- **Done 2026-09-28.**
  - The close-up keeps its unrounded processed values and its own window.
  - Each contrast step is that window narrowed (or widened) about its
    middle, made from those values by the server. It is sent lossless, as
    `lowcontrast_view.webp` or `.png` with `key` and `gain`.
  - The slider moves in steps of 10 between 20 and 300 %; the server rounds
    to the same steps. The picture on screen stays until the new step has
    arrived, and only the newest step is drawn.
  - The browser keeps each step for 30 days under the close-up's name. The
    name now includes the picture version.
  - The page says "Processed view — smoothed to make the discs visible", and
    the note points to the main image for the scan's own pixels.
  - The browser no longer re-maps the close-up at all.
- **Tests (2026-09-28):** test_exact_viewer, test_block_view,
  test_less_waiting, test_authorization, test_orientation_per_phantom and
  test_frontend_integrity: 450 tests pass in 6 min 34 s.
  - 5 new tests, including every step exact against the processed values in
    both formats.
  - The older close-up tests were turned around to the new behaviour, with
    the same intent: caching by name, and one render per placement.
- **Measured (2026-09-28).** Close-up 386×226 px, 29 steps:

  | Scan | WebP per step | PNG per step | Server per step | Grey levels at 300 % |
  |---|---|---|---|---|
  | PH0730_03 | 9.8–15.6 kB (14.3 at 100 %) | 11.7–18.5 kB | 76 ms median | 256 |
  | CS000001 | 10.1–16.1 kB (15.0 at 100 %) | 12.1–19.4 kB | 64 ms median | 256 |

  - The old browser stretch at 300 % left 87 grey levels on both scans.
  - A step is about 0.2 s at 512 kbit/s, and free when seen before.

**Step 10 — Printed report: the scan picture**
- **From:** request A, your decision 10 (WebP in reports).
- **What:**
  - the overview becomes a 1000 px picture, shrunk by averaging, lossless
    WebP;
  - the measuring areas are drawn as sharp vector lines;
  - the JPEG settings are removed. The command-line tool keeps working,
    lossless.
- **Files:** `report.py`, `pipeline.py`.
- **Tests:** test_review_fixes, test_lighter_downloads, test_store_labels, and
  new `test_exact_reports.py`. About 1 min.
- **Measure:** report bytes and build time on PH0730_03 and CS000001.
- **Done 2026-09-28.**
  - `pipeline.report_overview` makes the scan averaged to 1000 px on its
    longest side, in the viewer's automatic window, lossless WebP.
  - The outlines come as an SVG in scan coordinates, laid over the picture
    in one shrink-wrapped frame.
  - `pipeline.outline_shapes` is the one list of outlines. The command-line
    PNG draws the same list, and now averages instead of skipping pixels.
  - `OVERLAY_JPEG` and the report's JPEG path are gone. A finished picture
    handed in as bytes is shrunk by averaging and re-encoded as lossless
    WebP, never lossy.
- **Tests (2026-09-28):** test_exact_reports (new, 11 tests),
  test_lighter_downloads, test_bandwidth, test_review_fixes and
  test_store_labels: 81 tests pass in 1 min 24 s.
  - They include the report picture exact against 2×2 block means, and the
    same on the real Philips sample through the real route.
  - Also: the SVG coordinates, colours and dashes; and a check that the
    frame is exactly the picture's size.
  - The report size limit in test_bandwidth is now 700 kB, from the
    measurement below.
- **Measured (2026-09-28)**, through the real route, the report built twice:

  | Scan | Report | As sent (compressed) | At 512 kbit/s | Scan picture | Outlines | Built in |
  |---|---|---|---|---|---|---|
  | PH0727_03 (test sample) | 608 kB | 445 kB | 7.0 s | 323 kB | 7.1 kB, 64 shapes | 4.3 s (10.3 s the first time) |
  | PH0730_03 | 611 kB | 448 kB | 7.0 s | 325 kB | 7.2 kB, 65 shapes | 3.6–3.9 s |
  | CS000001 | 592 kB | 434 kB | 6.8 s | 316 kB | 6.9 kB, 62 shapes | 4.0–4.4 s |

  - With the JPEG overview the report was 300–335 kB (4.7–5.2 s). Now it is
    within the 6.5–8.0 s range in the approved plan.
  - Charts are 107–112 kB, still palette PNG until step 11.
- **Checked in headless Edge (2026-09-28, PH0730_03):**
  - the frame, the picture and the outlines are each 998×1000 px on the
    page, the picture's own size;
  - in the screenshot every outline sits on what it marks: registration
    border, line-pair groups, discs and rings, wedge steps, uniformity areas
    and field edges;
  - at 3× the lines stay sharp;
  - no page errors.

**Step 11 — Printed report: charts**
- **From:** request A. The 64-colour reduction counts as a loss.
- **What:** charts are lossless WebP; no colour reduction anywhere.
- **Tests:** as step 10, plus test_less_waiting. About 2 min.
- **Measure:** chart bytes and the whole report on the same two scans.
- **Done 2026-09-28.**
  - `report._chart` replaces the palette step. It draws the chart exactly as
    the chart library does, in full colour, as lossless WebP (PNG where the
    server cannot write WebP), and returns a ready data address.
  - The scan picture carries a description ("The scan with the measuring
    areas outlined"), so it can be told apart from the charts.
  - A new test fails if `quantize(`, JPEG or `quality=` ever appears in the
    report's code again.
- **Tests (2026-09-28):** the nine files that build or check reports
  (test_exact_reports, test_lighter_downloads, test_bandwidth,
  test_review_fixes, test_store_labels, test_less_waiting,
  test_exposure_index, test_integrity_logging, test_predeploy_fixes): 207
  tests pass in 3 min 10 s.
  - The chart test now checks every pixel is exactly as drawn, and that it is
    still smaller than the full-colour PNG.
- **Measured (2026-09-28):**

  | Scan | Charts (4) | Before, palette | Whole report | As sent (compressed) | At 512 kbit/s |
  |---|---|---|---|---|---|
  | PH0730_03 | 133 kB | 112 kB | 638 kB | 470 kB | 7.3 s |
  | CS000001 | 126 kB | 107 kB | 617 kB | 455 kB | 7.1 s |

  About +20 kB per report (0.3 s), within the 6.5–8.0 s range in the approved
  plan and under the 700 kB limit in test_bandwidth.

**Step 12 — Comparison report**
- **From:** request A, your decisions 2 and 3.
- **What:**
  - the small pictures stay JPEG at today's size. This is the only lossy
    picture in the app, allowed by your explicit choice;
  - each picture is rendered by the server on the window the page shows;
  - the "each picture on its own window" switch and its script are removed;
  - enlarging shows real pixels;
  - charts are lossless;
  - pictures for **all** scans you select: the 12-scan limit and its note are
    removed;
  - the picture version goes up, so stored pictures are redrawn once.
- **Files:** `comparison_report.py`, `thumbnails.py`, `webapp/main.py`.
- **Tests:** test_comparison_pictures, test_less_waiting, test_bandwidth.
  About 3 min.
- **Measure:** page bytes and build time for 2 and 12 scans, and for all scans
  in the local archive, both the first build and the stored build.
- **Split in two (2026-09-28)** to keep each part within 15 minutes:
  - **12a — the charts;**
  - **12b — the pictures, the window, the switch and script, the 12-scan
    limit, and the measurement.**
- **12a done 2026-09-28.**
  - The comparison charts are encoded like the single report's: exactly as
    drawn, in full colour, lossless WebP (`CHART_MIME`; PNG where the server
    cannot write WebP).
  - The helper threads that overlapped the palette step now overlap this
    encoding. The page is still byte for byte the one encoding each chart in
    turn gives.
  - **Tests:** test_review_fixes, test_less_waiting, test_store_labels and
    test_comparison_pictures: 130 tests pass in 4 min 27 s. The chart test
    now checks every pixel is exactly as drawn.
  - **Measured** on the comparison of PH0730_03 and CS000001 (17 charts, no
    pictures): lossless 309 kB against 299 kB as palette (+10 kB). Built in
    14.1 s against 13.0 s. Both times are far above the 4.6 s recorded
    earlier for two scans, so the computer was slower then, not this change.
- **Found while reading for 12b.** The pictures kept on the server's disk are
  finished JPEGs, each on its own window. Making them on any shared window
  without decoding every scan again means keeping the unrounded sampled
  values instead: about 1 MB per scan on the server's disk, instead of
  60–70 kB. Nothing extra crosses the network. 12b measures it.
- **12b done 2026-09-28.**
  - **What is kept:** `thumbnails` now keeps each region's unrounded sampled
    values (float32, compressed `.npz`, read without pickle) and its own
    window. Picture version 2: files kept the old way are drawn again once,
    and the old file is removed.
  - **Drawing:** `thumbnails.encode_on` draws a region on any window, from
    those values. JPEG quality 75 at today's sizes (PNG where smaller): the
    one deliberate lossy encoding in the application, marked as such. The
    close-up is now reduced by averaging, not a sharpening filter.
  - **The page:** each picture is drawn by the server on the window its row
    names (one per detector and protocol, as before), and the row note says
    which.
    - The browser re-mapping, the "own window" switch and its script are
      gone. The script left only enlarges a picture, and the enlarged
      picture shows its own pixels (`image-rendering: pixelated`).
    - A sentence under the table says the pictures are small JPEGs for an
      overall look, and that fine detail is judged in the viewer.
  - **All scans:** `PICTURE_COLUMNS_MAX` (12) and its note are gone; every
    selected scan gets pictures.
- **Tests (2026-09-28):** test_comparison_pictures, test_review_fixes,
  test_less_waiting and test_store_labels: 130 tests pass in 4 min 21 s,
  plus the new old-cache test.
  - Rewritten with the same intent: every picture's bytes are exactly what
    the server makes on the reference scan's window (two scans of different
    brightness, so a wrong window would show).
  - Each detector group is on its own group's window.
  - Hostile kept values, labels and reasons cannot put markup on the page.
  - The size budget is now measured through `encode_on`.
  - The limit test now checks that all 20 of 20 selected scans get
    pictures.
- **Measured (2026-09-28).** A throwaway archive of all 33 reference scans,
  each analysed fully (built in 177 s). Comparison pages through the real
  route; "first" means no pictures kept yet:

  | Scans | Page | As sent (compressed) | At 512 kbit/s | Picture table, as sent | Built: first / stored |
  |---|---|---|---|---|---|
  | 2 | 0.53 MB | 0.39 MB | 6 s | 0.06 MB | 15.2 s / 13.5 s |
  | 12 | 1.91 MB | 1.38 MB | 22 s | 0.42 MB | 43.3 s / 26.8 s |
  | 33 | 3.90 MB | 2.78 MB | 43 s | 1.38 MB | 66.5 s / 50.9 s |

  - Kept on the server's disk: 0.96–0.99 MB per scan.
  - The charts are most of each page and most of the build time. The
    pictures of all 33 scans add 1.38 MB (about 22 s at 512 kbit/s); with
    the old limit of 12 it was 0.42 MB.
  - The longest build, 67 s, is well inside the 900 s limit of gunicorn and
    the proxy (docs/DEPLOYMENT.md).
  - Build times on this computer were slow all day: 2 scans took 13–15 s,
    where 4.6 s was recorded earlier.

**Step 13 — Export to PDF: printed report**
- **From:** request C.
- **What:**
  - an "Export to PDF" button at the top. It opens the browser's print
    window, where you choose "Save as PDF". No new software on the server;
  - a print layout: A4 portrait with margins;
  - all buttons, sliders and switches hidden;
  - tables, charts and test sections not split across pages;
  - pictures fitted to the page width;
  - the analysis name and date, and page numbers, at the page edges.
- **Check:**
  - Edge makes a real PDF of the PH0730_03 report;
  - PyPDF2 confirms no button or slider text is in it, and gives the page
    count;
  - I look at every page for alignment.
- **Tests:** test_exact_reports (structure), test_security (the button's
  script is allowed). Under 1 min.
- **Done 2026-09-28.**
  - **The button:** "Export to PDF" sits at the top of the report, with the
    hint "opens the print window — choose “Save as PDF” as the printer". A
    small inline script calls `window.print()`. The server allows exactly
    that script, by its hash (`REPORT_SCRIPT_CSP`), on report pages only.
  - **The print rules:**
    - A4 portrait, 15/12 mm margins;
    - "MSF Phantom QA — site / phantom" top left, "Analysis id" top right,
      the acquisition (or upload) date bottom left, "Page N of M" bottom
      right;
    - labels are escaped, so a typed name cannot break the style;
    - buttons and other controls hidden;
    - sections, tables, pictures and the verdict row kept whole; headings
      never end a page; long tables repeat their heading; the verdict
      colours print.
- **Tests (2026-09-28):**
  - test_exact_reports, test_security, test_lighter_downloads,
    test_exposure_index, test_integrity_logging and test_predeploy_fixes:
    115 tests pass in 28 s, 5 of them new;
  - test_authorization: 228 tests pass in 48 s.
- **Checked with a real PDF (2026-09-28).** PH0730_03 fully analysed.
  - Edge printed it through the same path as Save as PDF (`printToPDF`, the
    page's own size, background graphics off as in the default dialog):
    9 pages, 210 × 297 mm.
  - PyPDF2 read every page: each has the phantom and analysis header and
    "Page N of 9"; "Export to PDF" appears on none.
  - With Edge's own "Headers and footers" option on, its date/URL header did
    not appear either; the report's own margins take its place.
  - In the real browser the button's script ran under the page's security
    policy and opened the print window.
  - I looked at all 9 pages, drawn by Edge's own PDF viewer:
    1. heading, validation, identification, verdicts and registration;
    2–6. geometry, line patterns, low contrast, uniformity and wedge, each
       whole, with tables and sharp charts;
    7. the scan with its outlines, exactly aligned in print too;
    8. acquisition data and file integrity;
    9. audit trail.

    Nothing is cut or misaligned. Several pages are half empty because a
    section that does not fit whole starts on a fresh page — the price of
    never splitting a table or a chart.
- **Found, not fixed (older than this round).** The app's own start page
  adds a `<base href>` tag, which the app's security policy (`base-uri
  'none'`) makes the browser refuse. It was logged in the check above. Links
  still work from the page address. It could matter only if the app is
  served under a sub-path and opened without the trailing slash. To be
  looked at separately.

**Step 14 — Export to PDF: comparison report**
- **From:** request C.
- **What:** as step 13, but A4 landscape; table headings repeat on every page;
  a picture is never cut across pages.
- **Check:** a real PDF of a 12-scan comparison, checked the same way.
- **Done 2026-09-28.**
  - **The button:** the comparison has the same Export to PDF button, with
    or without its picture table. The page keeps one inline script,
    `_COMPARISON_SCRIPT` (the button and picture enlarging), admitted by its
    hash as before.
  - **The page:** A4 landscape for the whole comparison, not only the
    picture table.
    - Margins: "MSF Phantom QA — comparison …" top left; the number of
      analyses and the dates spanned top right; how the analyses were chosen
      bottom left; "Page N of M" bottom right.
    - Controls are hidden. Headings never end a page. Long tables repeat
      their heading and wrap instead of running off the page. Colours
      print.
  - **What stays whole:**
    - picture rows, charts and table rows stay whole; the picture table's
      scan headings repeat on every page;
    - sections flow across pages — unlike the single report, where they fit;
    - a chart taller than a page is shrunk to fit it (170 mm, in
      proportion); the single report got the same safeguard (250 mm).
- **Checked with real PDFs (2026-09-28).** From the 33-scan archive (step
  12), printed by Edge as Save as PDF prints:
  - the button opened the print window under the page's security policy,
    with no refusals and no page errors;
  - every page has "Page N of M" and none has the button text.

  | Comparison | First try | After the fixes below |
  |---|---|---|
  | 12 scans | 27 pages | 21 pages, 297 × 210 mm |
  | 2 scans | 27 pages | — |

  - **Found by looking at the pages, and fixed:**
    1. Page 1 held only the title: the first section, kept whole, was
       pushed to page 2 and then split anyway.
    2. A tall heatmap was cut at the page's edge, and a bar chart split
       across two pages.

    After the fixes: page 1 has the title and the list of scans; the
    picture table repeats its scan headings on each page and no row is cut;
    every chart is whole, the tallest shrunk to one page.
  - **Limits of paper:** with 12 scans across a landscape page each small
    picture is about 1.5 cm wide — complete, an overall look. The per-group
    charts' axis labels are crowded with 12 scans; that is the charts'
    drawing, not the print.
- **Tests (2026-09-28):** test_exact_reports, test_comparison_pictures,
  test_store_labels, test_less_waiting, test_review_fixes and test_security:
  173 tests pass. 4 of them are new; one older one was updated because the
  whole comparison is now landscape, not only its picture table.

**Step 15 — Guard tests, documents, and Part A closing check**
- **From:** request A ("never in any part of the app").
- **Guard tests:**
  - one test fails if any lossy picture setting appears anywhere in the code.
    The comparison JPEG in `thumbnails.py` is the one allowed exception;
  - one checks, pixel by pixel, every picture of PH0730_03 and CS000001
    against an independent calculation.
- **Documents:** the user guide, design notes and deployment notes (the new
  delay setting).
- **Closing check:** all Part A test files together, measured today at
  6 min 16 s, plus the new files.
- **You check** in the browser:
  - the viewer;
  - zooming;
  - window/level;
  - both reports;
  - both PDFs.

  Then you commit.
- **Split in three (2026-09-28)** to keep each within 15 minutes: 15a guard
  tests, 15b documents, 15c closing check.
- **15a done 2026-09-28** — `tests/test_picture_guard.py`.
  - **Reading the code:**
    - no JPEG output, palette reduction, palette conversion, lossy WebP,
      lossy chart or encoder settings passed through the chart library
      anywhere in the server code;
    - exactly one allowed exception, the JPEG call in
      `thumbnails.encode_on`;
    - every WebP written with `lossless=True`;
    - in the browser code (app.js, index.html, both reports' scripts): no
      canvas exported as JPEG or WebP and no JPEG asked for;
    - the only 8-bit re-mapping is the labelled window/level preview.
  - **Two real reference scans (PH0730_03, CS000001)** through the
    application, each picture compared pixel by pixel with a calculation
    written out in the test:
    - the viewer picture at 640 and 1024 px, WebP and PNG;
    - every zoom piece at ½ and ¼;
    - the full-size pieces across the middle and along every edge;
    - the disc close-up at three contrast steps;
    - the printed report's picture.

    All exact. 22 tests (with test_imaging) pass in 40 s.
  - **Corrected on the way.** The averaging does not weigh partly covered
    scan pixels by the part covered, as imaging.py said. Each screen pixel is
    the plain mean of the whole scan pixels whose centres fall in it — still
    averaging, every scan pixel counting once. The comment now says so. The
    test repeats the image library's own rule, down to where a pixel exactly
    on the edge between two groups goes (2874 → 640 rows: scan row 718, at
    exactly 718.5, joins the next group). On random data at four sizes it
    agrees to the last bit.
- **15b done 2026-09-28** — the documents, with measured figures only.
  - **USER_GUIDE.md:**
    - the exact viewer picture, full detail on zoom after about a second of
      stillness, the background preload, and 30-day keeping — with the
      measured costs at 512 kbit/s;
    - the labelled window/level preview and its failure message;
    - the close-up as a processed view, with server-made contrast steps;
    - the banner saying "in this browser";
    - Export to PDF on the single report (A4 portrait) and on the
      comparison (landscape);
    - the comparison: drawn on the shared window, the switch gone, the small
      JPEGs for an overall look, every selected scan with measured sizes,
      small pictures on paper with 12 scans.
  - **DESIGN.md:**
    - a new section *Pictures of the scan are exact*: the rule, the one road,
      the viewer, the full-detail pieces, caching, reports, Export to PDF,
      guards;
    - the close-up, the preview, the thumbnail, the comparison pictures and
      their disk cache, the chart encoding and the inline scripts brought up
      to date;
    - the old section "Pictures for looking are lossy" replaced by a short
      account of why that was undone.
  - **DEPLOYMENT.md:** the `PHANTOMQA_DETAIL_DELAY_S` setting, the longest
    measured request (67 s) against the 900 s envelope, the picture caching
    and header rules, and the disk the comparison keeps (about 1 MB an
    analysis). The same disk figure is in **MAINTENANCE.md**.
- **15c done 2026-09-28** — the closing check. It covers every test file Part
  A touched, plus the new ones, run in the foreground in three parts.

  | Part | Files | Result | Time |
  |---|---|---|---|
  | 1 | test_imaging, test_exact_viewer, test_picture_guard, test_exact_reports, test_frontend_integrity, test_upload_progress, test_security, test_duplicate_upload, test_resume_after_reload, test_correctable_picking, test_leaving_an_analysis, test_upload_compression | 391 passed | 4 min 13 s (wall 4 min 30 s) |
  | 2 | test_authorization, test_bandwidth, test_lighter_downloads, test_block_view, test_less_waiting, test_stale_state, test_degraded_inputs, test_orientation_per_phantom | 383 passed | 5 min 26 s (wall 5 min 45 s) |
  | 3 | test_comparison_pictures, test_store_labels, test_review_fixes, test_exposure_index, test_integrity_logging, test_predeploy_fixes, test_schema_upgrade, test_load_and_concurrency | 170 passed | 2 min 44 s (wall 2 min 47 s) |

  **944 passed, none failed**, 12 min 54 s of test time in all. The full
  suite of every file still runs once, before deployment.

## Part B — Field analysis *(after Part A — your decision 15)*

**Step 16 — Record the mode, and where saved points came from**
- **From:** request B, your decisions 5 and 12.
- **What:**
  - each analysis records whether it was *field* or *full*;
  - for a phantom's saved points, the app can now tell:
    - whether they came from a full, step-by-step analysis;
    - whether that analysis passed the image-quality check without an
      administrator override;
    - whether its own discs measured in design order.
- **Files:** `store.py`, `webapp/main.py`.
- **Tests:** test_schema_upgrade, test_store_labels, test_layout_save_choice.
  About 1.5 min.
- **Done 2026-09-28.**
  - **The mode.** Each analysis now has a mode, "full" or "field". Every
    analysis already in the database reads "full", with no data changed:
    Field analysis did not exist before, so that is true of each of them.
    Any other value is refused. Cancelling a re-run puts the mode back with
    everything else.
  - **Where saved points came from.** Saving the points in step C now also
    records:
    - the scan's file name;
    - its image-quality verdict at that moment;
    - whether an administrator override was used;
    - a fingerprint of the exact measuring points.

    A discard that puts back the previous points puts back their record too.
  - **What the app can now tell,** for the page and for the next step:
    - whether the points came from a full analysis;
    - whether that scan passed the image-quality check on its own;
    - whether its discs measured in design order.

    Each "no" comes with one plain line. The mode and the disc order are read
    from that analysis as it is now, but only while its points still match the
    fingerprint. Any change to the points drops the results, so matching
    points mean the results were measured from exactly the saved points.
  - **Points saved before today** read as "not recorded", so Field analysis
    will not be offered on them until a full analysis saves them again (your
    decision 12). The one-line reason says so.
  - **Measured on the real scan PH0730_03** (120 kB of measuring points): the
    check takes 35 ms of the 516 ms it takes to open an analysis on this
    laptop.
  - **Tests.** 15 new tests in test_layout_save_choice, and the upgrade tests
    in test_schema_upgrade now also check the new columns.
    - The three planned files: 86 passed in 3 min 0 s. That is more than the
      1.5 min planned, because each new test uploads a scan and places
      points.
    - The other files that use saved points or re-runs (test_phantom_layout,
      test_round3_journey, test_delete_flow, test_rerun,
      test_degraded_inputs): 109 passed in 4 min 53 s.

**Step 17 — When Field analysis is offered**
- **From:** request B, your decisions 11 and 12.
- **What:** Field analysis is offered only when all of these hold:
  1. the phantom has saved points, first established by a full analysis whose
     discs were in design order (decision 12);
  2. the saved points match the current phantom description;
  3. the file is DICOM;
  4. the image-quality check is "ok";
  5. all 4 ruler lines are found, with error at most 1.0 mm (decision 11);
  6. the analysis is fresh: not worked on, finalised or signed off.

  Otherwise only "Full analysis" is offered, with one plain line saying why.
- **Tests:** new `test_field_analysis.py` (the offer), test_phantom_layout,
  test_quality_gate. About 1 min.
- **Done 2026-09-28.**
  - **Where it is decided.** The server decides whether Field analysis is
    offered and sends the answer with the analysis. Step 18 will run the same
    check again when the button is pressed. The answer carries:
    - offered or not;
    - one line saying why not, the first failing condition;
    - every failing condition;
    - the phantom ID, and when, by whom and from which scan the points were
      saved (for the screen in step 20).
  - **The six conditions, as built:**
    1. **Saved points:** the phantom is named and has saved points, and step
       16 finds them clean. Its own line says what is wrong.
    2. **Phantom description:** the saved points were made for the phantom
       description in use now.
    3. **DICOM:** the file is DICOM.
    4. **Image quality:** the check passed. "Not checked" is not a pass.
    5. **Ruler lines:** all 4 were found, each at most 1.0 mm off; exactly
       1.0 mm is accepted. The stored benchmark has only the count of lines
       found and their combined error (0.11–0.55 mm), not the worst single
       line, so the dry run in step 19 checked the limit on the real scans.
       The worst single line reaches 0.90 mm; see step 19.
    6. **Fresh:** the analysis is at the first two steps, not measured, never
       re-run, not finalised or signed off, and has not been run as Field
       analysis. None of its points have been moved, turned, reset or re-read
       by hand. The app placing the points itself (detection and the saved
       points) still counts as fresh.
  - **Measured:** the check adds 7.7 ms when an analysis is opened, on top of
    step 16's 35 ms. At first it took 24.7 ms: each database lookup opens a
    connection, which costs 8–13 ms here, while the query takes 0.03 ms. The
    two lookups now share one connection.
  - **Tests:**
    - `test_field_analysis.py`: 24 new tests covering every condition, the
      boundary, the automatic placing, the one line, and an upload through
      the whole path. 24 passed in 21 s.
    - test_phantom_layout, test_quality_gate and test_bandwidth (the analysis
      data grew): 74 passed in 3 min 28 s.

**Step 18 — The automatic run**
- **From:** request B.
- **What:**
  - one request to the server places the saved points, measures and stores
    the results, using the same code as the steps, so the numbers are
    identical;
  - **stops:** if a pattern is not found, or the saved points are more than
    8 mm off, the analysis stays at step B with the reason, and the operator
    continues by hand;
  - **flags:** "Review recommended" shows when:
    - the insert looks like the other build ("check the phantom ID");
    - a test could not be analysed;
    - any test did not pass (with its reason);
  - it never changes the saved points;
  - a second press is refused, and so is a locked analysis;
  - an audit entry records every check value.
- **Tests:** test_field_analysis, test_analysis_timeout, test_stale_state,
  test_orientation_per_phantom. About 1.5 min, plus the new tests.
- **Done 2026-09-28.**
  - **The request:** `POST /api/analyses/{id}/field_run`.
    - It checks the offer again (step 17) and refuses with its one line.
    - It then claims the analysis in a single database update, so of two
      presses arriving together only one runs.
    - Then, in the order of the steps: it confirms A, detects the patterns and
      lays the saved points on top, confirms B, C and D, measures, and
      confirms E.
    - The step B button and the step E button now call the same two functions
      the run calls, so the code is not just similar but the same.
    - The distance from the source (SID) is the analysis's own, or 1000 mm,
      which is what the page uses.
    - It never finalises; that stays a button (decision 14).
  - **Stops** (the analysis stays at step B, becomes an ordinary analysis
    again, and the reason is shown):
    - a test whose patterns the automatic detection could not find at all;
    - saved points more than 8 mm off (the existing check the step B button
      already makes);
    - a saved measuring area with nothing on this scan to go with it.

    A run that fails with an error ends the same way, unless results were
    already stored. After a stop, Field analysis is not offered again on that
    analysis, and the line says so: "Field analysis stopped on this analysis:
    … Continue step by step."
  - **Patterns only nudged:** a pattern step B would list as "not refined
    (nominal used)" does not stop the run, because the saved points replace
    its position anyway. The list is kept in the audit entry. The dry run
    (step 19) will show whether that is right.
  - **"Review recommended"** lists, in plain lines:
    - the discs looking like the other build ("Check the phantom ID");
    - every test that could not be analysed, with why;
    - every test that did not pass, with its first reason.
  - **Recorded:**
    - The mode becomes "field". The saved points are never written.
    - Each step confirmed carries the note "confirmed by Field analysis".
    - One audit entry holds every check value: where the points came from and
      whether they were clean, the description versions, the file type, the
      quality verdict, the four ruler-line errors and the limit, the 8 mm
      check with each offset, how many areas were placed or skipped, the
      patterns not refined, the overall result, the review lines and the
      seconds taken.
    - The audit log gets `event=field_run` with the outcome: finished,
      stopped, refused or failed.
  - **Measured on real scans of phantom HmmEi:**
    - PH0727_03 saved the points step by step.
    - PH0730_03 as Field analysis gave exactly the same results and measuring
      points as PH0730_03 done button by button (uploaded again on purpose).
      Overall "pass", nothing to review.
    - The run took 5.8 s on this laptop, inside the test run. The dry run of
      2026-09-27 reported 1.2–3.1 s; step 19 measures every scan and will
      settle it.
  - **Tests:**
    - test_field_analysis: 35 in all, 11 of them new. They cover the
      real-scan run above, refusals (not offered, finalised, signed off, two
      presses at once), each of the three stops and a failed run on the real
      scan with one step made to fail, and the review lines. 35 passed in
      1 min 52 s.
    - test_analysis_timeout, test_stale_state, test_orientation_per_phantom,
      test_authorization (the new route is on its list), test_phantom_layout
      and test_round3_journey: 332 passed in 8 min 48 s.

**Step 19 — Dry run on the real scans**
- **From:** request B, your answer to question 7.
- **What:**
  - the Philips sets HmmEi and A8Tgg get separate phantom IDs;
  - the first scan of each set saves the points through the full workflow;
  - every later scan runs automatically.
- **Measure:**
  - how many finish and how many stop, and why;
  - whether the numbers are identical to the step-by-step workflow;
  - the server seconds per run.
- If this takes more than 15 minutes, it is split by set: Philips 6, light
  blue 12, dark blue 15, field 5.
- **Done 2026-09-28**, in three foreground runs: Philips and field 88 s, light
  blue 89 s, dark blue 201 s.
  - **Sets:** each got its own phantom ID: HmmEi, A8Tgg, LIGHT-BLUE,
    DARK-BLUE. In each, the first scan saved the points step by step:
    PH0727_03, PH0727_05, CS000001 and CS000004. All four were clean on the
    first try (full analysis, quality passed, discs in design order).
  - **The field scans saved nothing** (the standing rule). They were filed
    under their own phantom ID, which has no saved points, and only the offer
    was recorded.
  - **Every later scan was uploaded twice:** once run as Field analysis, once
    done step by step with every point accepted.
  - **Results — 29 automatic runs:**
    - **Finished:** 29 of 29; none stopped.
    - **Identical:** the results and the measuring points were identical to
      the step-by-step analysis of the same scan, 29 of 29.
    - **Philips:** all 4 pass, nothing to review.
    - **Blue prints:** all 25 fail. Each has one review line, always the same
      test: "Line patterns did not pass (fail): G1.2: measured pitch 0.930–
      0.935 mm is +11.6–12.1% from the nominal 0.8333 mm". That is the parked
      line-pair question on the blue prints, and step by step gives the same
      fail.
    - **The other build:** no run showed the discs looking like the other
      build.
    - **Saved points:** unchanged in all four sets.
    - **Server time per run:** 1.3–5.3 s, median 1.9 s. Mid-way through the
      dark-blue set it rose to 4.5–5.3 s and fell back to 1.3–1.5 s at the
      end, so the laptop's load varied; the scans did not.
    - **The 8 mm check:** the saved points sat 0.02–3.39 mm from this scan's
      own detection (median over the patterns that show the orientation).
    - **Patterns not refined** by the detection, recorded but not a stop
      (step 18): line group G2.0 15 times, G1.1 9, G1.2 6, G1.6 2. None of the
      29 runs was changed by this, since the saved points place them.
  - **Ruler lines on the 29 scans: all 4 found every time.** The worst line of
    each scan:
    - 0.17–0.40 mm on 27 scans;
    - 0.84 mm (PH0730_07) and 0.90 mm (PH0730_03), both the top line of
      phantom HmmEi.

    All are under the 1.0 mm limit, but those two have only 0.10–0.16 mm to
    spare. The plan's "0.11–0.55 mm" was the root-mean-square of the four
    lines (0.11–0.51 mm here), not the worst line. It is corrected above and
    in the code. **Open for you:** keep the limit on the worst single line,
    or apply it to the four lines together.
  - **Field scans:** none offered. Every one names "no saved points yet".
    Beyond that:
    - F001 (phantom cut off at the edge) also fails the quality check, with
      all 4 lines found and the worst at 0.80 mm;
    - F003 and F003B (saturated) fail the quality check and have 0 of 4 lines;
    - F002 and F004 (usable) pass the quality check with the worst line at
      0.57 and 0.34 mm, so with saved points they would be offered.
  - **Scripts and raw results** are in the session scratchpad
    (`step19/dryrun.py`, one JSON per set), not in the repository. The
    temporary copies of the scans were deleted.

**Step 20 — The screens**
- **From:** request B, your decisions 13 and 14.
- **The choice after registration:**
  - "Field analysis — automatic" is highlighted, with the phantom ID in large
    letters and "points saved on [date] by [who], from scan [file]";
  - "Full analysis — step by step" sits next to it.
- **The result screen:**
  - the verdict, and one line per test with the reason for anything that did
    not pass;
  - the "Review recommended" box;
  - the image with the measuring points. It uses the viewer, with no extra
    download and no preload;
  - buttons: **Open full report · Review step by step · Finalise · New
    analysis**. There is no "mark as reference".
- A finished run reopens on this screen.
- **Tests:** test_frontend_integrity, test_field_analysis,
  test_resume_after_reload. About 30 s.
- **You can check** it in the browser.
- **Done 2026-09-28.**
  - **Step A** shows the choice when Field analysis is offered:
    - "Field analysis — automatic", outlined, with the phantom ID in large
      letters, "Points saved on … by …, from scan …", and a **Run Field
      analysis** button;
    - "Full analysis — step by step" beside it, with the usual **Confirm
      registration** button.

    When it is not offered, step A is as before, with one line above the
    details: "Field analysis is not available: …". After manual corners, the
    answer brings the new offer, since the ruler lines and the verdict
    change.
  - **The run:**
    - When it finishes, the page goes to the result screen.
    - When it stops, the page goes to step B, where a box says "Field analysis
      stopped here." with the reason. The box also shows when the analysis is
      reopened later.
    - On a refusal or a lost connection, the page re-reads the analysis and
      shows whatever the server now holds, because the run may have finished
      anyway.
  - **The result screen** (step F of a Field analysis):
    - the phantom ID large, and where the points came from, "not reviewed on
      screen";
    - the overall chip and one line per test, with the first reason for
      anything that did not pass;
    - the "Review recommended" box, or "Nothing to review";
    - the four buttons: **Open full report · Review step by step · Finalise ·
      New analysis**. There is no reference-scan button.

    The measuring points are drawn in the viewer beside it. A finished run
    reopens on this screen, after a reload too.
  - **Nothing extra downloaded:**
    - While Field analysis is on offer or done, the background download of
      zoom detail waits. It starts when the operator picks the full analysis
      or "Review step by step".
    - The run's answer carries the measuring points and the result lines, so
      the result screen needs no second request.
    - Finalise uses the same code as step F's button.
  - **Looked at in Edge** (1366×768, a real run of PH0730_03 after PH0727_03
    saved the points):
    - At the choice, the page had fetched the picture (123 kB) and the
      analysis (3 kB), with no zoom detail.
    - After pressing the button, the one request was the run (108 kB), and
      the result was on screen 2.8 s later. That is the same data the step
      buttons fetch in two requests.
    - Reopened: the result screen, with no zoom detail fetched.
    - Not offered (CS000001 under a new phantom ID): the one line, and the
      preload started.
    - No console errors. Screenshots are in the session scratchpad.
  - **Tests:**
    - 8 new tests in test_field_analysis (43 in all), and more checks in the
      real-scan run and the stops. They cover what the server sends for the
      screens (one line per test, the Field result only on Field analyses,
      the reason for a stop, the offer after registering again), and the
      page's source (the choice, the run's two outcomes, the result screen
      with its four buttons and no reference, nothing extra downloaded).
    - test_resume_after_reload now looks for the one shared finalise
      function behind both Finalise buttons.
    - test_field_analysis, test_frontend_integrity and
      test_resume_after_reload: 96 passed in 1 min 23 s, then 11 in 18 s
      after that change.

**Step 21 — The mode everywhere, and the rules**
- **From:** your decisions 4, 5 and 14.
- **What:**
  - the report header says *"Field analysis — automatic, measuring points not
    reviewed on screen"*. The same appears as a tag in History and in the
    sign-off panel, and as a column in the CSV;
  - a Field result cannot become the reference scan (decision 4);
  - an administrator may sign it off, with "automatic" printed (decision 5);
  - finalising is by button (decision 14);
  - "Review step by step" makes it a full analysis once recomputed;
  - discarding or re-running a Field analysis leaves the saved points alone.
- **Tests:** test_baselines, test_signed_off_is_protected,
  test_verdict_wording, test_discard_flow, test_rerun. About 3 min.
- **Done 2026-09-28.**
  - **One wording everywhere:** "Field analysis — automatic, measuring points
    not reviewed on screen" (`store.FIELD_MODE_TEXT`, the same words in the
    page).
    - **Report:** a blue line at the top, before the ruling, saying where the
      points came from (phantom, date, who, scan). "Field analysis —
      automatic" also appears in the running header of every printed page, so
      a page taken from the stack still says it.
    - **History:** an "automatic" tag beside the status. There is no
      reference star on those rows, only "—" with the reason on hover.
    - **Sign-off panel:** the line above the choices, "The ruling is printed
      as automatic."
    - **CSV:** an `analysis_mode` column (`field` or `full`), last in the long
      export and as the last row in the wide one, so spreadsheets built on the
      old layout keep every column where it was. The two exposure tests that
      checked "exposure columns last" now check they come just before it.
  - **Reference scan (decision 4):** refused for a Field analysis, both from
    the reference button and from finalising with "make reference". There is
    no override. The message gives the way round: "Review it step by step
    first — once it is measured again it is a full analysis and can be the
    reference." Finalising alone is allowed.
  - **Sign-off (decision 5):** allowed. The report prints the ruling as
    "VALIDATED (AUTOMATIC)", with "signed off on a Field analysis, whose
    measuring points were not reviewed on screen". The record's trail and the
    audit log mark it `automatic`.
  - **Review step by step:** the analysis becomes a full one when it is
    measured again after a person has confirmed its measuring points (step C)
    since the automatic run, and the trail says "field analysis reviewed step
    by step". Measured again without that — a re-run started from the results
    — it stays automatic, because nobody looked at the points. *Found while
    writing the test:* the first version turned any re-measurement into a
    full analysis.
  - **Discard and re-run** leave the phantom's saved points exactly as they
    were. Tested on the stored row, before and after.
  - **Looked at in Edge** after a real Field run (PH0727_03, then PH0730_03):
    the report's top line, the History tag with no star, the sign-off panel's
    line, the refusal of the reference. No console errors.
  - **Found, not fixed** (older than this round): for an analysis nobody has
    ruled on, the sign-off panel pre-selects "Withdraw ruling" rather than
    "Validated".
  - **Tests:**
    - 8 new tests in test_field_analysis (51 in all): the report on every
      page, the
      ruling printed as automatic, a sign-off accepted and recorded, History
      and both CSVs, the reference refused, discard, the page's tags, and on
      real scans the re-run and the review making it full.
    - test_field_analysis, test_exposure_index and test_frontend_integrity:
      128 run, 3 failed on test expectations (fixed above), then passed.
    - test_baselines, test_signed_off_is_protected, test_verdict_wording,
      test_discard_flow, test_rerun, test_exact_reports and
      test_store_labels: 182 passed in 4 min 3 s.
    - After every fix, test_field_analysis and test_frontend_integrity: 93
      passed in 1 min 8 s.

**Step 22 — Documents and Part B closing check**
- **What:** the user guide section for Field analysis, including asking that
  the first, point-saving analysis of each phantom is done carefully on a
  good exposure.
- **Closing check:** all Part B test files, measured today at 7 min 57 s, plus
  the new files.
- **You check** in the browser, then commit.
- **Done 2026-09-28.**
  - **USER_GUIDE.md:** a new section, *Field analysis — automatic*, with a
    pointer from step A. It covers:
    - setting a phantom up carefully once, on a good exposure, in four steps;
    - the conditions, with the line shown for each;
    - checking the phantom ID;
    - the result screen, and the blue prints' line-pair note (decision 6);
    - when it stops;
    - what is different about a Field analysis;
    - the measured figures: identical on 29 scans, about 2 s on the server,
      about 110 kB.
  - **DESIGN.md:** *Field analysis: automatic only where looking could not
    change anything*, covering the shared code, where points came from, the
    offer, the stops, the mode and the traffic.
  - **MAINTENANCE.md and DEPLOYMENT.md:** the three new columns, what an
    upgrade does (adds them and reads nothing), and that each phantom needs one
    full analysis with its points saved before Field analysis is offered.
  - **Closing check,** in four foreground parts, since the laptop ran slower
    than in the morning:

    | Part | Files | Result | Time |
    |---|---|---|---|
    | 1 | test_field_analysis, test_layout_save_choice, test_schema_upgrade, test_store_labels, test_exposure_index, test_resume_after_reload, test_frontend_integrity | 225 passed | 8 min 20 s |
    | 2 | test_rerun, test_discard_flow, test_unusable_exposures, test_verdict_wording, test_signed_off_is_protected, test_baselines, test_insert_orientation | 193 passed | 6 min 4 s |
    | 3 | test_orientation_per_phantom, test_round3_journey, test_phantom_layout, test_quality_gate, test_stale_state, test_analysis_timeout | 123 passed | 5 min 24 s |
    | 4 | test_authorization, test_exact_reports, test_bandwidth | 265 passed | 3 min 38 s |

    **806 passed, none failed**, 23 min 26 s of test time in all.

## Before deployment — the full suite once, in 3 parts

The last full run was 24 min 23 s, too long for one step, so it runs in 3 parts
of at most 15 minutes each:

1. the Part A files, about 6.5 min;
2. the Part B files, about 8 min;
3. everything else, including the reference-scan benchmark. That is about
   10 min: the 24 min 23 s total minus the two parts above.

## Not in this round

- **Line pairs on the blue prints (parked).** They keep showing fail or warn in
  both modes. Your decision 6: the physical check stays first on the list when
  a blue phantom is at hand.
- **A wrong phantom ID between two similar builds is not detected.** The dry
  run showed that it caused only false alarms, never a result that looked
  better than it should (apart from blue-print line pairs moving from fail to
  warn). The phantom ID is shown in large letters before the run for this
  reason.

---

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
| 4c · Compressing uploads in the browser | Done (approved 2026-09-22). The browser packs the file losslessly, and only when it clearly helps: the first 1 MB is tried first, and the file is sent as it is unless that and then the whole file come out at 90 % or less. Measured on all 38 real scans, every one unpacked byte-identical: Fuji 7.5 → 2.9 MB (1.1 MB over-exposed), about 2 minutes → 45 s at 512 kbit/s; Carestream 15.1 → 7.1 MB on average, 236 → 111 s; Philips already compressed inside the file, so sent as it is — the 1 MB test decides that in a fraction of a second. In a real browser the Fuji packs in 0.16 s, the Carestream in 0.5 s. The server unpacks and checks gzip's CRC32, the size and the SHA-256 taken in the browser before storing the original; any disagreement stores nothing and says "The file was damaged on the way — nothing was stored. Please send it again." Without https or with an older browser the upload is exactly as before. |
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
- **4c · Compress the scan in the browser before uploading** — 7.5 → 2.9 MB measured on a usable Fuji scan, about 2 minutes → 45 s at 512 kbit/s. Approved 2026-09-22 and done; see the status table above.

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
- **Separate users / per-user history (2026-09-23):** kept as it is — one shared login and one archive every signed-in browser sees, including the "Unfinished analyses" list, which is deliberately everyone's so a colleague can continue a scan someone else uploaded. Nothing identifies a computer; the "unfinished on this computer" banner is only a note in that browser's own storage. Accounts would buy identity (named audit entries, real approver names, roles), not separation, and splitting history per person would break trends per phantom, baselines, the duplicate check and sign-off. To be reconsidered on feedback from the field users; if separation is ever wanted the natural line is the site label, which every analysis already carries.

## Decisions taken

1. **The plan and its order:** approved 2026-09-21, with parallel agents allowed.
2. **Step 1, existing records:** the new wording applies to new and re-run analyses only; nothing already finalised or signed changes wording.
3. **Step 4c, compressing uploads in the browser:** approved 2026-09-22 after measuring it on the real scans and confirming it is lossless; built so that an unknown detector is never worse off than before.

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
