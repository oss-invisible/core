"""Nothing is uploaded to the index unless the wrapper's main passes on the TAGGED commit of this core.

The wrapper pins this package and is published after it. ci.yml runs the wrapper's e2e (`wrapper-on-this-core`) and its whole unit suite
(`wrapper-unit-on-this-core`) on this core, but on pull requests and pushes to main, never on a tag: a tag on a commit whose run on main was
skipped or red could publish. publish.yml now has a `proof` job that runs both on `github.sha`, and `upload` needs it.

The two jobs are written once: each lives in a reusable workflow (`wrapper-on-this-core.yml`, `wrapper-unit-on-this-core.yml`) that ci.yml
calls where `wrapper-job-needed` says and that `wrapper-proof.yml` calls for publish.yml, with no decision in between (a tag is always
required). These are structural assertions over files, as tests/test_release_wiring.py is: a run on GitHub cannot be made here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
E2E = "wrapper-on-this-core"
UNIT = "wrapper-unit-on-this-core"
FILE_OF = {E2E: "wrapper-on-this-core.yml", UNIT: "wrapper-unit-on-this-core.yml"}


def _load(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.exists(), f"{name} does not exist"
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in spec and "on" not in spec:  # PyYAML reads the key `on` as True
        spec["on"] = spec.pop(True)
    return spec


def _flat(expr) -> str:
    return re.sub(r"\s+", "", str(expr))


def _needs(job: dict) -> list:
    needs = job.get("needs")
    return [needs] if isinstance(needs, str) else list(needs or [])


def _called(job: dict) -> str:
    uses = str(job.get("uses", ""))
    assert uses.startswith("./.github/workflows/"), f"not a call of a workflow of this repository: {uses!r}"
    return uses.rsplit("/", 1)[1]


# ------------------------------------------------------------ publish.yml: what upload waits for

def test_upload_needs_the_proof_job():
    jobs = _load("publish.yml")["jobs"]
    assert "proof" in jobs, "publish.yml has no `proof` job: the tag is published on the strength of the core's own tests alone"
    needs = _needs(jobs["upload"])
    for name in ("already-published", "gate", "proof"):
        assert name in needs, f"upload needs {needs!r}, which does not include `{name}`"


def test_the_proof_job_is_the_wrapper_proof_and_runs_without_a_condition_that_could_skip_it_on_a_tag():
    jobs = _load("publish.yml")["jobs"]
    proof = jobs.get("proof")
    assert proof, "publish.yml has no `proof` job"
    assert _called(proof) == "wrapper-proof.yml", "`proof` is not a call of wrapper-proof.yml"
    assert "steps" not in proof, "`proof` carries steps of its own: the wrapper's jobs must be the ones ci.yml calls"
    # The only `if:` it may have is `upload`'s, word for word: skipped exactly where `upload` is skipped (a version already on the
    # index). Nothing about an event, a ref, a tag, or `always()`/`failure()`.
    cond = _flat(proof.get("if", ""))
    assert cond in ("", _flat(jobs["upload"].get("if", ""))), f"`proof` has its own condition: {proof.get('if')!r}"
    for word in ("github.event", "github.ref", "always", "failure", "cancelled", "success()"):
        assert word not in cond, f"`proof` has a condition that names {word}: {proof.get('if')!r}"
    assert not proof.get("continue-on-error"), "a red proof would not stop the upload (continue-on-error)"
    assert "wrapper-job-needed" not in _needs(proof) and "wrapper-job-needed" not in str(proof), (
        "`proof` asks `wrapper-job-needed`, a question for a diff: a tag is always required")


def test_a_skipped_proof_cannot_let_upload_run():
    """`upload` needs `proof` and has no `always()`: a skipped or failed need is an upload that never starts, so the only way
    upload runs is a proof that SUCCEEDED."""
    upload = _load("publish.yml")["jobs"]["upload"]
    assert "proof" in _needs(upload), "upload does not wait for `proof`: the default `success()` of a need is what keeps it from running"
    cond = _flat(upload.get("if", ""))
    for word in ("always", "failure", "cancelled", "proof"):
        assert word not in cond, f"upload's condition names {word}: a skipped or red need could still let it run"


def test_the_proof_runs_both_wrapper_jobs_through_the_files_ci_calls():
    proof_file = _load("wrapper-proof.yml")
    assert list(proof_file["on"]) == ["workflow_call"], "wrapper-proof.yml can be started on its own"
    jobs = proof_file["jobs"]
    assert sorted(_called(j) for j in jobs.values()) == sorted(FILE_OF.values()), (
        "wrapper-proof.yml does not run both the e2e and the whole unit suite of the wrapper")
    for name, job in jobs.items():
        assert "if" not in job, f"`{name}` of wrapper-proof.yml has a condition: it could be skipped on a tag"
        assert "needs" not in job, f"`{name}` of wrapper-proof.yml waits for something that could be skipped"
        assert not job.get("continue-on-error"), f"`{name}` of wrapper-proof.yml can be red without stopping the upload"


# ------------------------------------------------------------ the jobs run on the tagged commit

@pytest.mark.parametrize("job_name", [E2E, UNIT])
def test_a_wrapper_job_runs_this_cores_tagged_commit_and_the_wrappers_main(job_name):
    spec = _load(FILE_OF[job_name])
    assert list(spec["on"]) == ["workflow_call"], f"{FILE_OF[job_name]} can be started on its own"
    job = spec["jobs"][job_name]
    assert "if" not in job and "needs" not in job, "the called job carries a decision: the caller decides, a tag always runs it"
    steps = job["steps"]
    # The core: the first checkout, with no repository and no ref of its own, is the commit of the caller's event (`github.sha`: for a
    # tag, the commit the tag names).
    first = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert not (first.get("with") or {}).get("repository") and not (first.get("with") or {}).get("ref"), (
        "the core is not checked out at the commit of the event (`github.sha`)")
    assert (first.get("with") or {}).get("fetch-depth") == 0, "origin/main is not fetched: the list of main cannot be read"
    # The wrapper: chosen by the script, which gives main for an event that is not a pull request (a tag has no body).
    chooser = next(s for s in steps if "wrapper_on_this_core.py wrapper-ref" in str(s.get("run", "")))
    assert "--event" in chooser["run"] and "github.event_name" in str((chooser.get("env") or {}).get("EVENT", ""))
    wrapper = [s for s in steps if (s.get("with") or {}).get("repository") == "oss-invisible/playwright"]
    assert len(wrapper) == 1 and _flat(wrapper[0]["with"]["ref"]) == f"${{{{steps.{chooser['id']}.outputs.ref}}}}"
    # THIS checkout's core goes over the wrapper's pin.
    text = "\n".join(str(s.get("run", "")) for s in steps)
    assert "python -m pip install --force-reinstall --no-deps ." in text, "this core is not installed over the wrapper's pin"
    # No test is allowed to be silently dropped on the way.
    assert not any(s.get("continue-on-error") for s in steps), "a step of the job can be red without the job being red"
    # A called workflow does not see the caller's `env`: what ci.yml sets there is set here.
    env = spec.get("env") or {}
    assert "STEALTHFOX_SECURITY_STATE" in env and "GITHUB_TOKEN" in env, (
        "the called workflow does not set the security state and the token the caller's `env` gave the jobs")


def test_the_list_of_tests_is_compared_on_a_tag_with_the_list_of_main():
    """On a tag the `before` of a push is zeros and the compare step used to refuse an event it did not know."""
    steps = _load(FILE_OF[E2E])["jobs"][E2E]["steps"]
    run = next(s for s in steps if s.get("name") == "the list of tests is not shorter than main's")["run"]
    assert "github.ref_type" in run and re.search(r'"tag"\s*\]\s*;\s*then\s+BASE="origin/main"', run), (
        "a push of a tag does not compare with origin/main")
    assert re.search(r"\*\)[^\n]*exit 1", run), "an unknown event must stay red"


# ------------------------------------------------------------ ci.yml calls the same text

@pytest.mark.parametrize("job_name", [E2E, UNIT])
def test_ci_calls_the_same_jobs_where_the_decision_says_so(job_name):
    ci = _load("ci.yml")["jobs"]
    job = ci[job_name]
    assert _called(job) == FILE_OF[job_name], f"ci.yml's `{job_name}` is not a call of {FILE_OF[job_name]}: two texts of one job"
    assert "steps" not in job and "strategy" not in job, f"ci.yml still carries a copy of `{job_name}`"
    assert _needs(job) == ["wrapper-job-needed"]
    assert _flat(job["if"]) == "needs.wrapper-job-needed.outputs.run=='true'"
    assert job_name in _needs(ci["gate"]), f"`gate` does not wait for `{job_name}`"
