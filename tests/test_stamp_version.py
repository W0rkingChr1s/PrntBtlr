"""scripts/stamp-version.sh — the single writer of the version stamp.

The install paths rely on it (release tarballs, git checkouts, the release
workflows), and a wrong stamp is exactly the bug it exists to prevent: an
install that reports a version it isn't running.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "stamp-version.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def tree(tmp_path):
    """A minimal copy of the files the stamper writes."""
    (tmp_path / "app").mkdir()
    shutil.copy(REPO / "app" / "__init__.py", tmp_path / "app" / "__init__.py")
    shutil.copy(REPO / "pyproject.toml", tmp_path / "pyproject.toml")
    return tmp_path


def stamp(tree, version):
    return subprocess.run(
        ["bash", str(SCRIPT), version, str(tree)],
        capture_output=True,
        text=True,
    )


def versions(tree):
    init = (tree / "app" / "__init__.py").read_text()
    pyproject = (tree / "pyproject.toml").read_text()
    return (
        re.search(r'^__version__ = "(.*)"$', init, re.M).group(1),
        re.search(r'^version = "(.*)"$', pyproject, re.M).group(1),
    )


def test_stamps_both_files(tree):
    assert stamp(tree, "1.2.3").returncode == 0
    assert versions(tree) == ("1.2.3", "1.2.3")


def test_beta_keeps_the_tag_spelling_but_pyproject_gets_pep440(tree):
    # __version__ is compared against release tag names (v0.4.0-beta.2), while
    # pyproject has to be a version Python packaging accepts.
    assert stamp(tree, "0.4.0-beta.2").returncode == 0
    assert versions(tree) == ("0.4.0-beta.2", "0.4.0b2")


def test_local_build_suffix_survives(tree):
    assert stamp(tree, "0.3.1+dev.4.gabc1234").returncode == 0
    assert versions(tree) == ("0.3.1+dev.4.gabc1234", "0.3.1+dev.4.gabc1234")


def test_leaves_other_version_keys_alone(tree):
    stamp(tree, "9.9.9")
    pyproject = (tree / "pyproject.toml").read_text()
    assert 'target-version = "py39"' in pyproject
    assert 'requires-python = ">=3.9"' in pyproject


def test_rerunning_is_a_no_op(tree):
    stamp(tree, "1.2.3")
    before = (tree / "app" / "__init__.py").read_bytes()
    stamp(tree, "1.2.3")
    # The release workflows commit only when this leaves a diff.
    assert (tree / "app" / "__init__.py").read_bytes() == before


@pytest.mark.parametrize(
    "bad",
    ["0.4", "0.4.0.1", "0.4.0-rc.1", "0.4.0-beta", "v1.2.3", "1.2.3; id", ""],
)
def test_rejects_anything_that_is_not_a_version(tree, bad):
    before = versions(tree)
    assert stamp(tree, bad).returncode != 0
    assert versions(tree) == before


def test_fails_on_a_tree_that_is_not_prntbtlr(tmp_path):
    res = stamp(tmp_path, "1.2.3")
    assert res.returncode == 1
    assert "does not exist" in res.stderr
