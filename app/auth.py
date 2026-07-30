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
import math
import secrets
import threading
import time

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
    # Compare as bytes: compare_digest raises TypeError on non-ASCII str input,
    # which would turn a stray umlaut in the login form into a 500.
    user_ok = hmac.compare_digest(username.encode(), settings.auth_username.encode())

    if settings.auth_password_hash:
        pass_ok = verify_password(password, settings.auth_password_hash)
    elif settings.auth_password:
        pass_ok = hmac.compare_digest(password.encode(), settings.auth_password.encode())
    else:
        pass_ok = False
    # Evaluate both regardless of user_ok to avoid leaking which field was wrong.
    return user_ok and pass_ok


def auth_is_usable() -> bool:
    """True when auth is enabled *and* a credential is actually configured."""
    return settings.auth_enabled and bool(settings.auth_password or settings.auth_password_hash)


# --------------------------------------------------------------------------- #
# Brute-force throttle
# --------------------------------------------------------------------------- #
class LoginThrottle:
    """Per-client brake on password guessing at the login form.

    After ``max_failures`` consecutive failures a client must wait ``lockout``
    seconds before the next attempt is even checked; every further failure
    restarts the clock, so a bot gets one guess per lockout window instead of
    an unbounded stream. A successful login clears the slate. State is
    in-memory only — a restart forgives everything, which is fine for slowing
    online guessing (PBKDF2 already covers offline attacks).
    """

    def __init__(self, max_failures: int = 5, lockout: float = 60.0, clock=time.monotonic):
        self.max_failures = max_failures
        self.lockout = lockout
        self._clock = clock
        self._lock = threading.Lock()
        # client -> (consecutive failures, time of last failure)
        self._state: dict[str, tuple[int, float]] = {}

    def retry_after(self, client: str) -> int:
        """Seconds until *client* may try again (0 = not blocked)."""
        with self._lock:
            entry = self._state.get(client)
            if entry is None or entry[0] < self.max_failures:
                return 0
            remaining = self.lockout - (self._clock() - entry[1])
            return math.ceil(remaining) if remaining > 0 else 0

    def note_failure(self, client: str) -> None:
        now = self._clock()
        with self._lock:
            failures = self._state.get(client, (0, now))[0] + 1
            self._state[client] = (failures, now)
            # Keep the table bounded: forget clients whose lockout has long
            # since expired (an hour of silence means they're not attacking).
            if len(self._state) > 256:
                cutoff = now - max(3600.0, self.lockout)
                self._state = {c: e for c, e in self._state.items() if e[1] >= cutoff}

    def note_success(self, client: str) -> None:
        with self._lock:
            self._state.pop(client, None)

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


# Shared instance used by the login route.
throttle = LoginThrottle()


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
