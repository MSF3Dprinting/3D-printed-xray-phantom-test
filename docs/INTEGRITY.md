# Source file integrity — the SHA-256

Every analysis stores a **SHA-256 hash** of the exact file it was computed from,
and keeps a copy of that file under `data/uploads/<analysis-id>.bin`.

## What it is for

The hash ties a set of results to its input. A QA report is only meaningful if
you can still say *which* image produced it. The hash lets you demonstrate that
later, and detects the three ways that link silently breaks:

| Problem | What you would otherwise see | What the hash shows |
|---|---|---|
| Silent corruption (bad disk, bad copy, truncated transfer) | Nothing — the report still opens | **mismatch** |
| A backup restored from the wrong date | Nothing — the file is there and plausible | **mismatch** |
| The wrong scan attached to a phantom/date | Nothing | **mismatch** |
| The stored file deleted or moved | Nothing | **file missing** |

It is a *detection* mechanism, not access control. Anyone who can rewrite the
stored file could also rewrite the database row that holds the expected hash.
Its value is against accident and drift, which is what actually happens. If you
need tamper-evidence against a motivated insider, export the hash to somewhere
the application cannot write — a signed e-mail, a ticket, a printed report in a
QA binder — at the time of analysis. The value in the printed report serves
exactly that purpose.

## Where to find it

- **In the report** — the *Source file integrity* section shows the recorded
  hash and, because the report re-checks at the moment it is generated, whether
  the stored file still matches.
- **In the app** — Stage F shows the hash with a **Verify source file** button;
  the History table has a **verify** link on every row.
- **In the exports** — the JSON export carries `sha256`.

## How to verify it yourself

The stored value is a plain SHA-256 of the file's bytes, so any standard tool
reproduces it. Hash *your* copy of the original DICOM and compare — the two
strings must match character for character:

```powershell
# Windows PowerShell
Get-FileHash -Algorithm SHA256 "path\to\scan.dcm"
```

```bash
# Linux / macOS
sha256sum path/to/scan.dcm
```

To check what the server itself is holding, without trusting the web UI:

```bash
sha256sum data/uploads/<analysis-id>.bin
```

## Checking from the application

```bash
python -m phantom_qa.manage verify              # every stored analysis
python -m phantom_qa.manage verify <analysis-id>
```

Output is one line per analysis; the command exits non-zero if anything failed,
so it can be run from cron or a scheduled task:

```
OK       c81a123830db  10000003
MISMATCH d4df2f8f0132  10000005
         stored   09a846d530ee78f3...
         computed 1b77c0e4a9f1220c...
         THE STORED FILE NO LONGER MATCHES the hash recorded at analysis time
2/3 verified.
```

Every check — from the CLI, the UI, or a report being generated — is written to
`logs/audit.log` as an `event=verify` line, so a history of checks exists.

## What to do about a mismatch

1. **Do not delete anything.** The mismatch is evidence.
2. Re-hash your own copy of the original scan (`Get-FileHash` / `sha256sum`). If
   *your* copy matches the recorded value, the server's copy is the damaged one:
   restore it from backup and re-verify.
3. If neither matches, the original is gone. Treat the results as
   **unverifiable**: keep the report for reference, note the mismatch, and
   re-acquire if the measurement matters.
4. Check `logs/audit.log` around the relevant dates for `event=verify`,
   `event=upload` and `event=delete` lines to see when the file was last known
   good and who touched the record.

A mismatch never happens through normal use. It always means something went
wrong outside the application.
