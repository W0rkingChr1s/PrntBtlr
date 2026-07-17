"""Self-updates from GitHub Releases (stable + beta channels).

The repo publishes two kinds of releases: betas (``vX.Y.Z-beta.N``, GitHub
pre-releases) and stable releases (``vX.Y.Z``). The panel asks the GitHub API
for the newest release on the configured channel and either installs it
automatically or just shows a notice — both toggled on the System page. The
channel + auto-update preferences live in a small JSON state file so they
survive restarts and updates.

A release whose title or notes contain ``[failed]`` is skipped — the same
convention the promote workflow uses to decide which betas count towards the
next stable release.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from ..config import settings
from . import shell

log = logging.getLogger("prntbtlr.updater")

CHANNELS = ("stable", "beta")
FAILED_MARKER = "[failed]"

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$")
_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:-beta\.\d+)?$")

# Give the box a moment after boot before the first background check.
_STARTUP_DELAY = 30


@dataclass
class ReleaseInfo:
    tag: str  # e.g. "v0.2.0-beta.1"
    version: str  # e.g. "0.2.0-beta.1"
    prerelease: bool
    url: str
    published: str  # YYYY-MM-DD


@dataclass
class UpdateStatus:
    current: str
    channel: str
    auto_update: bool
    can_apply: bool
    last_check: str | None
    last_error: str | None
    available: dict | None  # ReleaseInfo as dict, or None


# --------------------------------------------------------------------------- #
# Versions & release selection
# --------------------------------------------------------------------------- #
def parse_version(text: str) -> tuple[int, int, int, int, int] | None:
    """Sortable key for ``[v]X.Y.Z[-beta.N]`` — a stable ranks above its betas."""
    m = _VERSION_RE.match(text.strip())
    if not m:
        return None
    major, minor, patch, beta = m.groups()
    if beta is None:
        return (int(major), int(minor), int(patch), 1, 0)
    return (int(major), int(minor), int(patch), 0, int(beta))


def _is_newer(tag: str) -> bool:
    candidate = parse_version(tag)
    if candidate is None:
        return False
    current = parse_version(__version__)
    return current is None or candidate > current


def _is_failed(release: dict) -> bool:
    text = f"{release.get('name') or ''} {release.get('body') or ''}".lower()
    return FAILED_MARKER in text


def pick_latest(releases: list[dict], channel: str) -> ReleaseInfo | None:
    """Newest usable release for *channel* (the beta channel sees stables too)."""
    best: dict | None = None
    best_key: tuple[int, ...] | None = None
    for rel in releases:
        if rel.get("draft") or _is_failed(rel):
            continue
        if rel.get("prerelease") and channel != "beta":
            continue
        key = parse_version(rel.get("tag_name") or "")
        if key is None:
            continue
        if best_key is None or key > best_key:
            best, best_key = rel, key
    if best is None:
        return None
    tag = best["tag_name"]
    return ReleaseInfo(
        tag=tag,
        version=tag[1:] if tag.startswith("v") else tag,
        prerelease=bool(best.get("prerelease")),
        url=best.get("html_url") or "",
        published=(best.get("published_at") or "")[:10],
    )


def fetch_releases() -> list[dict]:
    url = f"{settings.update_api_base}/repos/{settings.update_repo}/releases?per_page=30"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"prntbtlr/{__version__}",
        },
    )
    with urllib.request.urlopen(req, timeout=settings.command_timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("unexpected GitHub releases API response")
    return data


# --------------------------------------------------------------------------- #
# Persistent state (channel, auto-update, last check result)
# --------------------------------------------------------------------------- #
def _load_state() -> dict:
    try:
        with open(settings.update_state_file) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    path = settings.update_state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def save_prefs(channel: str, auto_update: bool) -> None:
    if channel not in CHANNELS:
        raise ValueError(f"unknown update channel: {channel}")
    state = _load_state()
    if state.get("channel") != channel:
        # Channel switched — a cached candidate may not fit the new channel.
        state["available"] = None
    state["channel"] = channel
    state["auto_update"] = bool(auto_update)
    _save_state(state)


def can_apply() -> bool:
    """In a container the image is the update path — self-apply on hosts only."""
    return not Path("/.dockerenv").exists()


def _status(state: dict) -> UpdateStatus:
    return UpdateStatus(
        current=__version__,
        channel=state.get("channel") or "stable",
        auto_update=bool(state.get("auto_update")),
        can_apply=can_apply(),
        last_check=state.get("last_check"),
        last_error=state.get("last_error"),
        available=state.get("available"),
    )


def status() -> UpdateStatus:
    """Cached status for rendering the System page — state file only, no network."""
    return _status(_load_state())


def notice() -> dict | None:
    """Update-available banner data (cheap enough for every page render)."""
    available = _load_state().get("available")
    if not available or not _is_newer(available.get("tag") or ""):
        return None
    return available


# --------------------------------------------------------------------------- #
# Checking & applying
# --------------------------------------------------------------------------- #
def check_for_update() -> UpdateStatus:
    """Query GitHub, remember the newest release for the channel, return status."""
    state = _load_state()
    channel = state.get("channel") or "stable"
    try:
        latest = pick_latest(fetch_releases(), channel)
        state["available"] = asdict(latest) if latest and _is_newer(latest.tag) else None
        state["last_error"] = None
    except (OSError, ValueError) as exc:
        state["last_error"] = str(exc)
    state["last_check"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        _save_state(state)
    except OSError as exc:
        log.warning("could not persist updater state: %s", exc)
    return _status(state)


def start_update(tag: str) -> shell.Result:
    """Kick off ``update.sh`` detached, so it survives the panel's own restart."""
    if not _TAG_RE.match(tag):
        return shell.Result(False, 2, "", f"invalid release tag: {tag}")
    script = settings.update_script
    if not script.exists():
        # Dev / non-installed fallback: run straight from the checkout.
        script = Path(__file__).resolve().parents[2] / "scripts" / "update.sh"
    if not script.exists():
        return shell.Result(False, 1, "", f"update script not found: {settings.update_script}")

    if shell.which("systemd-run"):
        # A transient unit lives outside the panel's cgroup, so the service
        # restart at the end of the update can't kill the update itself.
        shell.run(["systemctl", "reset-failed", "prntbtlr-update.service"])
        res = shell.run(
            ["systemd-run", "--unit=prntbtlr-update", "--collect", "/bin/bash", str(script), tag]
        )
        if res.ok:
            return res
        log.warning("systemd-run failed (%s) — falling back to a detached process", res.output)
    else:
        log.info("systemd-run not available — starting the updater as a detached process")
    subprocess.Popen(
        ["/bin/bash", str(script), tag],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return shell.Result(True, 0, f"update to {tag} started", "")


async def background_loop() -> None:
    """Periodic check; installs automatically when auto-update is switched on."""
    await asyncio.sleep(_STARTUP_DELAY)
    while True:
        try:
            st = await asyncio.to_thread(check_for_update)
            if st.available and _is_newer(st.available.get("tag") or ""):
                if st.auto_update and can_apply():
                    log.info("auto-update: installing %s", st.available["tag"])
                    res = start_update(st.available["tag"])
                    if not res.ok:
                        log.warning("auto-update could not start: %s", res.output)
                else:
                    log.info("update available: %s (notify only)", st.available["tag"])
        except Exception:
            log.exception("update check failed")
        await asyncio.sleep(settings.update_check_interval)
