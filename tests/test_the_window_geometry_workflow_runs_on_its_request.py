"""The retail-window-geometry workflow runs when captures/WINDOW_GEOMETRY_REQUEST.json changes, and is judged by one job.

.github/workflows/retail-window-geometry.yml measures the window geometry of the official retail Firefox on windows-2025,
one job per display scale. Held here, on the file itself:

  * the trigger is an event of the code (a pull request or a push to main that changes the request); no schedule, no
    dispatched run;
  * a first job validates the request; the capture job is a matrix of the scales it prints, on windows-2025, TEMP and TMP
    on D:, 30 minutes at most, `fail-fast: false`;
  * the browser is started only by the scripts, never by the workflow; the build is fetched by the script that verifies it;
  * the artifact is named for the version and the scale, is uploaded even when a step is red, and is kept 90 days;
  * the `judge` job needs the matrix, runs `if: always()`, downloads every artifact and runs the judge script;
  * the Marionette exception and its proof are in the header of the file;
  * every script it runs exists, and every action is pinned the way retail-capture.yml pins them.
"""
from __future__ import annotations

import re

import pytest
import yaml

from _retail_scripts import ROOT, load_window_geometry

pytestmark = pytest.mark.unit

WORKFLOWS = ROOT / ".github" / "workflows"
TEXT = (WORKFLOWS / "retail-window-geometry.yml").read_text(encoding="utf-8")
SPEC = yaml.safe_load(TEXT)
ON = SPEC.get("on", SPEC.get(True))
JOBS = SPEC["jobs"]
D_TEMP = "D:" + chr(92) + "a" + chr(92) + "_temp"


def _runs(job: str) -> str:
    return "\n".join(s.get("run", "") for s in JOBS[job]["steps"])


def test_it_runs_on_a_change_of_the_request_and_on_nothing_else():
    assert set(ON) == {"push", "pull_request"}
    assert ON["push"] == {"branches": ["main"], "paths": ["captures/WINDOW_GEOMETRY_REQUEST.json"]}
    assert ON["pull_request"] == {"paths": ["captures/WINDOW_GEOMETRY_REQUEST.json"]}


