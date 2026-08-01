# Your data and code updates

**Short answer: updating the code never costs you your analyses, and you never
have to re-upload a scan.**

## What is stored, and where

| Path | Contents | Survives a code update? |
|---|---|---|
| `data/phantom_qa.sqlite3` | Every analysis: labels, confirmed geometry, results, validation, baseline flag, audit trail | **yes** |
| `data/uploads/<id>.bin` | The original DICOM/image of every analysis | **yes** |
| `data/phantom_definitions/` | Calibrated phantom geometry (this one *is* in git — it is source, not data) | updated with the code |
| `logs/` | Application, error and audit logs | **yes** |

`data/` and `logs/` are **git-ignored**, so `git pull`, `git checkout` and
branch switches cannot touch them. The application itself never deletes an
analysis on its own — the only path that removes one is the admin-gated
**delete**, which needs the administrator password and the analysis id typed
back.

The schema is migrated **in place** on startup: new columns are added, existing
rows are untouched. `tests/test_persistence_and_reanalysis.py` proves a full
record — labels, validation, approver, comment, baseline, results, geometry,
audit trail and SHA-256 — survives a migration and a restart.

> **If you lost data during development, it was because the development
> smoke-tests explicitly cleared `data/` afterwards.** That cleanup is not part
> of the application and does not happen in production.

## When results become *stale* (not lost)

Stored results are the numbers produced by the algorithm and phantom definition
**of the day they were computed**. That is deliberate — a QA record should not
silently change under you, especially one an administrator has signed.

So after an update the old results are still there and still valid *for the
version that produced them*; they are simply not what today's code would
produce. Every analysis records `algo_version` and `pdef_version` so this is
never ambiguous.

To see which ones predate the current version:

```bash
python -m phantom_qa.manage outdated
```

## Bringing them up to date — without re-uploading

Because the original file is kept, results can be recomputed from disk:

```bash
python -m phantom_qa.manage reanalyze --dry-run   # preview, writes nothing
python -m phantom_qa.manage reanalyze             # apply
```

Two modes, matching the two reasons results go stale:

| Mode | Command | What it does | Use after |
|---|---|---|---|
| **results** (default) | `reanalyze` | Recomputes the numbers from the geometry the user already confirmed. **Manual ROI adjustments are preserved.** | an **algorithm** change |
| **full** | `reanalyze --full` | Re-detects the phantom and every ROI, then computes. Manual adjustments are discarded. | a **phantom definition** change |

Useful flags:

- `--dry-run` — show what would change, including the metrics that move most, and write nothing.
- `--include-validated` — also recompute analyses an administrator signed off. **Skipped by default**, because re-running changes numbers somebody put their name to.
- `--all` — include analyses that are already on the current version.
- `<id> [<id>...]` — just these analyses.

Always preserved: site, phantom, operator, notes, validation state and approver,
comment, baseline flag, the audit trail, and the stored source file. The
re-analysis itself is added to the audit trail and to `logs/audit.log`.

Re-analysis **refuses** to run if the stored file no longer matches its recorded
SHA-256 — new numbers must not be computed from a file that is not the one the
original results came from. It also reports, rather than crashes, when a stored
file is missing or undecodable, so one bad record cannot abort a batch run.

### Typical example

```console
$ python -m phantom_qa.manage outdated
1 analysis(es) predate the current version:

  cfb4ef9eea00  Goma Hospital/MSF-01  2026-07-30 14:36  [signed off: validated]
      algorithm 0.9.0 -> 1.0.0

$ python -m phantom_qa.manage reanalyze --dry-run --include-validated
mode: results  (DRY RUN — nothing written)

  cfb4ef9eea00  Goma Hospital/MSF-01  pass -> pass
        linepairs·G2.0·sd 62.41->63.02 (+1.0%)

1 would be updated, 0 skipped, 0 failed.
Re-run without --dry-run to apply.
```

## Do you have to re-analyse after every update?

No. It is optional, and often unnecessary:

- **Update touches the web app, reports, exports, security** — nothing to do.
  Reports are rendered from stored results at the moment you open them, so
  presentation improvements appear immediately.
- **Update changes a measurement algorithm** — old and new results are not
  strictly comparable. Re-analyse the series you intend to trend together, or
  accept the mixed history knowing `algo_version` records which is which.
- **Update changes the phantom definition** — re-analyse with `--full` if you
  want the improved detection applied to old scans.

For trending, the honest rule is: **compare like with like.** A series
recomputed on one version is comparable throughout; a series spanning an
algorithm change is not, which is exactly why the version is stored per
analysis.

## Backups

See [DEPLOYMENT.md](DEPLOYMENT.md#what-lives-in-data-and-what-to-back-up).
In short: `python -m phantom_qa.manage backup` for the database plus a copy of
`data/uploads/` — the database alone does not contain the scans.
