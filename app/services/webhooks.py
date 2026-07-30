"""Outbound webhooks — POST a JSON payload to user URLs when things happen.

The panel emits an event (a scan finished, a printer was added, health changed,
an update is available/applied) and every enabled endpoint subscribed to that
event receives an HTTP POST with a small JSON body. It's the glue for wiring
PrntBtlr into home automation — n8n, Home Assistant, Discord/Slack relays, a
Raspberry Pi buzzer, whatever speaks HTTP.

Design notes, matching the rest of the app:

* Endpoints persist in a tiny JSON state file (same pattern as the features and
  updater state), so no database and they survive restarts/updates.
* Delivery uses the standard library (``urllib``) — no extra dependency — and
  runs on a small background thread pool, so a slow or dead endpoint never
  blocks the request that triggered the event.
* Every request is best-effort and self-contained: a failed delivery is logged,
  never raised into the caller. Emitting when nobody's subscribed is free.
* An optional per-endpoint secret signs the body with HMAC-SHA256
  (``X-Prntbtlr-Signature: sha256=…``) so the receiver can verify authenticity.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from .. import __version__
from ..config import settings
from . import statefile

log = logging.getLogger("prntbtlr.webhooks")


# --------------------------------------------------------------------------- #
# Event catalogue
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EventType:
    key: str  # stable identifier sent as the payload's ``event`` and header
    label: str  # human label for the System-page checkbox
    group: str  # grouping header on the System page


# The events an endpoint can subscribe to, grouped as they render on the System
# page. Keys are stable — they're the contract with the receiving side.
EVENTS: tuple[EventType, ...] = (
    EventType("scan.completed", "Scan completed", "Scanning"),
    EventType("printer.added", "Printer added", "Printers"),
    EventType("printer.deleted", "Printer removed", "Printers"),
    EventType("print.submitted", "Print job submitted", "Printers"),
    EventType("print.completed", "Print job completed", "Printers"),
    EventType("health.degraded", "Health degraded (warning/failure)", "Health"),
    EventType("health.recovered", "Health recovered", "Health"),
    EventType("repair.performed", "Self-repair performed", "Health"),
    EventType("update.available", "Update available", "Updates"),
    EventType("update.applied", "Update applied", "Updates"),
)

_EVENT_KEYS = frozenset(e.key for e in EVENTS)
# Events grouped for template rendering, preserving catalogue order.
EVENT_GROUPS: dict[str, list[EventType]] = {}
for _e in EVENTS:
    EVENT_GROUPS.setdefault(_e.group, []).append(_e)

# Health events, so the background monitor only runs when one is subscribed.
HEALTH_EVENTS = frozenset({"health.degraded", "health.recovered"})
# Print-job events, watched by the CUPS job monitor (jobmon) on the same terms.
PRINT_EVENTS = frozenset({"print.submitted", "print.completed"})


@dataclass
class Webhook:
    id: str
    url: str
    events: list[str]  # subscribed event keys ("*" means every event)
    secret: str = ""  # optional HMAC signing secret
    enabled: bool = True

    def wants(self, event_key: str) -> bool:
        """True when this endpoint should receive *event_key*."""
        return self.enabled and ("*" in self.events or event_key in self.events)

    def event_labels(self) -> list[str]:
        """Human labels for the subscribed events (for the System page)."""
        if "*" in self.events:
            return ["All events"]
        by_key = {e.key: e.label for e in EVENTS}
        return [by_key.get(k, k) for k in self.events]


# --------------------------------------------------------------------------- #
# Persistent state
# --------------------------------------------------------------------------- #
def _load() -> list[Webhook]:
    try:
        with open(settings.webhook_state_file) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    raw = data.get("endpoints") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    hooks: list[Webhook] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        events = item.get("events")
        hooks.append(
            Webhook(
                id=str(item.get("id") or _new_id()),
                url=str(item["url"]),
                events=[str(e) for e in events] if isinstance(events, list) else [],
                secret=str(item.get("secret") or ""),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return hooks


def _save(hooks: list[Webhook]) -> None:
    payload = {
        "endpoints": [
            {
                "id": h.id,
                "url": h.url,
                "events": list(h.events),
                "secret": h.secret,
                "enabled": h.enabled,
            }
            for h in hooks
        ]
    }
    # Owner-only: the state carries the HMAC signing secrets.
    statefile.save_json(settings.webhook_state_file, payload)


def _new_id() -> str:
    return secrets.token_hex(6)


# --------------------------------------------------------------------------- #
# CRUD (used by the System page)
# --------------------------------------------------------------------------- #
def list_webhooks() -> list[Webhook]:
    return _load()


def valid_url(url: str) -> bool:
    """Only absolute http(s) URLs with a host are accepted."""
    try:
        parts = urlparse(url.strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def blocked_target(url: str) -> str | None:
    """Why *url* must not receive webhooks, or ``None`` when it may (SSRF guard).

    The panel POSTs to whatever URL is configured, so without a check a webhook
    could be pointed at the box itself (loopback — e.g. the CUPS admin API on
    :631 or this panel's own routes) or at link-local space (cloud metadata
    endpoints like 169.254.169.254). Private LAN addresses stay allowed —
    home-automation receivers (n8n, Home Assistant, …) live there by design.

    A hostname that doesn't resolve is allowed through: delivery will fail on
    its own, and refusing it here would break adding an endpoint that's simply
    offline right now.
    """
    try:
        host = urlparse(url).hostname
    except ValueError:
        return "the URL cannot be parsed"
    if not host:
        return "the URL has no host"
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return None
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_loopback or ip.is_unspecified:
            return f"{host} points at this host itself ({ip})"
        if ip.is_link_local:
            return f"{host} is a link-local address ({ip})"
        if ip.is_multicast or ip.is_reserved:
            return f"{host} is not a routable unicast address ({ip})"
    return None


def valid_events(events: list[str]) -> list[str]:
    """Keep only known event keys, preserving catalogue order and de-duping."""
    chosen = set(events)
    return [e.key for e in EVENTS if e.key in chosen]


def add_webhook(url: str, events: list[str], secret: str = "") -> tuple[bool, str]:
    """Register a new endpoint. Returns ``(ok, message)`` for the flash."""
    url = url.strip()
    if not valid_url(url):
        return False, "Enter a valid http:// or https:// URL."
    reason = blocked_target(url)
    if reason:
        return False, f"Refusing this target: {reason}."
    keep = valid_events(events)
    if not keep:
        return False, "Pick at least one event to send."
    hooks = _load()
    hooks.append(Webhook(id=_new_id(), url=url, events=keep, secret=secret.strip()))
    try:
        _save(hooks)
    except OSError as exc:
        return False, f"Could not save webhook: {exc}"
    log.info("webhook added: %s → %s", ", ".join(keep), url)
    return True, f"Webhook added for {url}."


def delete_webhook(hook_id: str) -> bool:
    hooks = _load()
    remaining = [h for h in hooks if h.id != hook_id]
    if len(remaining) == len(hooks):
        return False
    _save(remaining)
    return True


def set_enabled(hook_id: str, enabled: bool) -> bool:
    hooks = _load()
    found = False
    for h in hooks:
        if h.id == hook_id:
            h.enabled = enabled
            found = True
    if found:
        _save(hooks)
    return found


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
# A small pool so a burst of events (or one slow endpoint) never piles up on, or
# blocks, the request thread that emitted them.
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="prntbtlr-webhook")


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _build_payload(event_key: str, data: dict) -> dict:
    return {
        "event": event_key,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app": settings.app_name,
        "version": __version__,
        "host": socket.gethostname(),
        "data": data,
    }


def _post(url: str, body: bytes, headers: dict[str, str], timeout: int) -> tuple[bool, str]:
    """Single blocking HTTP POST. Isolated so tests can stub it."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, str(getattr(exc, "reason", exc))