def test_a_new_push_to_a_pull_request_replaces_its_run_and_nothing_else_is_cancelled():
    conc = SPEC["concurrency"]
    assert conc["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert "github.event.pull_request.number" in conc["group"] and "github.run_id" in conc["group"]
    assert SPEC["permissions"] == {"contents": "read"}


def test_the_jobs_are_the_request_the_matrix_and_the_judge():
    assert list(JOBS) == ["request", "capture", "judge"]
    assert "request.py" in _runs("request")
    assert JOBS["request"]["outputs"].keys() == {"firefox_version", "dpr"}


def test_the_capture_is_one_job_per_scale_on_windows_2025_with_temp_on_d():
    job = JOBS["capture"]
    assert job["needs"] == "request" and job["runs-on"] == "windows-2025"
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["matrix"] == {"dpr": "${{ fromJSON(needs.request.outputs.dpr) }}"}
    assert job["env"]["TEMP"] == D_TEMP and job["env"]["TMP"] == D_TEMP
    assert job["timeout-minutes"] == 30, "the jobs of round 7, restored-window steps and grid included, took 4 to 11 minutes (run_scale.TIME_BUDGET_SECONDS, 20)"
    assert "--dpr ${{ matrix.dpr }}" in _runs("capture")


def test_the_browser_is_started_by_the_scripts_and_the_build_by_the_script_that_verifies_it():
    runs = _runs("capture")
    assert not re.search(r"firefox(\.exe)?\s+-", runs) and "Start-Process" not in runs
    assert "firefox_build.py fetch" in runs and "run_scale.py" in runs and '--install "$RUNNER_TEMP/ff/extracted/core"' in runs
    for token in ("msiexec", "setup.exe", "winget", "choco", "Invoke-WebRequest", "curl", "wget"):
        assert token not in runs


def test_the_artifact_is_named_for_version_and_scale_and_kept_when_a_step_is_red():
    upload = JOBS["capture"]["steps"][-1]
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == "retail-window-geometry-${{ needs.request.outputs.firefox_version }}-dpr${{ matrix.dpr }}"
    assert upload["with"]["retention-days"] == 90


def test_the_judge_waits_for_the_matrix_runs_always_and_reads_every_artifact():
    job = JOBS["judge"]
    assert job["needs"] == ["request", "capture"] and job["if"] == "always()"
    download = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/download-artifact@"))
    assert download["with"]["pattern"] == "retail-window-geometry-*"
    assert "judge.py --in judge-in" in _runs("judge")


def test_the_marionette_exception_and_its_proof_are_in_the_header():
    header = TEXT.split("\non:")[0]
    for needle in ("MARIONETTE EXCEPTION", "-marionette", "plain launch", "maximizing the window", "WINDOW_FRAME_BY_DPR", "judge.py"):
        assert needle in header, needle


def test_every_script_it_runs_exists_and_every_action_is_pinned_like_retail_capture():
    scripts = set(re.findall(r"scripts/retail/([\w/]+\.py)", TEXT))
    assert scripts == {"window_geometry/request.py", "firefox_build.py", "window_geometry/run_scale.py", "window_geometry/judge.py"}
    for name in scripts:
        assert (ROOT / "scripts" / "retail" / name).is_file(), name
    pinned = set(re.findall(r"uses: (actions/[\w-]+@[0-9a-f]{40})", (WORKFLOWS / "retail-capture.yml").read_text(encoding="utf-8")))
    used = set(re.findall(r"uses: (actions/[\w-]+@\S+)", TEXT))
    assert all(re.fullmatch(r"actions/[\w-]+@[0-9a-f]{40}", u) for u in used), "every action is pinned by commit SHA"
    only_here = {u.split("@")[0] for u in used - pinned}
    assert only_here == {"actions/download-artifact"}, "an action pinned differently from retail-capture.yml"


def test_the_every_resolution_launches_stop_with_the_minutes_the_setup_the_last_launch_and_the_upload_need():
    run_scale = load_window_geometry("run_scale")
    timeout_seconds = JOBS["capture"]["timeout-minutes"] * 60
    assert run_scale.TIME_BUDGET_SECONDS == 20 * 60 and timeout_seconds - run_scale.TIME_BUDGET_SECONDS >= 10 * 60


def test_the_header_says_what_round_5_added():
    header = TEXT.split("\non:")[0]
    for needle in ("Round 5", "eight steps", "iframes_transformed", "real click", "F11-style fullscreen", "cross-site iframe"):
        assert needle in header, needle


def test_the_header_says_what_round_7_adds():
    header = TEXT.split("\non:")[0]
    for needle in ("Round 7 adds no step to the launches that are compared", "surface.html", "parse time", "DOMContentLoaded", "first and second animation frame",
                   "`early`", "iframes_max and iframes_transformed", "each frame is a new document", "not judged"):
        assert needle in header, needle
    assert header.index("Round 7") > header.index("Round 5"), "round 7 after round 5"


def test_the_header_says_round_8_drops_the_restored_window_steps_and_launch_n_and_why():
    header = " ".join(line.lstrip("# ").strip() for line in TEXT.split("\non:")[0].splitlines())
    for needle in ("Round 8 takes the restored-window steps out of the plan, and so out of the judged set",
                   "the restored-window feature is dropped from the product, so the capture no longer measures it",
                   "Launch n", "the runner's display refuses panels above 1920x1080 (code -2)", "so it can never run there",
                   "iframes_juggler (events.count 2 against 3 at dpr 1.5, run 46061552952) leaves with the step",
                   "the only reason it leaves the verdict, not a way around the red",
                   "The job has 30 minutes (it had 60)", "TIME_BUDGET_SECONDS is 20 minutes (it was 49)", "took 4 to 11 minutes"):
        assert needle in header, needle
    assert header.index("Round 8") > header.index("Round 7") > header.index("Round 5")


def test_the_header_no_longer_asks_for_any_restored_window_step_of_a_launch():
    run_scale, plan = load_window_geometry("run_scale"), load_window_geometry("plan")
    for needle in ("Juggler's whole sequence", "steps juggler", "a grid of restored windows and the minimum", "launch n"):
        assert needle not in " ".join(run_scale.COMMAND_IN_WORDS.split()), needle
    assert "grid" not in run_scale.PROOF_RULE and "launch n" not in run_scale.PROOF_RULE
    assert not (set(plan.COMPARED_STEPS) & {"juggler", "restored", "iframes_juggler"})


def test_the_header_says_the_top_page_and_the_popups_record_their_early_reads_and_that_none_of_it_is_judged():
    header = " ".join(line.lstrip("# ").strip() for line in TEXT.split("\non:")[0].splitlines())
    for needle in ("Round 8 also gives the page the launch opens (page.html) the same first inline script as surface.html",
                   "the very first thing in its head", "window.__early", "The maximized step records it as `content.early`",
                   "so does each popup", "next to the settled reading and the window's client origin", "`now` left out",
                   "none of it is judged"):
        assert needle in header, needle
    run_scale = load_window_geometry("run_scale")
    assert "what the page read at parse, DOMContentLoaded, the first and second animation frame and load" in " ".join(run_scale.COMMAND_IN_WORDS.split())
