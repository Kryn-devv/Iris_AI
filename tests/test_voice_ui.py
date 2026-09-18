"""The voice state machine, driven on a clock we own.

app.js is the one part of IRIS with no Python behind it, and its bugs are
about *timing*: who is still talking when the next thing arrives. A reminder
firing while she reads an answer aloud produced two voices at once — the exact
symptom reported — and no screenshot, and no test that does not own the clock,
can see that.

These scenarios run the real ``app.js`` inside a stub browser. They are skipped
where Node is unavailable, which keeps the promise that the suite passes on a
headless box with zero optional dependencies.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCENARIOS = REPO / "tests" / "js" / "voice_scenarios.js"

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


def test_no_two_voices_in_any_scenario(result):
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OVERLAP" not in result.stdout, result.stdout


@pytest.mark.parametrize("scenario", [
    "reminder lands mid-reply",
    "filler then answer",
    "server audio first, reply deferred",
    "node engine is not our audio",
    "two replies back to back",
])
def test_every_scenario_passes(result, scenario):
    line = next((l for l in result.stdout.splitlines() if scenario in l), None)
    assert line is not None, f"scenario missing from output:\n{result.stdout}"
    assert line.startswith("OK"), result.stdout


def test_a_reply_is_never_silently_dropped(result):
    """Deferring a reply behind the speakers is right; swallowing it is not."""
    assert "s:1000-4000" in result.stdout and "b:4000-" in result.stdout, (
        "the deferred reply should play once the server audio finishes\n" + result.stdout
    )


def test_the_harness_still_catches_the_bug_it_was_written_for(tmp_path):
    """A test that cannot fail proves nothing.

    The pre-fix app.js let a reminder play on the speakers while the browser
    read an answer aloud. If this scenario stops catching that, the scenario
    has rotted rather than the bug having been fixed twice.
    """
    old = subprocess.run(
        ["git", "show", "f2faaa4:iris/app/static/app.js"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    if old.returncode != 0:
        pytest.skip("the pre-fix revision is not in this checkout")
    target = tmp_path / "app_old.js"
    target.write_text(old.stdout, encoding="utf-8")

    before = _run(target)
    assert "OVERLAP" in before.stdout, (
        "the harness no longer reproduces the original two-voices bug\n" + before.stdout
    )