def deliver(hook: Webhook, event_key: str, payload: dict) -> tuple[bool, str]:
    """Deliver *payload* to one endpoint synchronously. Returns ``(ok, detail)``."""
    # Re-checked per delivery (not just when the endpoint was added) so a
    # hostname that later re-resolves to loopback/link-local (DNS rebinding) or
    # a hand-edited state file can't turn the panel into an SSRF proxy. The
    # resolve here and the one inside urlopen are still two lookups — a small
    # TOCTOU window we accept for a LAN tool.
    reason = blocked_target(hook.url)
    if reason:
        log.warning("webhook %s → %s refused: %s", event_key, hook.url, reason)
        return False, f"blocked target: {reason}"
    body = json.dumps(payload).encode("utf-8")
    # Header names use the canonical HTTP title-case that urllib puts on the wire
    # anyway, so what we document is exactly what a receiver sees (headers are
    # case-insensitive regardless).
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"prntbtlr/{__version__}",
        "X-Prntbtlr-Event": event_key,
        "X-Prntbtlr-Delivery": _new_id(),
    }
    if hook.secret:
        headers["X-Prntbtlr-Signature"] = _sign(hook.secret, body)
    ok, detail = _post(hook.url, body, headers, settings.webhook_timeout)
    level = log.debug if ok else log.warning
    level("webhook %s → %s: %s", event_key, hook.url, detail)
    return ok, detail


