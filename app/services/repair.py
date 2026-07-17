"""Self-repair (Selbstreparatur).

Acts on the health checks to bring the station back to a working state:

* restart a dead service and (re)enable it on boot,
* start the scan-button handler,
* restart the CUPS scheduler,
* wake a paused or stopped printer and set it to retry jobs,
* recreate the scan folder,
* re-enable AirPrint sharing.

Every action is idempotent (safe to run repeatedly) and reports what it did.
Nothing here deletes scans/jobs or touches the network configuration — those
breakages are surfaced by the checks but left for a human, since "fixing" them
automatically is either destructive or risks cutting off remote access.

The manual **Run self-repair** button and the optional background loop both go
through :func:`run`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..config import settings
from . import cups, health, system

log = logging.getLogger("prntbtlr.repair")

_STARTUP_DELAY = 45


@dataclass
class RepairAction:
    target: str  # the Check.key this addresses
    title: str
    ok: bool
    message: str


# --------------------------------------------------------------------------- #
# Per-target repairs (each re-reads live state so it's safe to call directly)
# --------------------------------------------------------------------------- #
def repair_service(name: str) -> list[RepairAction]:
    st = system.service_state(name)
    if st.status in ("not-installed", "unknown"):
        return []
    actions: list[RepairAction] = []
    key = f"service:{name}"
    if not st.active:
        res = system.restart_service(name)
        actions.append(
            RepairAction(
                key,
                f"Restart {name}",
                res.ok,
                f"{name} restarted." if res.ok else f"restart failed: {res.output}",
            )
        )
    if not st.enabled:
        res = system.enable_service(name)
        actions.append(
            RepairAction(
                key,
                f"Enable {name} on boot",
                res.ok,
                f"{name} enabled on boot." if res.ok else f"enable failed: {res.output}",
            )
        )
    return actions


def repair_scan_button() -> list[RepairAction]:
    target = health.scan_button_target()
    if target is None:
        return []
    return repair_service(target)


def repair_cups() -> list[RepairAction]:
    res = system.restart_service("cups")
    return [
        RepairAction(
            "cups",
            "Restart CUPS",
            res.ok,
            "CUPS restarted." if res.ok else f"restart failed: {res.output}",
        )
    ]


def repair_printer(name: str) -> list[RepairAction]:
    key = f"printer:{name}"
    res = cups.set_enabled(name, True)  # cupsenable + resume
    if not res.ok:
        return [
            RepairAction(key, f"Resume {name}", False, f"could not resume {name}: {res.output}")
        ]
    # Keep it from stalling on the next single error — the classic stuck-queue fix.
    cups.set_error_policy(name, "retry-job")
    return [RepairAction(key, f"Resume {name}", True, f"{name} resumed and set to retry jobs.")]


def repair_storage() -> list[RepairAction]:
    directory = settings.scan_dir
    if directory.exists():
        return []
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return [RepairAction("storage", "Create scan folder", False, f"mkdir failed: {exc}")]
    return [RepairAction("storage", "Create scan folder", True, f"Created {directory}.")]


def repair_sharing() -> list[RepairAction]:
    res = cups.set_sharing(True)
    return [
        RepairAction(
            "sharing",
            "Enable AirPrint sharing",
            res.ok,
            "AirPrint/IPP sharing enabled." if res.ok else f"failed: {res.output}",
        )
    ]


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def _dispatch(check: health.Check) -> list[RepairAction]:
    key = check.key
    if key == "cups":
        return repair_cups()
    if key == "scanbutton":
        return repair_scan_button()
    if key == "storage":
        return repair_storage()
    if key == "sharing":
        return repair_sharing()
    if key.startswith("service:"):
        return repair_service(key.split(":", 1)[1])
    if key.startswith("printer:"):
        return repair_printer(key.split(":", 1)[1])
    return []


def run(
    report: health.HealthReport | None = None,
) -> tuple[list[RepairAction], health.HealthReport]:
    """Repair every fixable check, then re-run the checks to show the outcome.

    Returns ``(actions_taken, report_after)``. With no repairable checks the
    action list is empty and *report_after* just mirrors the current state.
    """
    if report is None:
        report = health.run_checks()
    actions: list[RepairAction] = []
    for check in report.repairable:
        actions += _dispatch(check)
    after = health.run_checks() if actions else report
    return actions, after


# --------------------------------------------------------------------------- #
# Optional background self-repair
# --------------------------------------------------------------------------- #
async def background_loop() -> None:
    """Periodically repair the station on its own (opt-in via config)."""
    await asyncio.sleep(_STARTUP_DELAY)
    while True:
        try:
            report = await asyncio.to_thread(health.run_checks)
            if report.repairable:
                names = ", ".join(c.key for c in report.repairable)
                log.info("self-repair: acting on %s", names)
                actions, _ = await asyncio.to_thread(run, report)
                for a in actions:
                    (log.info if a.ok else log.warning)("self-repair: %s — %s", a.title, a.message)
        except Exception:
            log.exception("self-repair sweep failed")
        await asyncio.sleep(max(30, settings.self_repair_interval))
