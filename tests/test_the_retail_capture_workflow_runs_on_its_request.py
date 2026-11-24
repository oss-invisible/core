"""The retail-capture workflow runs when captures/REQUEST.json changes, once, on one runner, and only that.

.github/workflows/retail-capture.yml takes one capture of the official retail Firefox on a GitHub Windows runner.
What is held here, on the file itself:

  * the trigger is an event of the code: a pull request or a push to main that changes captures/REQUEST.json. No
    schedule, no manual or dispatched run, no workflow_call;
  * one job on windows-latest (the account runs at most 20 jobs at once), TEMP and TMP on D:;
  * the steps are the ones the rule names, in order, and each script they run exists in this repository;
  * the browser is started only by scripts/retail/run_retail_capture.py, never by the workflow, and nothing in the
    workflow sets a preference, a policy or an automation argument;
  * the artifact is uploaded even when a step before it is red.
"""
from __future__ import annotations

import re

import pytest
import yaml

from _retail_scripts import ROOT

pytestmark = pytest.mark.unit

PATH = ROOT / ".github" / "workflows" / "retail-capture.yml"
TEXT = PATH.read_text(encoding="utf-8")
SPEC = yaml.safe_load(TEXT)
ON = SPEC.get("on", SPEC.get(True))  # PyYAML reads the key `on` as a boolean
STEPS = SPEC["jobs"]["capture"]["steps"]
D_TEMP = "D:" + chr(92) + "a" + chr(92) + "_temp"


def _runs() -> str:
    return "\n".join(s.get("run", "") for s in STEPS)


def test_it_runs_on_a_change_of_the_request_and_on_nothing_else():
    assert set(ON) == {"push", "pull_request"}, f"triggers: {sorted(ON)}"
    assert ON["push"] == {"branches": ["main"], "paths": ["captures/REQUEST.json"]}
    assert ON["pull_request"] == {"paths": ["captures/REQUEST.json"]}


def test_it_is_one_job_on_a_windows_runner_with_temp_on_d():
    assert list(SPEC["jobs"]) == ["capture"]
    job = SPEC["jobs"]["capture"]
    assert job["runs-on"] == "windows-latest" and "strategy" not in job
    assert job["env"]["TEMP"] == D_TEMP and job["env"]["TMP"] == D_TEMP
    assert job["timeout-minutes"] <= 30


def test_a_new_push_to_a_pull_request_replaces_its_run_and_nothing_else_is_cancelled():
    conc = SPEC["concurrency"]
    assert conc["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert "github.event.pull_request.number" in conc["group"] and "github.run_id" in conc["group"]
    assert SPEC["permissions"] == {"contents": "read"}


def test_the_steps_are_the_ones_of_the_rule_in_order():
    scripts = re.findall(r"scripts/retail/(\w+\.py)", _runs())
    assert scripts == ["capture_request.py", "firefox_build.py", "run_retail_capture.py", "make_runner_capture.py",
                       "check_runner_capture.py"]
    for name in scripts:
        assert (ROOT / "scripts" / "retail" / name).is_file(), name
    assert "fetch --version" in _runs()


def test_the_browser_is_started_by_the_script_only():
    runs = _runs()
    assert not re.search(r"firefox(\.exe)?\s+-", runs) and "Start-Process" not in runs and "start firefox" not in runs.lower()
    assert re.search(r"run_retail_capture\.py --install \"\$RUNNER_TEMP/ff/extracted/core\" --out", runs)


def test_nothing_in_the_workflow_sets_a_preference_a_policy_or_an_automation_argument():
    lowered = TEXT.lower()
    for token in ("user.js", "prefs.js", "policies.json", "user_pref", "marionette", "remote-debugging", "headless",
                  "-juggler", "webdriver", "moz_", "geckodriver", "distribution"):
        assert token not in lowered, token


def test_the_build_is_fetched_by_the_script_that_verifies_it_and_nothing_is_installed():
    runs = _runs()
    assert "firefox_build.py fetch" in runs
    for token in ("msiexec", "/S ", "setup.exe", "winget", "choco", "Invoke-WebRequest", "curl", "wget"):
        assert token not in runs, f"{token}: the build is downloaded and checked by firefox_build.py only"


def test_the_artifact_is_kept_when_a_step_is_red():
    gather, upload = STEPS[-2], STEPS[-1]
    assert gather["if"] == "always()" and upload["if"] == "always()"
    assert upload["uses"].startswith("actions/upload-artifact@v4")
    assert upload["with"]["name"] == "retail-capture-${{ steps.request.outputs.firefox_version }}"


def test_the_checkout_is_the_commit_that_changed_the_request():
    assert STEPS[0]["uses"].startswith("actions/checkout@") and "with" not in STEPS[0]


def test_the_comparison_with_the_registry_is_a_step_of_the_job():
    check = next(s for s in STEPS if "check_runner_capture.py" in s.get("run", ""))
    assert "steps.made.outputs.capture_dir" in check["run"]
