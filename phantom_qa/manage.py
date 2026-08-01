"""Admin helper for secrets, integrity checks and maintenance.

    python -m phantom_qa.manage gen-secret
    python -m phantom_qa.manage set-password         # everyday login
    python -m phantom_qa.manage set-admin-password   # required to delete
    python -m phantom_qa.manage check
    python -m phantom_qa.manage verify [<id> | --all]
    python -m phantom_qa.manage backup [<dest.sqlite3>]
    python -m phantom_qa.manage checkpoint

    python -m phantom_qa.manage outdated
    python -m phantom_qa.manage reanalyze [<id>...] [--dry-run] [--full]
                                          [--all] [--include-validated]

`reanalyze` recomputes stored analyses from the source files the app already
keeps — you never re-upload anything. Default mode reuses the geometry the user
confirmed (so manual ROI adjustments survive) and is right after an ALGORITHM
change; `--full` re-detects everything and is right after a PHANTOM DEFINITION
change. Signed-off analyses are skipped unless --include-validated.
"""

from __future__ import annotations

import getpass
import os
import secrets
import sys

from .security import hash_password

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_new_password(what: str) -> str | None:
    pw = getpass.getpass(f"New {what}: ")
    if len(pw) < 12:
        print("Refusing: use at least 12 characters.", file=sys.stderr)
        return None
    if pw != getpass.getpass(f"Repeat {what}: "):
        print("Passwords do not match.", file=sys.stderr)
        return None
    return pw


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "help"

    if cmd == "gen-secret":
        print(f"PHANTOMQA_SECRET_KEY={secrets.token_urlsafe(48)}")
        return 0

    if cmd == "set-password":
        pw = _read_new_password("password")
        if pw is None:
            return 1
        print("\nAdd this line to your .env file:\n")
        print(f"PHANTOMQA_PASSWORD_HASH={hash_password(pw)}")
        return 0

    if cmd == "set-admin-password":
        print("This password gates the administrator decisions: DELETING an "
              "analysis and VALIDATING one (validated / conditionally validated "
              "/ not validated). Give it only to people entitled to make those "
              "calls — it should not be the everyday login password.\n")
        pw = _read_new_password("administrator password")
        if pw is None:
            return 1
        print("\nAdd this line to your .env file:\n")
        print(f"PHANTOMQA_ADMIN_PASSWORD_HASH={hash_password(pw)}")
        return 0

    if cmd == "outdated":
        from .phantom_def import load_default
        from .reanalyze import find_outdated
        from .store import Store
        rows = find_outdated(Store(ROOT), load_default())
        if not rows:
            print("Every stored analysis was produced by the current algorithm "
                  "and phantom definition.")
            return 0
        print(f"{len(rows)} analysis(es) predate the current version:\n")
        for r in rows:
            sign = f"  [signed off: {r['validation_status']}]" \
                if r["validation_status"] else ""
            print(f"  {r['id']}  {r['site']}/{r['phantom']}  "
                  f"{r['acquired_at'][:16]}{sign}")
            print(f"      {r['reason']}")
        print("\nTheir stored numbers are still valid for the version that "
              "produced them.\nTo bring them up to date without re-uploading:")
        print("  python -m phantom_qa.manage reanalyze --dry-run")
        return 0

    if cmd == "reanalyze":
        from .phantom_def import load_default
        from .reanalyze import reanalyze_all, reanalyze_one
        from .store import Store
        args = argv[1:]
        dry = "--dry-run" in args
        mode = "full" if "--full" in args else "results"
        include_validated = "--include-validated" in args
        only_outdated = "--all" not in args
        ids = [a for a in args if not a.startswith("-")]

        store, pdef = Store(ROOT), load_default()
        if ids:
            results = [reanalyze_one(store, pdef, a, mode=mode, dry_run=dry)
                       for a in ids]
        else:
            results = reanalyze_all(store, pdef, mode=mode, dry_run=dry,
                                    only_outdated=only_outdated,
                                    include_validated=include_validated)
        if not results:
            print("Nothing to do. (Use --all to include up-to-date analyses.)")
            return 0

        print(f"mode: {mode}"
              f"{'  (DRY RUN — nothing written)' if dry else ''}\n")
        changed = failed = skipped = 0
        for r in results:
            st = r["status"]
            if st in ("updated", "would_change"):
                changed += 1
                head = f"  {r['id']}  {r.get('site','')}/{r.get('phantom','')}"
                print(f"{head}  {r['old_overall']} -> {r['new_overall']}")
                for line in r.get("changes", []):
                    print(f"        {line}")
                if not r.get("changes"):
                    print("        (no metric moved by more than 0.5 %)")
            elif st.startswith("skipped"):
                skipped += 1
                print(f"  {r['id']}  SKIPPED — {r.get('message','')}")
            else:
                failed += 1
                print(f"  {r['id']}  {st.upper()} — {r.get('message','')}")
        verb = "would be updated" if dry else "updated"
        print(f"\n{changed} {verb}, {skipped} skipped, {failed} failed.")
        if dry and changed:
            print("Re-run without --dry-run to apply.")
        return 1 if failed else 0

    if cmd == "backup":
        from .store import Store
        store = Store(ROOT)
        dest = argv[1] if len(argv) > 1 else os.path.join(
            ROOT, "data", "backup", "phantom_qa-backup.sqlite3")
        store.backup_to(dest)
        size = os.path.getsize(dest)
        print(f"Database copied to {dest} ({size/1024:.0f} KB).")
        print("This copy is complete on its own — it does not need the -wal or "
              "-shm files.")
        print("Remember to copy data/uploads/ as well; the database alone does "
              "not contain the scans.")
        return 0

    if cmd == "checkpoint":
        from .store import Store
        Store(ROOT).checkpoint()
        print("Write-ahead log folded into the main database file.")
        return 0

    if cmd == "verify":
        from .store import Store
        store = Store(ROOT)
        target = argv[1] if len(argv) > 1 else "--all"
        results = (store.verify_all() if target == "--all"
                   else [store.verify_integrity(target)])
        if not results:
            print("No analyses stored.")
            return 0
        bad = 0
        for r in results:
            mark = {"ok": "OK      ", "mismatch": "MISMATCH",
                    "missing_file": "MISSING ",
                    "not_found": "NO SUCH "}.get(r["status"], "?       ")
            print(f"{mark} {r['id']}  {r.get('source_name', '')}")
            if r["status"] != "ok":
                bad += 1
                print(f"         stored   {r.get('stored_sha256')}")
                print(f"         computed {r.get('computed_sha256')}")
                print(f"         {r.get('message', '')}")
        print(f"\n{len(results) - bad}/{len(results)} verified.")
        return 1 if bad else 0

    if cmd == "check":
        from .config import get_config
        try:
            cfg = get_config()
        except SystemExit as e:
            print(f"CONFIG ERROR: {e}", file=sys.stderr)
            return 1
        print(cfg.summary())
        problems = []
        if cfg.is_production:
            if not cfg.auth_enabled:
                problems.append("auth disabled in production")
            if not cfg.https_only:
                problems.append("HTTPS-only cookies disabled in production")
            if not cfg.allowed_hosts:
                problems.append("PHANTOMQA_ALLOWED_HOSTS is empty "
                                "(Host header not restricted)")
            if cfg.password_plain:
                problems.append("plain-text password set")
            if len(cfg.secret_key) < 32:
                problems.append("PHANTOMQA_SECRET_KEY is shorter than 32 "
                                "characters")
            if not cfg.behind_proxy:
                problems.append("PHANTOMQA_BEHIND_PROXY is false — client IPs "
                                "in the logs and the login throttle will all "
                                "be the proxy's address")
        if cfg.admin_password_hash and cfg.admin_password_hash == cfg.password_hash:
            problems.append("the admin password is the same as the everyday "
                            "login password — deletion is then no protection")
        for p in problems:
            print(f"  WARNING: {p}")
        if not cfg.deletion_enabled:
            print("  NOTE: no PHANTOMQA_ADMIN_PASSWORD_HASH is set, so deletion "
                  "AND validation sign-off are both disabled.")
        return 1 if problems else 0

    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
