"""CUPS print-job monitor — fires print.submitted / print.completed webhooks.

Real print jobs (AirPrint from an iPhone, ``lp`` from a laptop, the panel's own
test page) go straight to ``cupsd`` and never touch the web app, so the routes
can't emit their webhooks the way they do for browser scans. Rather than install
a CUPS notifier and juggle IPP subscription leases on a headless box, this polls
``lpstat`` on an interval and diffs the job list — the same tool the rest of the
app already shells out to. A little latency (the poll interval) buys zero CUPS
reconfiguration and no extra failure modes across ``cupsd`` restarts.

The monitor is idle by default: it only polls while an enabled webhook is
subscribed to a print event, and the first sweep after (re)start records a
baseline without emitting, so a restart never replays the existing job history.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ..config import settings
from . import cups, webhooks

log = logging.getLogger("prntbtlr.jobmon")


@dataclass
class State:
    """What we've already reported, so each job fires each event at most once."""

    submitted: set[str] = field(default_factory=set)
    completed: set[str] = field(default_factory=set)
    started: bool = False  # has the baseline sweep run?


def _job_num(job_id: str) -> int:
    """Trailing integer of a ``queue-N`` job id, for chronological ordering."""
    tail = job_id.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _payload(job: cups.Job) -> dict:
    return {
        "job": job.id,
        "printer": job.printer,
        "user": job.user,
        "size": job.size,
        "when": job.when,
    }


def sweep(
    active: list[cups.Job], completed: list[cups.Job], state: State
) -> list[tuple[str, cups.Job]]:
    """Diff a poll against *state*, returning ``(event_key, job)`` pairs to emit.

    Mutates *state* to remember what's been reported. The first call only records
    a baseline (returns nothing). State is pruned to the jobs CUPS still knows
    about, so it can't grow without bound as jobs age out of the history.
    """
    active_by_id = {j.id: j for j in active}
    completed_by_id = {j.id: j for j in completed}
    all_ids = set(active_by_id) | set(completed_by_id)

    events: list[tuple[str, cups.Job]] = []
    if not state.started:
        # Baseline: adopt the current jobs as already-seen so a restart doesn't
        # replay everything still in the CUPS history.
        state.submitted = set(all_ids)
        state.completed = set(completed_by_id)
        state.started = True
        return events

    # A job we've never seen (even one so quick it only shows up as completed)
    # gets a submitted event; then, if it's already done, a completed one.
    for jid in sorted(all_ids, key=_job_num):
        if jid not in state.submitted:
            state.submitted.add(jid)
            events.append(("print.submitted", active_by_id.get(jid) or completed_by_id[jid]))
    for jid in sorted(completed_by_id, key=_job_num):
        if jid not in state.completed:
            state.completed.add(jid)
            events.append(("print.completed", completed_by_id[jid]))

    # Forget jobs CUPS has aged out of its history (they can't recur).
    state.submitted &= all_ids
    state.completed &= set(completed_by_id)
    return events


async def background_loop() -> None:
    """Poll CUPS and emit print-job webhooks on new submissions/completions."""
    await asyncio.sleep(20)  # let cupsd settle after boot
    state = State()
    while True:
        try:
            if webhooks.has_subscriber(webhooks.PRINT_EVENTS) and cups.available():
                active = await asyncio.to_thread(cups.list_jobs)
                completed = await asyncio.to_thread(cups.list_jobs, "completed")
                for event_key, job in sweep(active, completed, state):
                    webhooks.emit(event_key, _payload(job))
            else:
                # Nobody's listening (or no CUPS) — drop the baseline so that
                # re-subscribing later starts clean instead of flooding.
                state = State()
        except Exception:
            log.exception("print job monitor failed")
        await asyncio.sleep(max(5, settings.webhook_jobs_interval))
