"""scripts/uninstall.sh — the parts that edit files that aren't ours.

The uninstaller cuts its own block out of smb.conf and scanbd.conf instead of
restoring a backup, because those files hold the user's own settings too. That
surgery is what these tests pin down: our block goes, everything around it —
including a `[scans]` share somebody else wrote — stays.

The script is sourced with PRNTBTLR_UNINSTALL_LIB=1, which stops it right after
the function definitions, so nothing here can uninstall the machine running the
suite.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "uninstall.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

OUR_SHARE = """
[scans]
   comment = PrntBtlr scans
   path = /srv/scans
   read only = no
   guest ok = yes
   force user = pi
   create mask = 0664
   directory mask = 0775
"""

SMB_BASE = """[global]
   workgroup = WORKGROUP
   server string = %h

[homes]
   browseable = no
   read only = no
"""


def call(func, *args, env=None):
    """Source the uninstaller as a library and call one of its functions."""
    return subprocess.run(
        [
            "bash",
            "-c",
            f'PRNTBTLR_UNINSTALL_LIB=1 . "{SCRIPT}"; {func} "$@"',
            "bash",
            *[str(a) for a in args],
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", **(env or {})},
    )


def run_script(*args, env=None):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", **(env or {})},
    )


# --------------------------------------------------------------------------- #
# Samba share
# --------------------------------------------------------------------------- #
def test_removes_our_share_and_leaves_the_rest(tmp_path):
    conf = tmp_path / "smb.conf"
    conf.write_text(SMB_BASE + OUR_SHARE)

    assert call("strip_samba_share", conf).returncode == 0
    # Byte-identical to the file the installer found: the blank line it padded
    # its block with goes too, so install/uninstall cycles don't grow the file.
    assert conf.read_text() == SMB_BASE


def test_a_share_after_ours_survives(tmp_path):
    trailing = "\n[media]\n   path = /srv/media\n   read only = yes\n"
    conf = tmp_path / "smb.conf"
    conf.write_text(SMB_BASE + OUR_SHARE + trailing)

    assert call("strip_samba_share", conf).returncode == 0
    assert conf.read_text() == SMB_BASE + trailing


def test_a_foreign_scans_share_is_left_alone(tmp_path):
    foreign = "\n[scans]\n   comment = my own share\n   path = /mnt/scans\n"
    conf = tmp_path / "smb.conf"
    conf.write_text(SMB_BASE + foreign)

    assert call("strip_samba_share", conf).returncode == 1
    assert conf.read_text() == SMB_BASE + foreign


def test_share_removal_keeps_file_ownership_and_mode(tmp_path):
    conf = tmp_path / "smb.conf"
    conf.write_text(SMB_BASE + OUR_SHARE)
    conf.chmod(0o640)

    assert call("strip_samba_share", conf).returncode == 0
    assert conf.stat().st_mode & 0o777 == 0o640


def test_missing_smb_conf_is_not_an_error_to_report(tmp_path):
    assert call("strip_samba_share", tmp_path / "nope.conf").returncode == 1


def test_dry_run_does_not_touch_the_file(tmp_path):
    conf = tmp_path / "smb.conf"
    original = SMB_BASE + OUR_SHARE
    conf.write_text(original)

    assert call("strip_samba_share", conf, env={"DRY_RUN": "1"}).returncode == 0
    assert conf.read_text() == original


# --------------------------------------------------------------------------- #
# scanbd include
# --------------------------------------------------------------------------- #
SCANBD_BASE = """global {
        debug-level = 2
        user = saned
}
"""
OUR_INCLUDE = "\n# PrntBtlr: Canon PIXMA button actions\ninclude(scanner.d/prntbtlr-pixma.conf)\n"


def test_removes_our_include(tmp_path):
    conf = tmp_path / "scanbd.conf"
    conf.write_text(SCANBD_BASE + OUR_INCLUDE)

    assert call("strip_scanbd_include", conf).returncode == 0
    assert conf.read_text() == SCANBD_BASE


def test_the_distro_glob_include_survives(tmp_path):
    glob_include = "include(scanner.d/*.conf)\n"
    conf = tmp_path / "scanbd.conf"
    conf.write_text(SCANBD_BASE + glob_include + OUR_INCLUDE)

    assert call("strip_scanbd_include", conf).returncode == 0
    assert conf.read_text() == SCANBD_BASE + glob_include


def test_nothing_to_strip_reports_nothing_to_strip(tmp_path):
    conf = tmp_path / "scanbd.conf"
    conf.write_text(SCANBD_BASE)

    assert call("strip_scanbd_include", conf).returncode == 1
    assert conf.read_text() == SCANBD_BASE


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/", "/srv", "/var", "/home", "", "srv/scans", "/srv/../etc"])
def test_refuses_to_delete_system_directories(path):
    assert call("is_protected_dir", path).returncode == 0


@pytest.mark.parametrize("path", ["/srv/scans", "/mnt/nas/scans", "/home/pi/scans"])
def test_a_real_scan_folder_is_deletable(path):
    assert call("is_protected_dir", path).returncode == 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_help_lists_the_purge_flags():
    result = run_script("--help")
    assert result.returncode == 0
    for flag in ("--dry-run", "--purge-config", "--purge-scans", "--purge-packages", "--all"):
        assert flag in result.stdout


def test_unknown_option_is_rejected_before_anything_happens():
    result = run_script("--purge-everything")
    assert result.returncode == 2
    assert "Unknown option" in result.stderr
