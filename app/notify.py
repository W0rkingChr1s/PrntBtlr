"""CLI to emit a webhook event from out-of-process helpers.

Button scans are handled outside the web app (``scan2pdf.sh``, driven by
``scanbd`` / ``scan-listen.py``), so they can't call :func:`webhooks.emit`
in-process the way the browser routes do. This tiny entry point bridges that gap:
the scan script runs it once the PDF is published, and it delivers the same
``scan.completed`` webhook browser scans already send.

Usage::

    python -m app.notify scan.completed file=scan_20260730.pdf mode=Color source=button

Positional args after the event are ``key=value`` pairs folded into the payload's
``data`` object. Delivery is synchronous (this process is short-lived) and
best-effort: any problem is logged and the process still exits ``0``, so a
webhook hiccup never fails a scan. Run it from the app directory (or with the app
on ``PYTHONPATH``) using the app's virtualenv interpreter.
"""

from __future__ import annotations

import logging
import sys

from .services import webhooks

log = logging.getLogger("prntbtlr.notify")


def _parse(argv: list[str]) -> tuple[str | None, dict[str, str]]:
    if not argv:
        return None, {}
    event = argv[0]
    data: dict[str, str] = {}
    for pair in argv[1:]:
        key, sep, value = pair.partition("=")
        if sep and key:
            data[key] = value
    return event, data


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="prntbtlr.notify: %(message)s")
    event, data = _parse(sys.argv[1:] if argv is None else argv)
    if not event:
        print("usage: python -m app.notify <event> [key=value ...]", file=sys.stderr)
        return 2
    try:
        n = webhooks.emit_sync(event, data)
        log.info("%s → delivered to %d endpoint(s)", event, n)
    except Exception:  # never let a webhook problem surface as a failed scan
        log.exception("failed to emit %s", event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
