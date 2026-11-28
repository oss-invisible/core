"""ci.yml is ready for a GitHub merge queue, and a queue run is judged as a pull request is.

With a merge queue GitHub builds the merge result on a branch of its own
(`gh-readonly-queue/...`) and runs the workflows that listen to `merge_group`; the
required context is `gate` of ci.yml, the one verdict. Three things would break that
without a sound: the workflow does not listen (the queue waits for ever for a check that
never starts), a job that decides by the event (`pull_request` or `push`) falls into its
"anything else" branch (a skip that reads as "not needed", or a decision made on an empty
base), and a run of the queue is cancelled by another one.

What is held here, on the files themselves:

  1. ci.yml listens to `merge_group` (`checks_requested`) and still to pull requests and
     to pushes to main.
  2. A merge group run is keyed on its own ref and is never cancelled; a pull request run
     still cancels the one it supersedes, and a push to main is still unique to its run.
  3. No `if:` and no script of ci.yml (or of the workflow it calls) names `pull_request`
     or `push` as an event without handling `merge_group` too; no step reads
     `github.event.pull_request.*` without a `merge_group` branch (static scan).
  4. `wrapper-job-needed` decides from the files between the base and the head of the
     merge group with the same rule as for a pull request: the same changed files give the
     same answer, in the function and on a real git diff; an event the rule does not know
     is red, never "not needed".
  5. `gate` names no event: what it requires follows `wrapper-job-needed`, so it holds in a
     queue run as on a pull request.
  6. The reusable noreply-identity workflow judges a merge group as a pull request, and its
     own `push` trigger leaves the queue's branches to ci.yml (one run per commit).

Every checker is also run on a known-bad input first: a check that has only ever said
"fine" is not known to be able to say anything else.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
DECIDER = ROOT / "scripts" / "wrapper_job_needed.py"
CHOOSER = ROOT / "scripts" / "wrapper_on_this_core.py"
#: ci.yml and the reusable workflows it calls: all run in a queue run.
IN_THE_QUEUE_RUN = ("ci.yml", "noreply-identity.yml", "wrapper-on-this-core.yml", "wrapper-unit-on-this-core.yml")


def _flat(expr) -> str:
    return re.sub(r"\s+", "", str(expr))


def _load(text: str) -> dict:
    spec = yaml.safe_load(text)
    if True in spec and "on" not in spec:  # PyYAML reads the key `on` as True
        spec["on"] = spec.pop(True)
    return spec


def _spec(name: str = "ci.yml") -> dict:
    return _load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _script(path: Path, alias: str):
    assert path.exists(), f"{path} does not exist"
    spec = importlib.util.spec_from_file_location(alias, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ 1 and 2: the triggers

def trigger_problems(spec: dict) -> list[str]:
    on = spec.get("on") or {}
    out = []
    merge_group = on.get("merge_group", "absent")
    if merge_group == "absent":
        out.append("the workflow does not listen to `merge_group`: a merge queue would wait for ever for `gate`")
    elif (merge_group or {}).get("types") != ["checks_requested"]:
        out.append(f"`merge_group` is listened to with types {(merge_group or {}).get('types')!r}, not [checks_requested]")
    if "pull_request" not in on or (on.get("push") or {}).get("branches") != ["main"]:
        out.append("the workflow no longer runs on pull requests and on pushes to main")
    return out


def _evaluate_cancel(expr: str, event: str) -> bool:
    """`${{ github.event_name == 'X' }}` (the only shape a cancel may have) for ``event``."""
    flat = _flat(expr)
    m = re.fullmatch(r"\$\{\{github\.event_name=='(\w+)'\}\}", flat)
    if flat in ("true", "True"):
        return True
    if flat in ("false", "False"):
        return False
    assert m, f"cancel-in-progress is not a plain comparison of the event: {expr!r}"
    return m.group(1) == event


def concurrency_problems(spec: dict) -> list[str]:
    conc = spec.get("concurrency")
    if not isinstance(conc, dict):
        return ["no concurrency group"]
    group, cancel = _flat(conc.get("group", "")), conc.get("cancel-in-progress")
    out = []
    if "github.event_name=='merge_group'&&github.ref||" not in group:
        out.append("a merge group run is not keyed on github.ref (one branch per queue entry)")
    try:
        if _evaluate_cancel(cancel, "merge_group"):
            out.append("a merge group run is cancelled in progress")
        if not _evaluate_cancel(cancel, "pull_request"):
            out.append("a pull request run no longer cancels the one it supersedes")
        if _evaluate_cancel(cancel, "push"):
            out.append("a push to main is cancelled in progress")
    except AssertionError as exc:
        out.append(str(exc))
    if "github.event.pull_request.number" not in group or "github.run_id" not in group:
        out.append("the group of a pull request or of a push (the run id) changed")
    return out


OLD_TRIGGERS = """
name: ci
on:
  push:
    branches: [main]
  pull_request:
