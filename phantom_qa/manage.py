"""Admin helper for secrets, integrity checks and maintenance.

    python -m phantom_qa.manage gen-secret
    python -m phantom_qa.manage set-password         # everyday login
    python -m phantom_qa.manage set-admin-password   # required to delete
    python -m phantom_qa.manage check
    python -m phantom_qa.manage verify [<id> | --all]
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
