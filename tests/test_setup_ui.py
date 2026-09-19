"""The "Connect a model" dialog, when the server will not answer.

The dialog opened with an empty PROVIDER dropdown and no explanation. Its
status probe had failed, the failure was caught and discarded, and what was
left was a dialog with nothing to choose from — on the one screen whose whole
job is to fix a broken configuration.

Nothing in Python could see that: the endpoint was returning 200 with three
providers the entire time. It took running the real ``app.js`` against a
server that refuses to answer.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCENARIOS = REPO / "tests" / "js" / "setup_scenarios.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is not installed; app.js scenarios skipped"
)


def _run(app_js: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(SCENARIOS), str(app_js)],
        capture_output=True, text=True, timeout=120, cwd=str(REPO),
    )


@pytest.fixture(scope="module")
def result() -> subprocess.CompletedProcess:
    return _run(REPO / "iris" / "app" / "static" / "app.js")


def test_all_scenarios_pass(result):
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("scenario", [
    "dropdown is not empty when the probe fails",
    "groq is offered when the probe fails",
    "the failure is explained, not swallowed",
    "the server's list replaces the fallback",
    "no error is shown when nothing failed",
])
def test_every_scenario_passes(result, scenario):
    line = next((l for l in result.stdout.splitlines() if scenario in l), None)
    assert line is not None, f"scenario missing from output:\n{result.stdout}"
    assert line.startswith("OK"), result.stdout


def test_the_harness_still_catches_the_bug_it_was_written_for(tmp_path):
    """A test that cannot fail proves nothing.

    The pre-fix app.js left the dropdown empty whenever the status probe
    failed. If this stops reproducing that, the scenario has rotted rather
    than the bug having been fixed twice.
    """
    old = subprocess.run(
        ["git", "show", "1a0fab8:iris/app/static/app.js"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    if old.returncode != 0:
        pytest.skip("the pre-fix revision is not in this checkout")
    target = tmp_path / "app_old.js"
    target.write_text(old.stdout, encoding="utf-8")

    before = _run(target)
    assert "FAIL dropdown is not empty" in before.stdout, (
        "the scenario no longer reproduces the empty-dropdown bug\n" + before.stdout
    )