concurrency:
  group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && format('pr-{0}', github.event.pull_request.number) || github.run_id }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
"""
CANCEL_EVERYTHING = """
name: ci
on:
  push:
    branches: [main]
  pull_request:
  merge_group:
    types: [checks_requested]
concurrency:
  group: ${{ github.workflow }}-${{ github.event_name == 'merge_group' && github.ref || github.run_id }}
  cancel-in-progress: true
"""


def test_the_checkers_of_the_triggers_can_fail():
    old = _load(OLD_TRIGGERS)
    assert any("does not listen" in p for p in trigger_problems(old))
    assert any("not keyed on github.ref" in p for p in concurrency_problems(old))
    bad = _load(CANCEL_EVERYTHING)
    assert trigger_problems(bad) == []
    found = concurrency_problems(bad)
    assert any("merge group run is cancelled" in p for p in found)
    assert any("push to main is cancelled" in p for p in found)
    assert any("pull request" in p and "changed" in p for p in found)


def test_ci_listens_to_merge_group_and_still_to_pull_requests_and_main():
    assert trigger_problems(_spec()) == []


def test_a_merge_group_run_is_keyed_on_its_ref_and_never_cancelled():
    assert concurrency_problems(_spec()) == []


# ------------------------------------------------------------------ 3: the static scan

#: `pull_request` or `push` named as an event, not as a part of `github.event.pull_request.*`.
_EVENT_NAMED = re.compile(
    r"""(?<![.\w])pull_request(?![\w])"""
    r"""|event_name\s*[=!]=\s*'push'"""
    r"""|\bEVENT\}?"?\s*=\s*"push\""""
    r"""|^\s*push[|)]""", re.MULTILINE)
_READS_PULL_REQUEST = re.compile(r"github\.event\.pull_request\.")


def _strings(node) -> list[str]:
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return [] if node is None else [str(node)]


def _scopes(spec: dict) -> list[tuple[str, str]]:
    """(where, text): the concurrency, every job's `if:` and `env`, every step as a whole."""
    out = [("concurrency", "\n".join(_strings(spec.get("concurrency"))))]
    for name, job in (spec.get("jobs") or {}).items():
        out.append((f"job {name}", "\n".join(_strings({k: v for k, v in job.items() if k != "steps"}))))
        for i, step in enumerate(job.get("steps") or []):
            out.append((f"job {name}, step {i + 1} ({step.get('name') or step.get('id') or step.get('uses') or 'run'})",
                        "\n".join(_strings(step))))
    return out


def merge_group_problems(workflow: str, spec: dict) -> list[str]:
    out = []
    for where, text in _scopes(spec):
        if "merge_group" in text:
            continue
        if _EVENT_NAMED.search(text):
            out.append(f"{workflow}: {where} names `pull_request` or `push` as an event and has no `merge_group` branch")
        elif _READS_PULL_REQUEST.search(text):
            out.append(f"{workflow}: {where} reads github.event.pull_request.* and has no `merge_group` branch")
    return out


OLD_DECIDER = """
name: ci
jobs:
  decide:
    runs-on: ubuntu-latest
    steps:
      - id: decide
        env:
          EVENT: ${{ github.event_name }}
          BASE_SHA: ${{ github.event.pull_request.base.sha }}
        run: |
          if [ "$EVENT" = "push" ]; then run=true; else git diff --name-only "$BASE_SHA...HEAD"; fi
  later:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - run: |
          case "$EVENT" in
            pull_request|pull_request_target) echo pr ;;
            push) echo push ;;
          esac
"""
HANDLED = """
name: ci
jobs:
  decide:
    runs-on: ubuntu-latest
    steps:
      - id: decide
        env:
          EVENT: ${{ github.event_name }}
          BASE_SHA: ${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha }}
        run: |
          case "$EVENT" in
            push) echo push ;;
            pull_request|merge_group) git diff --name-only "$BASE_SHA...HEAD" ;;
          esac
  later:
    if: github.event_name == 'pull_request' || github.event_name == 'merge_group'
    runs-on: ubuntu-latest
    steps:
      - run: echo
"""


