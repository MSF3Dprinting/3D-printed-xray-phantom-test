"""Admin helper: generate secrets and password hashes for .env.

    python -m phantom_qa.manage gen-secret
    python -m phantom_qa.manage set-password
    python -m phantom_qa.manage check
"""

from __future__ import annotations

import getpass
import secrets
import sys

from .security import hash_password


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "help"

    if cmd == "gen-secret":
        print(f"PHANTOMQA_SECRET_KEY={secrets.token_urlsafe(48)}")
        return 0

    if cmd == "set-password":
        pw = getpass.getpass("New password: ")
        if len(pw) < 12:
            print("Refusing: use at least 12 characters.", file=sys.stderr)
            return 1
        again = getpass.getpass("Repeat password: ")
        if pw != again:
            print("Passwords do not match.", file=sys.stderr)
            return 1
        print("\nAdd this line to your .env file:\n")
        print(f"PHANTOMQA_PASSWORD_HASH={hash_password(pw)}")
        return 0

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
        for p in problems:
            print(f"  WARNING: {p}")
        return 1 if problems else 0

    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
