"""Feature toggles — switch top-level panel functions on and off.

Every function (managing printers, scanning, self-updates) ships enabled. The
System page lets the operator turn off the ones they don't use; a disabled
function disappears from the navigation and its routes redirect back to the
dashboard. The choices persist in a small JSON state file (same pattern as the
updater) so they survive restarts and updates.

Unknown or absent state simply means "enabled" — a fresh install, a missing
file, or a feature added by a newer version all default to on, so an upgrade
never silently hides something the user relied on.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from ..config import settings
from . import system

log = logging.getLogger("prntbtlr.features")


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    description: str
    default: bool = True


@dataclass
class FeatureState:
    key: str
    label: str
    description: str
    enabled: bool


# The switchable functions, in the order they appear on the System page. Keys
# double as the navigation identifiers and the ``/printers`` / ``/scans`` route
# prefixes gated in ``main.py``.
FEATURES: tuple[Feature, ...] = (
    Feature(
        "printers",
        "Printers",
        "Manage print queues, jobs and AirPrint sharing.",
    ),
    Feature(
        "scans",
        "Scanning",
        "Scan from the browser and browse the saved-scan library.",
    ),
    Feature(
        "updates",
        "Self-updates",
        "Check GitHub Releases for new versions and install them.",
    ),
)

_BY_KEY: dict[str, Feature] = {f.key: f for f in FEATURES}

# systemd units each function owns. Switching a function off stops and disables
# these (no point running the print daemon on a scan-only box, and vice versa);
# switching it back on enables and starts them again. Units not in this map (or
# not installed) are left untouched. ``smbd``/``avahi-daemon`` are deliberately
# absent — they're shared infrastructure, not owned by one function.
FEATURE_SERVICES: dict[str, tuple[str, ...]] = {
    "printers": ("cups",),
    "scans": ("scanbd", "prntbtlr-scan-listen"),
    "updates": (),
}


def services_for(key: str) -> tuple[str, ...]:
    """systemd units owned by feature *key* (empty if none/unknown)."""
    return FEATURE_SERVICES.get(key, ())


# --------------------------------------------------------------------------- #
# Persistent state
# --------------------------------------------------------------------------- #
def _load_state() -> dict:
    try:
        with open(settings.feature_state_file) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    path = settings.feature_state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def is_enabled(key: str) -> bool:
    """True when feature *key* is on. Unknown keys and missing state default on."""
    feature = _BY_KEY.get(key)
    default = feature.default if feature else True
    value = _load_state().get(key)
    return bool(value) if isinstance(value, bool) else default


def states() -> dict[str, bool]:
    """Map of every feature key to its current enabled flag (for templates)."""
    state = _load_state()
    return {
        f.key: (state[f.key] if isinstance(state.get(f.key), bool) else f.default) for f in FEATURES
    }


def all_features() -> list[FeatureState]:
    """Every feature with its current on/off flag, for the System page."""
    current = states()
    return [FeatureState(f.key, f.label, f.description, current[f.key]) for f in FEATURES]


# --------------------------------------------------------------------------- #
# Updates
# --------------------------------------------------------------------------- #
def save(selected: set[str]) -> None:
    """Persist the toggles: features in *selected* are on, the rest off.

    Only known feature keys are written, so a stray form field can't create a
    bogus entry and unrelated keys already in the file are left untouched.
    """
    state = _load_state()
    for f in FEATURES:
        state[f.key] = f.key in selected
    _save_state(state)
    log.info("features updated: %s", {f.key: f.key in selected for f in FEATURES})


def apply_services(before: dict[str, bool], after: dict[str, bool]) -> list[str]:
    """Start/stop the systemd units of any function that just changed state.

    A function switched *off* has its units stopped and disabled on boot; one
    switched *on* has them enabled and started again. Units that aren't installed
    (or when systemctl is unavailable) are skipped, so a scan-only box that never
    had ``cups`` doesn't error. Returns short human-readable notes for the flash.
    """
    notes: list[str] = []
    for key in FEATURE_SERVICES:
        was, now = before.get(key, True), after.get(key, True)
        if was == now:
            continue
        for unit in services_for(key):
            # Skip units the host doesn't have (or when systemctl is missing —
            # service_state reports "not-installed"/"unknown" in both cases).
            if system.service_state(unit).status in ("not-installed", "unknown"):
                continue
            if now:  # switched on → enable + start
                system.enable_service(unit)
                res = system.start_service(unit)
                notes.append(f"started {unit}" if res.ok else f"could not start {unit}")
            else:  # switched off → stop + disable
                system.disable_service(unit)
                res = system.stop_service(unit)
                notes.append(f"stopped {unit}" if res.ok else f"could not stop {unit}")
    return notes
