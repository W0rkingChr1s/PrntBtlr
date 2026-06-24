"""Authentication helpers for the (opt-in) login.

Passwords are hashed with PBKDF2-HMAC-SHA256 — part of the standard library, so
no extra dependency. The stored format is::

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

A small CLI lets operators generate a hash or a session secret without writing
any Python::

    python -m app.auth hash          # prompts for a password, prints the hash
    python -m app.auth secret        # prints a random session secret
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from .config import settings

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 240_000


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    """Return a self-describing PBKDF2 hash string for *password*."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{_ALGO}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of *password* against a stored PBKDF2 hash."""
    try:
        algo, iterations_s, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        iterations = int(iterations_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def check_credentials(username: str, password: str) -> bool:
    """Validate a login attempt against the configured user + password."""
    if not settings.auth_username or not password:
        return False
    user_ok = hmac.compare_digest(username, settings.auth_username)

    if settings.auth_password_hash:
        pass_ok = verify_password(password, settings.auth_password_hash)
    elif settings.auth_password:
        pass_ok = hmac.compare_digest(password, settings.auth_password)
    else:
        pass_ok = False
    # Evaluate both regardless of user_ok to avoid leaking which field was wrong.
    return user_ok and pass_ok


def auth_is_usable() -> bool:
    """True when auth is enabled *and* a credential is actually configured."""
    return settings.auth_enabled and bool(settings.auth_password or settings.auth_password_hash)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _main(argv: list[str]) -> int:
    import getpass

    cmd = argv[1] if len(argv) > 1 else "help"
    if cmd == "hash":
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Repeat:   "):
            print("Passwords do not match.")
            return 1
        print(hash_password(pw))
        return 0
    if cmd == "secret":
        print(secrets.token_urlsafe(48))
        return 0
    print(__doc__)
    return 0 if cmd in {"help", "-h", "--help"} else 1


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv))