def test_the_scan_can_fail():
    found = merge_group_problems("old.yml", _load(OLD_DECIDER))
    assert len(found) == 3, found  # the step that decides, the `if:` of the job, the case of the last step
    assert any("step 1" in p for p in found) and any("job later" in p for p in found)
    assert merge_group_problems("good.yml", _load(HANDLED)) == []


@pytest.mark.parametrize("workflow", IN_THE_QUEUE_RUN)
def test_no_condition_or_script_branches_on_the_event_without_handling_merge_group(workflow):
    assert merge_group_problems(workflow, _spec(workflow)) == []


# ------------------------------------------------- 4: wrapper-job-needed, the same rule for both

def _decider():
    return _script(DECIDER, "wrapper_job_needed_under_test")


UNRELATED = ["README.md", "docs/a.md", "tests/test_branding_source.py", "scripts/other.py"]
#: What the installed core is made of (ci-12): the same rule in a pull request and in a merge group.
CORE = ["src/invisible_core/config.py"]


def _needed(mod, changed) -> bool:
    return any(p in mod.JOB_FILES or mod.core_change(p) for p in changed)
CASES = [
    pytest.param([], id="nothing"),
    pytest.param(UNRELATED, id="unrelated"),
    pytest.param([".github/workflows/ci.yml"], id="ci.yml"),
    pytest.param(["scripts/wrapper_on_this_core.py"], id="the wrapper script"),
    pytest.param(["scripts/wrapper_job_needed.py"], id="the decider"),
    pytest.param(["scripts/wrapper_test_seconds.json"], id="the table of seconds"),
    pytest.param(["tests/test_the_wrapper_job_runs_on_the_core.py"], id="the job's test"),
    pytest.param(CORE, id="src only"),
    pytest.param(["pyproject.toml"], id="pyproject only"),
    pytest.param(["docs/a.md"], id="docs only"),
    pytest.param(UNRELATED + ["scripts/wrapper_test_seconds.json"], id="one among others"),
]


@pytest.mark.parametrize("changed", CASES)
def test_the_same_changed_files_give_the_same_answer_in_a_pull_request_and_in_a_merge_group(changed):
    mod = _decider()
    pull_request, _ = mod.decide("pull_request", head_ref="feature/x", body="", changed=changed)
    merge_group, _ = mod.decide("merge_group", changed=changed)
    assert pull_request == merge_group
    assert merge_group == _needed(mod, changed)


def test_in_a_merge_group_a_src_only_diff_requires_the_job_and_a_docs_only_diff_does_not():
    mod = _decider()
    assert mod.decide("merge_group", changed=CORE)[0] is True
    assert mod.decide("merge_group", changed=["docs/a.md", "README.md"])[0] is False


def test_a_queue_run_asks_for_the_files_only_and_a_push_always_runs():
    mod = _decider()
    # no head branch and no body in a queue run: they are the pull request's, judged on its own run
    assert mod.decide("merge_group", head_ref="release/1", body="wrapper-ref: x", changed=UNRELATED)[0] is False
    assert mod.decide("merge_group", head_ref="release/1", body="wrapper-ref: x", changed=CORE)[0] is True
    assert mod.decide("pull_request", head_ref="release/1", changed=UNRELATED)[0] is True
    assert mod.decide("pull_request", body="WRAPPER-REF : x", changed=UNRELATED)[0] is True
    assert mod.decide("push", changed=[])[0] is True


