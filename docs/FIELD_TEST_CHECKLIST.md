# Second field test — checklist

Tick what you tried. Write down anything that looks wrong, even if you are not
sure. Details of every item are in the [user guide](USER_GUIDE.md).

**Before you start.** The scans you take during this test are used only to see
how the software behaves. They are **never** used to set the phantom definition
or baselines (the reference values the software is built and checked against).
Do not take extra exposures just to test the software — use the scans you would
take anyway. Type the phantom name the same way every time.

## Try these

**Upload**
- [ ] Choose a file: the box shows its size and modified time. Can you tell two `003_0000.dcm` apart?
- [ ] Upload: the bar shows how much is sent and the time left. Press **Cancel** once — the file should stay selected.
- [ ] Choose a file that is already uploaded: **This scan is already here** should appear *before* anything is sent, showing both files. Try **Continue this analysis**.

**Step A**
- [ ] **Manual corners…**: left click places a corner; middle-drag, right-drag or Space+drag moves the view; drag a corner to correct it; **Undo last point**; nothing happens until **Apply corners**.
- [ ] If an exposure comes out badly anyway: is there a red panel in step A, and an **image** badge in History? In step C, above **Measuring points confirmed ✓**, it should say the measuring points *will not be used for future scans* and list the checks that failed. Do not press **Use anyway (administrator)…** just to try it — only an administrator should, and only for an image that really is the best available.

**Step C — keeping the measuring points**
- [ ] Above **Measuring points confirmed ✓** there is a box: *Use these measuring points for future scans of phantom …*. For a phantom never analysed before it should start **ticked**; for a phantom that already has stored points (from an earlier analysis) it should start **unticked**. Does the note under the box say when, and by whom, the current points were stored?
- [ ] Leave it unticked and confirm: the message should say the points were confirmed *for this analysis only*. The next scan of that phantom should still start from the earlier points.
- [ ] Where the box starts ticked, untick it and then move a measuring point: the box should stay unticked.

**Step C — low contrast**
- [ ] The close-up of the block: move the **contrast** slider. Are the discs easier to see than before? Do the ring colours (green / amber / red) match what you see?
- [ ] Under the close-up, note the **Low-contrast insert** setting and what is in brackets. A phantom seen before should say *saved for this phantom*.
- [ ] Did you ever see *Check that the phantom ID is right*? Note the phantom name and the analysis id.
- [ ] **Turn 180°** moves the rings to the opposite discs. Press **Refresh** on the close-up: the insert setting should stay the same (unless it says it could not be read from the contrast).
- [ ] **Click 4 block corners…**: the same controls as manual corners in step A. Drag a corner to correct it, try **Undo last point**; the block should not move until **Apply corners**.
- [ ] **↶ Undo** / **↷ Redo** after a change.

**Step C — field edge**
- [ ] Press a side under **Manual field-edge placement** and left-click the field edge: a blue point appears. Click somewhere else or drag it to move it. Nothing should change until **Apply edge**; **Cancel** should leave the edge as it was.

**Step E and the identity bar**
- [ ] A good scan should read **pass** with the note *X-ray field alignment not checked (no field edge found in the image)*.
- [ ] Any **not measured**, **error** or **warn**: note the test and the reason shown.
- [ ] The identity bar and History show the **exposure index** and **deviation index**. Do they match the X-ray unit's console, if you can see it?

**Step F and History**
- [ ] **Discard…** an analysis you do not want (a wrong label, a test upload): no password; it disappears from History.
- [ ] **Re-run analysis…** → **Recompute the numbers only** on a measured analysis that is not finalised; it reopens at step C. Try **Undo re-run** before measuring again.
- [ ] In History, press **re-run…** on the row of a measured analysis, without opening it. The same panel should appear, naming that analysis, and the analysis should then open at step C (or step A for **Start again from registration**). Rows with nothing measured should have no **re-run…**.
- [ ] **Finalize** a scan you want to keep, then open it again from History: **Discard…** should be gone, and **Re-run analysis…** should ask for the administrator password and a reason. **re-run…** on its History row should ask for the same. (A finalised analysis can only be removed with the administrator password.)
- [ ] **Comparison report** of two or more scans of one phantom: the picture table; tick *window each picture on its own*; click a picture to enlarge; save the page and open the saved copy.
- [ ] Note how long the report, the comparison and opening an analysis take on your connection.

## When something goes wrong, write down

1. A **screenshot** of the whole screen, including the message at the top right.
2. The **analysis id** — in step F (*Analysis … stored*), in the title of the **Edit** box, or at the top of the printed report.
3. The **time** it happened (local time), and what you pressed just before.
4. The file name, the phantom name, and the connection (slow? dropped?).

Then carry on with **Discard** or **Re-run** — do not delete and re-upload.

## Only when a blue phantom is at hand

- [ ] Put a blue phantom next to the original phantom **MSF^PHANTOM001**, the same way up: same side facing you, turned so the step wedge and the low-contrast block are in the same places on both.
- [ ] On each, look at the diagonal line-pair strip. At which end are the **finest lines** (the ones closest together) — the end nearer the low-contrast block, or the other end?
- [ ] Write down both phantom IDs and the answer for each, and take one photo of the two side by side. If the lines cannot be seen by eye, write that down — do not guess. No exposure is needed for this.
