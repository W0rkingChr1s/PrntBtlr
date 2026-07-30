"""Atomic JSON state files, written owner-only.

The features, updater, webhook and scan-caps state all persist as small JSON
files under ``/etc/prntbtlr``. They share two requirements: a crash must never
leave a half-written file behind (write to a temp name, rename into place), and
the files must not be world-readable — the webhook state carries HMAC signing
secrets, and the rest has no business being readable by other local users
either.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def save_json(path: Path, payload) -> None:
    """Serialize *payload* to *path* atomically with ``0600`` permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(payload, indent=2))
    # O_CREAT's mode only applies to a newly created file; chmod as well so
    # rewriting a pre-existing world-readable state file tightens it too.
    os.chmod(tmp, 0o600)
    tmp.replace(path)