@pytest.mark.parametrize("event", ["", "workflow_dispatch", "schedule", "pull_request_target", "merge-group", "merge_group "])
def test_an_unknown_event_is_red_never_not_needed(event, tmp_path):
    mod = _decider()
    with pytest.raises(mod.UnknownEvent):
        mod.decide(event, changed=UNRELATED)
    out = tmp_path / "out.txt"
    env = {"GITHUB_OUTPUT": str(out)}
    done = subprocess.run([sys.executable, str(DECIDER)], capture_output=True, text=True,
                          env={**os.environ, **env, "EVENT": event or "none"})
    assert done.returncode == 1 and "RED" in done.stderr
    assert not out.exists(), "a red decision wrote an output the `gate` could read as 'not needed'"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@users.noreply.github.com",
                           "-c", "commit.gpgsign=false", *args], cwd=repo, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _repo_with(tmp_path: Path, changed: list[str]) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    for rel in changed or ["something_else.txt"]:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _run_cli(repo: Path, out: Path, event: str, base: str, head: str, head_ref: str = ""):
    """The script as the step runs it: everything comes through the environment."""
    return subprocess.run([sys.executable, str(DECIDER)], cwd=repo, capture_output=True, text=True,
                          env={**os.environ, "GITHUB_OUTPUT": str(out), "EVENT": event, "HEAD_REF": head_ref,
                               "BASE_SHA": base, "HEAD_SHA": head, "PR_BODY": ""})


@pytest.mark.parametrize("changed", [UNRELATED[:1], CORE, [".github/workflows/ci.yml"], ["scripts/wrapper_test_seconds.json"]])
def test_on_a_real_diff_the_pull_request_and_the_merge_group_get_the_same_output(changed, tmp_path):
    repo, base, head = _repo_with(tmp_path, changed)
    answers = {}
    for event, head_ref in (("pull_request", "feature/x"), ("merge_group", "")):
        out = tmp_path / f"{event}.out"
        done = _run_cli(repo, out, event, base, head, head_ref)
        assert done.returncode == 0, done.stderr
        answers[event] = out.read_text(encoding="utf-8")
    assert answers["pull_request"] == answers["merge_group"]
    assert answers["merge_group"] in ("run=true\n", "run=false\n")
    assert (answers["merge_group"] == "run=true\n") == _needed(_decider(), changed)


def test_a_diff_that_cannot_be_read_is_red_in_both_events(tmp_path):
    repo, base, head = _repo_with(tmp_path, UNRELATED[:1])
    for event in ("pull_request", "merge_group"):
        out = tmp_path / f"{event}.out"
        unknown = _run_cli(repo, out, event, "0" * 40, head)
        assert unknown.returncode == 1 and "cannot be read" in unknown.stderr, event
        missing = _run_cli(repo, out, event, "", head)
        assert missing.returncode == 1, event
        assert not out.exists(), "a red decision wrote an output"


def test_the_step_that_decides_takes_the_base_and_the_head_of_the_pull_request_or_the_merge_group():
    job = _spec()["jobs"]["wrapper-job-needed"]
    step = next(s for s in job["steps"] if s.get("id") == "decide")
    env = {k: _flat(v) for k, v in (step.get("env") or {}).items()}
    assert env["EVENT"] == "${{github.event_name}}"
    assert "github.event.pull_request.base.sha" in env["BASE_SHA"] and "github.event.merge_group.base_sha" in env["BASE_SHA"]
    assert "github.event.pull_request.head.sha" in env["HEAD_SHA"] and "github.event.merge_group.head_sha" in env["HEAD_SHA"]
    run = step["run"]
    assert "scripts/wrapper_job_needed.py" in run
    assert set(env) >= {"EVENT", "HEAD_REF", "BASE_SHA", "HEAD_SHA", "PR_BODY"}
    assert not re.search(r"\$\{\{[^}]*pull_request\.body", run), "the body is untrusted: env only"
    assert any((s.get("with") or {}).get("fetch-depth") == 0 for s in job["steps"]), "the diff needs the history"


# ------------------------------------------------- the wrapper's branch, the list of main's, the gate

def test_a_merge_group_run_tries_the_wrappers_main_and_reads_no_body():
    mod = _script(CHOOSER, "wrapper_on_this_core_under_test")

    def never(_branch):
        raise AssertionError("a queue run asked the wrapper's repository for a branch")
    ref, why = mod.choose_wrapper_ref("merge_group", "wrapper-ref: some/branch", ls_remote=never)
    assert ref == "main" and "merge_group" in why and "no pull request body" in why


