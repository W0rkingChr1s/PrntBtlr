"""Tests for the CUPS print-job monitor's diff logic."""

from app.services import cups, jobmon


def _job(job_id, printer="MX870", user="pi", size=1024, when="today"):
    return cups.Job(id=job_id, user=user, size=size, when=when, printer=printer)


def test_baseline_sweep_emits_nothing():
    state = jobmon.State()
    events = jobmon.sweep([_job("MX870-1")], [_job("MX870-0")], state)
    assert events == []
    assert state.started
    # Everything currently known is adopted as already-seen.
    assert state.submitted == {"MX870-1", "MX870-0"}
    assert state.completed == {"MX870-0"}


def test_new_job_emits_submitted():
    state = jobmon.State()
    jobmon.sweep([_job("MX870-1")], [], state)  # baseline
    events = jobmon.sweep([_job("MX870-1"), _job("MX870-2")], [], state)
    assert [e[0] for e in events] == ["print.submitted"]
    assert events[0][1].id == "MX870-2"


def test_completion_emits_completed_without_reemitting_submitted():
    state = jobmon.State()
    jobmon.sweep([_job("MX870-1")], [], state)  # baseline: job 1 active
    # Job 1 finishes; job 2 appears and also finishes in the same interval.
    events = jobmon.sweep([], [_job("MX870-1"), _job("MX870-2")], state)
    kinds = [e[0] for e in events]
    # Job 2 is brand new -> submitted; both 1 and 2 are newly completed.
    assert kinds.count("print.submitted") == 1
    assert kinds.count("print.completed") == 2
    assert ("print.submitted", "MX870-2") in [(k, j.id) for k, j in events]
    # Completions come out in job-number order.
    completed_ids = [j.id for k, j in events if k == "print.completed"]
    assert completed_ids == ["MX870-1", "MX870-2"]


def test_fast_job_seen_only_as_completed_gets_both_events():
    state = jobmon.State()
    jobmon.sweep([], [], state)  # empty baseline
    events = jobmon.sweep([], [_job("MX870-5")], state)
    assert [e[0] for e in events] == ["print.submitted", "print.completed"]
    assert all(j.id == "MX870-5" for _, j in events)


def test_no_duplicate_emits_across_polls():
    state = jobmon.State()
    jobmon.sweep([], [], state)
    jobmon.sweep([], [_job("MX870-5")], state)
    # Same state again -> nothing new.
    assert jobmon.sweep([], [_job("MX870-5")], state) == []


def test_submitted_events_are_job_number_ordered():
    state = jobmon.State()
    jobmon.sweep([], [], state)
    events = jobmon.sweep([_job("MX870-3"), _job("MX870-1"), _job("MX870-2")], [], state)
    assert [j.id for _, j in events] == ["MX870-1", "MX870-2", "MX870-3"]


def test_state_prunes_jobs_aged_out_of_history():
    state = jobmon.State()
    jobmon.sweep([], [_job("MX870-1")], state)  # baseline knows job 1
    # Job 1 ages out of the CUPS history; nothing is known now.
    jobmon.sweep([], [], state)
    assert state.submitted == set()
    assert state.completed == set()


def test_payload_shape():
    p = jobmon._payload(_job("MX870-7", user="alice", size=2048))
    assert p == {
        "job": "MX870-7",
        "printer": "MX870",
        "user": "alice",
        "size": 2048,
        "when": "today",
    }