def emit(event_key: str, data: dict | None = None) -> int:
    """Fire *event_key* to every subscribed endpoint, off the request thread.

    Returns the number of endpoints the event was dispatched to (0 when nobody
    is subscribed). Never raises — a broken endpoint or unwritable state file is
    logged, not propagated into the action that triggered the event.
    """
    try:
        targets = [h for h in _load() if h.wants(event_key)]
        if not targets:
            return 0
        payload = _build_payload(event_key, data or {})
        for hook in targets:
            _pool.submit(_safe_deliver, hook, event_key, payload)
        return len(targets)
    except Exception:
        log.exception("failed to emit webhook %s", event_key)
        return 0


def _safe_deliver(hook: Webhook, event_key: str, payload: dict) -> None:
    try:
        deliver(hook, event_key, payload)
    except Exception:
        log.exception("webhook delivery crashed for %s", hook.url)


def emit_sync(event_key: str, data: dict | None = None) -> int:
    """Like :func:`emit`, but deliver inline and return the success count.

    For short-lived, out-of-process callers — the button-scan handler runs as a
    one-shot process, so the background pool would never get a turn before it
    exits. Delivery is blocking (bounded by ``webhook_timeout`` per endpoint) and
    still best-effort: failures are logged, never raised.
    """
    try:
        targets = [h for h in _load() if h.wants(event_key)]
        if not targets:
            return 0
        payload = _build_payload(event_key, data or {})
        return sum(1 for hook in targets if deliver(hook, event_key, payload)[0])
    except Exception:
        log.exception("failed to emit webhook %s", event_key)
        return 0


def send_test(hook_id: str) -> tuple[bool, str]:
    """Deliver a ``webhook.test`` payload to one endpoint and report the result."""
    hook = next((h for h in _load() if h.id == hook_id), None)
    if hook is None:
        return False, "Webhook not found."
    payload = _build_payload("webhook.test", {"message": "This is a PrntBtlr test event."})
    ok, detail = deliver(hook, "webhook.test", payload)
    return ok, (f"Test delivered ({detail})." if ok else f"Test failed: {detail}.")


# --------------------------------------------------------------------------- #
# Health-transition monitor (drives health.degraded / health.recovered)
# --------------------------------------------------------------------------- #
def has_subscriber(event_keys) -> bool:
    """True when any enabled endpoint subscribes to at least one of *event_keys*.

    Lets a background monitor skip its work entirely (no shell-outs, no polling)
    until something actually wants the events it produces.
    """
    keys = set(event_keys)
    return any(h.enabled and ("*" in h.events or keys & set(h.events)) for h in _load())


def _has_health_subscriber() -> bool:
    return has_subscriber(HEALTH_EVENTS)


def note_health(overall: str, previous: str | None) -> str | None:
    """Emit a health transition event when *overall* differs from *previous*.

    Returns the emitted event key (or ``None`` when nothing changed). The first
    observation after start-up has ``previous=None`` and only records a baseline,
    so a reboot doesn't fire a spurious "recovered".
    """
    if previous is None or overall == previous:
        return None
    # OK -> anything is a degrade; anything -> OK is a recovery. A warn<->fail
    # move stays "degraded" so escalations still notify.
    event = "health.recovered" if overall == "ok" else "health.degraded"
    emit(event, {"overall": overall, "previous": previous})
    return event


async def background_loop() -> None:
    """Poll health on an interval and emit degrade/recover transitions.

    Skips the (comparatively expensive) health run entirely unless an enabled
    endpoint is subscribed to a health event, so an install that only uses, say,
    scan events pays nothing for this loop.
    """
    import asyncio

    from . import health

    await asyncio.sleep(30)
    previous: str | None = None
    while True:
        try:
            if _has_health_subscriber():
                overall = (await asyncio.to_thread(health.run_checks)).overall
                note_health(overall, previous)
                previous = overall
            else:
                previous = None  # forget baseline so re-subscribing starts clean
        except Exception:
            log.exception("webhook health monitor failed")
        await asyncio.sleep(max(15, settings.webhook_health_interval))