def test_the_list_of_tests_compares_with_main_in_a_queue_run_and_refuses_an_unknown_event():
    steps = _spec("wrapper-on-this-core.yml")["jobs"]["wrapper-on-this-core"]["steps"]
    step = next(s for s in steps if s.get("name") == "the list of tests is not shorter than main's")
    run = step["run"]
    assert re.search(r"pull_request\|merge_group(\|workflow_dispatch)?\)\s+BASE=\"origin/main\"", run), "a queue run compares with origin/main as a pull request does"
    assert re.search(r"\*\)[^\n]*exit 1", run), "an unknown event must be red here, not compared with origin/main"


def test_gate_names_no_event_and_follows_wrapper_job_needed_in_every_run():
    jobs = _spec()["jobs"]
    gate, job = jobs["gate"], jobs["wrapper-on-this-core"]
    text = "\n".join(_strings(gate))
    assert "event_name" not in text and "pull_request" not in text and "merge_group" not in text
    env = {k: v for s in gate["steps"] for k, v in (s.get("env") or {}).items()}
    assert re.sub(r"^\$\{\{|\}\}$", "", _flat(env["WRAPPER_JOB_REQUIRED"])) == re.sub(r"^\$\{\{|\}\}$", "", _flat(job["if"]))
    assert _flat(job["if"]) == "needs.wrapper-job-needed.outputs.run=='true'"
    run = "\n".join(s.get("run", "") for s in gate["steps"])
    assert 'test "${{ needs.wrapper-job-needed.result }}" = "success"' in run, "a decision that failed must not read as a skip"
    assert 'test "${{ needs.wrapper-on-this-core.result }}" = "success"' in run
    assert gate["if"] == "always()"
    # and in a queue run the decision that feeds it is made by the rule: required where the job changes, a skip elsewhere
    mod = _decider()
    assert mod.decide("merge_group", changed=[".github/workflows/ci.yml"])[0] is True
    assert mod.decide("merge_group", changed=UNRELATED)[0] is False
    assert mod.decide("merge_group", changed=CORE)[0] is True
    assert _flat(jobs["wrapper-unit-on-this-core"]["if"]) == _flat(job["if"]), "both wrapper jobs apply the same decision"
    for needed in ("noreply-identity", "tests", "wrapper-job-needed", "wrapper-on-this-core"):
        assert needed in gate["needs"]


# ------------------------------------------------- 6: the reusable workflow

def noreply_problems(spec: dict) -> list[str]:
    out = []
    ignored = ((spec.get("on") or {}).get("push") or {}).get("branches-ignore") or []
    if "gh-readonly-queue/**" not in ignored:
        out.append("its `push` trigger also runs on the branches of the merge queue, which ci.yml's merge_group run covers")
    steps = spec["jobs"]["noreply-identity"]["steps"]
    judge = next(s for s in steps if s.get("name") == "every address is a GitHub noreply one")
    run = str(judge.get("run", ""))
    m = re.search(r"merge_group\)(.*?);;", run, re.DOTALL)
    if not m or "--event pull_request" not in m.group(1) or "MG_BASE_SHA" not in m.group(1) or "MG_HEAD_SHA" not in m.group(1):
        out.append("a merge group is not judged as a pull request: what the group adds to its base")
    env = {k: _flat(v) for k, v in (judge.get("env") or {}).items()}
    for key, field in (("MG_BASE_SHA", "base_sha"), ("MG_HEAD_SHA", "head_sha"), ("MG_BASE_REF", "base_ref")):
        if env.get(key) != "${{github.event.merge_group." + field + "}}":
            out.append(f"{key} does not come from github.event.merge_group.{field}")
    return out


def test_the_noreply_checker_can_fail():
    old = _load("""
name: noreply-identity
on:
  workflow_call:
  push:
    branches-ignore: [main]
jobs:
  noreply-identity:
    runs-on: ubuntu-latest
    steps:
      - name: every address is a GitHub noreply one
        env:
          EVENT: ${{ github.event_name }}
        run: |
          case "$EVENT" in
            pull_request|pull_request_target) echo pr ;;
            *) echo other ;;
          esac
""")
    assert len(noreply_problems(old)) >= 3


def test_the_reusable_noreply_workflow_judges_a_merge_group_as_a_pull_request():
    assert noreply_problems(_spec("noreply-identity.yml")) == []
