"""Tests for the CUPS output parsers — no real CUPS required (shell is mocked)."""

from app.services import cups, shell


def _result(stdout="", ok=True):
    return shell.Result(ok=ok, returncode=0 if ok else 1, stdout=stdout, stderr="")


def test_list_printers_parses_state_and_uri(monkeypatch):
    responses = {
        "-p": _result("printer MX870 is idle.  enabled since Wed 24 Jun 2026\n"),
        "-d": _result("system default destination: MX870\n"),
        "-v": _result("device for MX870: usb://Canon/MX870%20series?serial=1A2B3C\n"),
    }

    def fake_run(cmd, **kwargs):
        if cmd[0].endswith("lpstat"):
            return responses[cmd[1]]
        if cmd[0].endswith("lpoptions"):
            return _result("printer-error-policy=retry-job copies=1\n")
        return _result("", ok=False)

    monkeypatch.setattr(cups.shell, "run", fake_run)

    printers = cups.list_printers()
    assert len(printers) == 1
    p = printers[0]
    assert p.name == "MX870"
    assert p.state == "idle"
    assert p.is_default is True
    assert p.is_usb is True
    assert p.error_policy == "retry-job"


def test_list_jobs_parses_and_derives_printer(monkeypatch):
    out = "MX870-7   pi   4096   Wed 24 Jun 2026 10:00:00\n"
    monkeypatch.setattr(cups.shell, "run", lambda cmd, **k: _result(out))
    jobs = cups.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "MX870-7"
    assert jobs[0].printer == "MX870"
    assert jobs[0].size == 4096


def test_list_drivers_filters_by_query(monkeypatch):
    out = (
        "gutenprint.5.3://bjc-MX870/expert Canon PIXMA MX870 - CUPS+Gutenprint\n"
        "drv:///sample.drv/generic.ppd Generic PostScript Printer\n"
    )
    monkeypatch.setattr(cups.shell, "run", lambda cmd, **k: _result(out))
    drivers = cups.list_drivers("mx870")
    assert len(drivers) == 1
    assert drivers[0].ppd.startswith("gutenprint")


def test_set_error_policy_rejects_invalid():
    res = cups.set_error_policy("MX870", "bogus")
    assert res.ok is False


def test_is_valid_printer_name():
    for good in ("MX870", "office_printer", "hp.laserjet", "p-1", "A", "_x"):
        assert cups.is_valid_printer_name(good), good
    for bad in (
        "",
        " ",
        "has space",
        "-leadingdash",
        "with/slash",
        "with#hash",
        "tab\tname",
        "x" * 200,
    ):
        assert not cups.is_valid_printer_name(bad), repr(bad)


def test_printer_parses_serial_and_interface():
    p = cups.Printer(
        name="MX870-series",
        state="idle",
        uri="usb://Canon/MX870%20series?serial=10C5A0&interface=1",
    )
    assert p.serial == "10C5A0"
    assert p.usb_interface == "1"
    assert p.is_fax is False


def test_printer_detects_fax_interface():
    p = cups.Printer(
        name="MX870",
        state="idle",
        uri="usb://Canon/MX870%20series%20FAX?serial=10C5A0&interface=3",
    )
    assert p.serial == "10C5A0"
    assert p.usb_interface == "3"
    assert p.is_fax is True


def test_printer_without_query_has_no_serial():
    p = cups.Printer(name="net", state="idle", uri="ipp://printer.local/ipp/print")
    assert p.serial == ""
    assert p.usb_interface == ""
    assert p.device_params == {}
    assert p.is_fax is False


def test_duplicate_device_serials_groups_by_serial():
    printers = [
        cups.Printer(
            "MX870", "idle", uri="usb://Canon/MX870%20series%20FAX?serial=10C5A0&interface=3"
        ),
        cups.Printer(
            "MX870-series", "idle", uri="usb://Canon/MX870%20series?serial=10C5A0&interface=1"
        ),
        cups.Printer("Office", "idle", uri="usb://HP/LaserJet?serial=ABC123"),
    ]
    # The two Canon queues share serial 10C5A0; the HP is alone.
    assert cups.duplicate_device_serials(printers) == {"10C5A0"}


def test_duplicate_device_serials_ignores_missing_serial():
    printers = [
        cups.Printer("a", "idle", uri="ipp://host/ipp/print"),
        cups.Printer("b", "idle", uri="ipp://other/ipp/print"),
    ]
    assert cups.duplicate_device_serials(printers) == set()


def test_status_reports_unavailable(monkeypatch):
    monkeypatch.setattr(cups.shell, "which", lambda b: None)
    st = cups.status()
    assert st.available is False
    assert "not installed" in st.message
