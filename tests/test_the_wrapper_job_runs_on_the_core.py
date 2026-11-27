"""The wrapper's tests run on the core about to be published, and a release of the
core cannot go through without them.

The wrapper pins this package and is published after it, so the wrapper's CI
installs the PINNED core from the index: until `wrapper-on-this-core` existed, no
test of the wrapper that stands on the core's Juggler client (a Lifecycle, an
InjectedScript, a Connection, the protocol) ran on the code that was about to be
released.

What is held here, on the files themselves and on the function that computes the
list of tests:

  1. `ci.yml` has the job, on a release pull request (head branch `release/*`),
     on a push to main and on a pull request that touches the job itself (the
     small job `wrapper-job-needed` decides, and is run here on a real diff), on
     Linux in two parts and on
     Windows in four, each leg told how many parts there are, with the pin left alone (`INVISIBLE_CORE_PIN=allow`,
     `INVISIBLE_CORE_AUTOFIX=off`) and the core installed over it.
  2. The single verdict (`gate`) needs it, under the very condition the job has:
     required, and green, where the job applies; skipped where it does not.
  3. The list of tests is computed, never typed, and it is not empty: the grep
     finds the files of a sample wrapper tree that import the Juggler client or
     build one of its objects by hand, and only those. A list that got shorter
     than main's is red; an empty one is red.
  4. Every exclusion has a witness that its reason still holds, and a witness that
     no longer holds is red naming the entry.
  5. A run is red on a failure, on a SKIPPED test, on a part that ran no file it
     was given, and on a run that ran nothing; a test that passed only at a later
     attempt (a testcase skipped with type `pytest.rerun`) is a problem, not a pass
     (ci-13).
  6. The wrapper the job installs is its `main`, or, on a pull request whose body has
     the line `wrapper-ref: <branch>`, that branch (it must exist; a bad line, two
     lines or a missing branch is red; a push and a tag are always `main`). The body
     reaches the script through `env:`, never through an interpolation.

Every check is also run on a known-bad input first: a check that has only ever
said "fine" is not known to be able to say anything else.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / "scripts" / "wrapper_on_this_core.py"
NEEDED = ROOT / "scripts" / "wrapper_job_needed.py"
JOB = "wrapper-on-this-core"
UNIT_JOB = "wrapper-unit-on-this-core"
CALLED = {"wrapper-on-this-core": ROOT / ".github" / "workflows" / "wrapper-on-this-core.yml",
          "wrapper-unit-on-this-core": ROOT / ".github" / "workflows" / "wrapper-unit-on-this-core.yml"}
JOB_FILES = (".github/workflows/ci.yml", ".github/workflows/wrapper-on-this-core.yml",
             ".github/workflows/wrapper-unit-on-this-core.yml", ".github/workflows/wrapper-proof.yml",
             "scripts/wrapper_on_this_core.py",
             "scripts/wrapper_test_seconds.json", "scripts/wrapper_job_needed.py",
             "tests/test_the_wrapper_job_runs_on_the_core.py", "tests/test_wrapper_job_needed.py")


def _load_script():
    # By path, as a module of its own: `scripts/` is not a package.
    assert SCRIPT.exists(), f"{SCRIPT} does not exist: nothing computes the list of wrapper tests"
    spec = importlib.util.spec_from_file_location("wrapper_on_this_core_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _flat(expr) -> str:
    return re.sub(r"\s+", "", str(expr))


def _bare(expr) -> str:
    """An expression without its `${{ }}` and whitespace."""
    return re.sub(r"^\$\{\{|\}\}$", "", _flat(expr))


def _spec() -> dict:
    spec = yaml.safe_load(CI.read_text(encoding="utf-8"))
    if True in spec and "on" not in spec:
        spec["on"] = spec.pop(True)
    # The two wrapper jobs live in reusable workflows that ci.yml calls (and publish.yml, on the tag, through wrapper-proof.yml): the
    # caller keeps `needs` and `if`, the called file the rest. They are read here as the one job they make, so every check below holds
    # the text that runs.
    for name, path in CALLED.items():
        caller = (spec.get("jobs") or {}).get(name) or {}
        if caller.get("uses") == f"./.github/workflows/{path.name}":
            called = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"][name]
            spec["jobs"][name] = {**called, "needs": caller.get("needs"), "if": caller.get("if")}
    return spec


# ------------------------------------------------------------------ the workflow

def job_problems(spec: dict) -> list[str]:
    out = []
    jobs = spec.get("jobs") or {}
    job = jobs.get(JOB)
    if not isinstance(job, dict):
        return [f"ci.yml has no job `{JOB}`"]
    if _flat(job.get("if")) != "needs.wrapper-job-needed.outputs.run=='true'" \
            or "wrapper-job-needed" not in (job.get("needs") or []):
        out.append("the job does not run where `wrapper-job-needed` says (its output `run`)")
    selector = jobs.get("wrapper-job-needed")
    decide = next((x for x in ((selector or {}).get("steps") or []) if x.get("id") == "decide"), None)
    if not selector or not decide:
        out.append("ci.yml has no job `wrapper-job-needed` that decides")
    else:
        if "run" not in (selector.get("outputs") or {}):
            out.append("`wrapper-job-needed` has no output `run`")
        if not any((x.get("with") or {}).get("fetch-depth") == 0 for x in selector["steps"]):
            out.append("`wrapper-job-needed` does not fetch the history it diffs")
        text = str(decide.get("run", ""))
        # The rules (a push, release/*, the body, src/, pyproject.toml, the locks, the job's own files) are in the script, held by
        # tests/test_wrapper_job_needed.py and, run for real on a diff, below.
        if "scripts/wrapper_job_needed.py" not in text:
            out.append("`wrapper-job-needed` does not decide with scripts/wrapper_job_needed.py")
        for name in ("EVENT", "HEAD_REF", "BASE_SHA", "HEAD_SHA", "PR_BODY"):
            if name not in (decide.get("env") or {}):
                out.append(f"`wrapper-job-needed` does not give the script {name}")
        if "label" in text or "labels" in str(decide.get("env", "")):
            out.append("the job still looks at a label")
    on = spec.get("on") or {}
    if "pull_request" not in on or "main" not in ((on.get("push") or {}).get("branches") or []):
        out.append("ci.yml no longer runs on pull requests and on pushes to main")
    include = ((job.get("strategy") or {}).get("matrix") or {}).get("include") or []
    legs = sorted((m.get("os"), m.get("part"), m.get("parts")) for m in include)
    if legs != [("linux", 1, 2), ("linux", 2, 2), ("windows", 1, 4), ("windows", 2, 4),
                ("windows", 3, 4), ("windows", 4, 4)]:
        out.append(f"the legs are {legs}, not Linux in two parts and Windows in four")
    env = job.get("env") or {}
    if str(env.get("INVISIBLE_CORE_PIN")) != "allow" or str(env.get("INVISIBLE_CORE_AUTOFIX")) != "off":
        out.append("the pin is not left alone (INVISIBLE_CORE_PIN=allow, INVISIBLE_CORE_AUTOFIX=off)")
    steps = job.get("steps") or []
    text = "\n".join(str(s.get("run", "")) for s in steps)
    checkouts = [s for s in steps if (s.get("with") or {}).get("repository") == "oss-invisible/playwright"]
    chooser = [s for s in steps if "wrapper_on_this_core.py wrapper-ref" in str(s.get("run", ""))]
    if len(checkouts) != 1:
        out.append("the wrapper is not checked out exactly once")
    elif (len(chooser) != 1 or not chooser[0].get("id")
          or _bare(checkouts[0]["with"].get("ref")) != f"steps.{chooser[0]['id']}.outputs.ref"
          or steps.index(chooser[0]) > steps.index(checkouts[0])):
        out.append("the wrapper's checkout does not take its ref from the output of the step that chooses it")
    for needle, what in (("pip install --force-reinstall --no-deps .", "this core is not installed over the pin"),
                         ("wrapper_on_this_core.py compare", "the list is not compared with main's"),
                         ("wrapper_on_this_core.py\" engine", "the engine of the seal is not fetched and checked"),
                         ("wrapper_on_this_core.py run", "the tests are not run")):
        if needle not in text:
            out.append(what)
    # This core is installed BEFORE the wrapper: a declared wrapper branch pins this release, which the index does not
    # have yet, and pip must find it installed (run 44651554169: "No matching distribution found for invisible-core==67.78.0").
    install = next((str(s.get("run", "")) for s in steps if 'pip install ".[dev]"' in str(s.get("run", ""))), "")
    lines = [line.strip() for line in install.splitlines()]
    first_core = next((i for i, line in enumerate(lines) if line == "python -m pip install ."), None)
    wrapper_line = next((i for i, line in enumerate(lines) if 'pip install ".[dev]"' in line), None)
    if first_core is None or wrapper_line is None or first_core > wrapper_line:
        out.append("this core is not installed before the wrapper (a declared wrapper branch pins a core the index does not have yet)")
    run_step = next((s for s in steps if "wrapper_on_this_core.py run" in str(s.get("run", ""))), {})
    if run_step.get("timeout-minutes") != 12:
        out.append("the test step is not held to 12 minutes")
    if '--parts "${{ matrix.parts }}"' not in str(run_step.get("run", "")):
        out.append("a leg is not told how many parts there are (--parts from the matrix)")
    if "xvfb-run" not in text:
        out.append("Linux does not run under a virtual display")
    # The body of the pull request is untrusted: it reaches a script only through `env:`.
    for name in (JOB, "wrapper-job-needed"):
        for step in (jobs.get(name) or {}).get("steps") or []:
            if re.search(r"\$\{\{[^}]*pull_request\.body", str(step.get("run", ""))):
                out.append(f"the body of the pull request is interpolated in the `run:` of a step of `{name}`")
    if len(chooser) == 1:
        body_vars = [k for k, v in (chooser[0].get("env") or {}).items() if "github.event.pull_request.body" in str(v)]
        if not body_vars:
            out.append("the body of the pull request does not reach the chooser through `env:`")
        elif not any(f'"${k}"' in str(chooser[0].get("run", "")) for k in body_vars):
            out.append("the chooser step does not read the body from its environment variable")
    return out


def unit_job_problems(spec: dict) -> list[str]:
    """`wrapper-unit-on-this-core`: the wrapper's required unit suite, run as the wrapper's own unit job runs it, judged by the wrapper's main."""
    jobs = spec.get("jobs") or {}
    job = jobs.get(UNIT_JOB)
    if not isinstance(job, dict):
        return [f"ci.yml has no job `{UNIT_JOB}`"]
    out = []
    other = jobs.get(JOB) or {}
    if _flat(job.get("if")) != _flat(other.get("if")) or job.get("needs") != other.get("needs"):
        out.append(f"`{UNIT_JOB}` does not run under the `if:` and the `needs:` of `{JOB}`")
    legs = sorted((m.get("os"), m.get("runner")) for m in ((job.get("strategy") or {}).get("matrix") or {}).get("include") or [])
    if legs != [("linux", "ubuntu-24.04"), ("windows", "windows-2022")]:
        out.append(f"the legs are {legs}, not one on Linux and one on Windows")
    env = job.get("env") or {}
    if str(env.get("INVISIBLE_CORE_PIN")) != "allow" or str(env.get("INVISIBLE_CORE_AUTOFIX")) != "off":
        out.append("the pin is not left alone (INVISIBLE_CORE_PIN=allow, INVISIBLE_CORE_AUTOFIX=off)")
    if env.get("TEMP") != other.get("env", {}).get("TEMP") or env.get("TMP") != other.get("env", {}).get("TMP"):
        out.append("TEMP and TMP are not those of the other wrapper job (D:\\a\\_temp on Windows)")
    steps = job.get("steps") or []
    chooser = [s for s in steps if "wrapper_on_this_core.py wrapper-ref" in str(s.get("run", ""))]
    checkouts = [s for s in steps if (s.get("with") or {}).get("repository") == "oss-invisible/playwright"]
    if (len(checkouts) != 1 or len(chooser) != 1 or not chooser[0].get("id")
            or _bare(checkouts[0]["with"].get("ref")) != f"steps.{chooser[0]['id']}.outputs.ref"
            or steps.index(chooser[0]) > steps.index(checkouts[0])):
        out.append("the wrapper is not checked out once, at the ref the `wrapper-ref` step chooses")
    elif not any(f'"${k}"' in str(chooser[0].get("run", ""))
                 for k, v in (chooser[0].get("env") or {}).items() if "github.event.pull_request.body" in str(v)):
        out.append("the body of the pull request does not reach the chooser through `env:`")
    for s in steps:
        if re.search(r"\$\{\{[^}]*pull_request\.body", str(s.get("run", ""))):
            out.append("the body of the pull request is interpolated in a `run:`")
    text = "\n".join(str(s.get("run", "")) for s in steps)
    install = next((str(s.get("run", "")) for s in steps if 'pip install -e ".[dev]"' in str(s.get("run", ""))), "")
    lines = [line.strip() for line in install.splitlines()]
    first_core = next((i for i, line in enumerate(lines) if line == "python -m pip install ."), None)
    wrapper_line = next((i for i, line in enumerate(lines) if 'pip install -e ".[dev]"' in line), None)
    over = next((i for i, line in enumerate(lines) if line == "python -m pip install --force-reinstall --no-deps ."), None)
    if wrapper_line is None:
        out.append("the wrapper is not installed with its dev extra")
    elif first_core is None or first_core > wrapper_line or over is None or over < wrapper_line:
        out.append("this core is not installed before the wrapper and again over its pin")
    if ('ci_verdict.py args --required ci/required-suites.json --suite "wrapper.unit.all.$SUITE_OS" --os "$SUITE_OS" --with-marker'
            not in text):
        out.append("the selection is not the one the wrapper's `ci_verdict.py args` prints for wrapper.unit.all.<os>")
    run_step = next((s for s in steps if "-m pytest" in str(s.get("run", ""))), {})
    for needle, what in (("-p no:rerunfailures", "pytest does not run with -p no:rerunfailures"),
                         ("--junitxml=", "pytest writes no junit"), ('"${ARGS[@]}"', "pytest does not take the selection of `args`"),
                         ("-o addopts=''", "pytest keeps the addopts of the wrapper")):
        if needle not in str(run_step.get("run", "")):
            out.append(what)
    if (run_step.get("env") or {}).get("SUITE_OS") is None or _bare(run_step["env"]["SUITE_OS"]) != "matrix.os":
        out.append("the suite of a leg is not named by its os (SUITE_OS from the matrix)")
    if run_step.get("working-directory") != "wrapper":
        out.append("pytest does not run in the wrapper's checkout")
    judge = next((s for s in steps if "ci_verdict.py\" judge" in str(s.get("run", ""))), None)
    if judge is None:
        out.append("nothing judges the junit with ci_verdict.py")
    else:
        jt = str(judge["run"])
        if judge.get("working-directory") != "wrapper" or judge.get("if") != "always()":
            out.append("the judge does not run (always) in the wrapper's checkout")
        for needle, what in (("git show origin/main:scripts/ci_verdict.py", "the judge is not the wrapper main's ci_verdict.py"),
                             ("git show origin/main:ci/required-suites.json", "the list is not the wrapper main's required-suites.json"),
                             ('--main-required "$RUNNER_TEMP/main/required-suites.json"', "the judge is not told the list of main"),
                             ('--suite "wrapper.unit.all.$SUITE_OS"', "the judge does not judge wrapper.unit.all.<os>"),
                             ('--junit "$RUNNER_TEMP/unit-junit.xml"', "the judge does not read the junit of the run")):
            if needle not in jt:
                out.append(what)
        if "--required ci/required-suites.json" not in jt:
            out.append("the judge does not read the row of the wrapper's checkout")
    if not any("fetch" in str(s.get("run", "")) and "origin main" in str(s.get("run", "")) and s.get("working-directory") == "wrapper"
               for s in steps):
        out.append("the wrapper's origin/main is not fetched for the judge")
    return out


def gate_problems(spec: dict) -> list[str]:
    jobs = spec.get("jobs") or {}
    job, gate = jobs.get(JOB) or {}, jobs.get("gate") or {}
    out = []
    if JOB not in (gate.get("needs") or []):
        out.append(f"`gate` does not need `{JOB}`")
    if UNIT_JOB not in (gate.get("needs") or []):
        out.append(f"`gate` does not need `{UNIT_JOB}`")
    if "wrapper-job-needed" not in (gate.get("needs") or []):
        out.append("`gate` does not need `wrapper-job-needed`")
    steps = gate.get("steps") or []
    env = {k: v for s in steps for k, v in (s.get("env") or {}).items()}
    if _bare(env.get("WRAPPER_JOB_REQUIRED", "")) != _bare(job.get("if", "")):
        out.append("`gate` decides whether the job was required by another condition than the job's own `if`")
    run = "\n".join(str(s.get("run", "")) for s in steps)
    block = re.search(r'if \[ "\$WRAPPER_JOB_REQUIRED" = "true" \]; then(.*?)\belse\b(.*?)\bfi\b', run, re.DOTALL)
    required = block and all(
        f'test "${{{{ needs.{name}.result }}}}" = "success"' in block.group(1)
        and f'test "${{{{ needs.{name}.result }}}}" = "skipped"' in block.group(2)
        and f'test "${{{{ needs.{name}.result }}}}" = "success"' not in block.group(2)
        for name in (JOB, UNIT_JOB))
    if 'test "${{ needs.wrapper-job-needed.result }}" = "success"' not in run:
        out.append("`gate` does not demand that the decision was made (a failed decision reads as a skip)")
    if not required:
        out.append("`gate` does not demand success of both wrapper jobs where they are required and a skip elsewhere")
    return out


OLD_CI = """
name: ci
on:
  push:
    branches: [main]
  pull_request:
jobs:
  tests:
    runs-on: ubuntu-latest
    steps: [{run: "true"}]
  gate:
    needs: [tests]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - run: test "${{ needs.tests.result }}" = "success"
"""


def test_the_workflow_before_this_job_is_caught():
    old = yaml.safe_load(OLD_CI)
    assert job_problems(old) == [f"ci.yml has no job `{JOB}`"]
    assert unit_job_problems(old) == [f"ci.yml has no job `{UNIT_JOB}`"]
    assert f"`gate` does not need `{JOB}`" in gate_problems(old)
    assert f"`gate` does not need `{UNIT_JOB}`" in gate_problems(old)


def test_a_job_that_runs_on_less_is_caught():
    spec = _spec()
    job = spec["jobs"][JOB]
    job["if"] = "github.event_name == 'push'"
    spec["jobs"]["wrapper-job-needed"]["steps"][-1]["run"] = "echo run=true >> $GITHUB_OUTPUT"
    job["strategy"]["matrix"]["include"] = job["strategy"]["matrix"]["include"][:2]
    job["env"]["INVISIBLE_CORE_AUTOFIX"] = "on"
    found = job_problems(spec)
    assert any("wrapper-job-needed" in p for p in found)
    assert any("legs" in p for p in found)
    assert any("pin" in p for p in found)


def test_a_gate_that_forgets_the_job_or_decides_otherwise_is_caught():
    spec = _spec()
    spec["jobs"]["gate"]["needs"] = [n for n in spec["jobs"]["gate"]["needs"] if n != JOB]
    assert gate_problems(spec)
    spec = _spec()
    for s in spec["jobs"]["gate"]["steps"]:
        s.get("env", {}).pop("WRAPPER_JOB_REQUIRED", None)
    assert gate_problems(spec)


def test_the_core_ci_runs_the_wrapper_tests_on_this_core():
    assert job_problems(_spec()) == []


def test_the_single_verdict_requires_it_where_it_applies_and_only_there():
    assert gate_problems(_spec()) == []


# ------------------------------------- the wrapper's whole unit suite, on this core (ci-12)

def test_the_core_ci_runs_the_wrappers_unit_suite_on_this_core():
    assert unit_job_problems(_spec()) == []
    job = _spec()["jobs"][UNIT_JOB]
    assert sorted(m["os"] for m in job["strategy"]["matrix"]["include"]) == ["linux", "windows"]
    assert job["needs"] == ["wrapper-job-needed"]
    assert _flat(job["if"]) == "needs.wrapper-job-needed.outputs.run=='true'"


def test_a_unit_job_that_selects_judges_or_installs_otherwise_is_caught():
    def broken(edit):
        spec = _spec()
        edit(spec["jobs"][UNIT_JOB])
        return unit_job_problems(spec)

    def step(job, needle):
        return next(s for s in job["steps"] if needle in str(s.get("run", "")))

    assert any("-p no:rerunfailures" in p for p in broken(lambda j: step(j, "-m pytest").update(
        run=step(j, "-m pytest")["run"].replace("-p no:rerunfailures", ""))))
    assert any("junit" in p for p in broken(lambda j: step(j, "-m pytest").update(
        run=step(j, "-m pytest")["run"].replace("--junitxml=", "--junit-prefix="))))
    assert any("selection" in p for p in broken(lambda j: step(j, "ci_verdict.py args").update(
        run=step(j, "ci_verdict.py args")["run"].replace("wrapper.unit.all.", "wrapper.unit.udp."))))
    assert any("main's ci_verdict" in p for p in broken(lambda j: step(j, "judge").update(
        run=step(j, "judge")["run"].replace("git show origin/main:scripts/ci_verdict.py", "cat scripts/ci_verdict.py"))))
    assert any("list of main" in p or "required-suites" in p for p in broken(lambda j: step(j, "judge").update(
        run=step(j, "judge")["run"].replace("--main-required", "--ignored"))))
    assert any("`if:`" in p for p in broken(lambda j: j.update({"if": "github.event_name == 'push'"})))
    assert any("legs" in p for p in broken(lambda j: j["strategy"]["matrix"].update(include=j["strategy"]["matrix"]["include"][:1])))
    assert any("pin" in p for p in broken(lambda j: j["env"].update(INVISIBLE_CORE_AUTOFIX="on")))
    assert any("ref" in p for p in broken(lambda j: next(
        s for s in j["steps"] if (s.get("with") or {}).get("repository") == "oss-invisible/playwright")["with"].update(ref="main")))


def test_the_gate_reads_a_required_skip_of_the_unit_job_as_red():
    spec = _spec()
    assert gate_problems(spec) == []
    spec["jobs"]["gate"]["needs"] = [n for n in spec["jobs"]["gate"]["needs"] if n != UNIT_JOB]
    assert any(UNIT_JOB in p for p in gate_problems(spec))
    spec = _spec()
    for s in spec["jobs"]["gate"]["steps"]:
        if "run" in s:
            s["run"] = s["run"].replace(
                'test "${{ needs.wrapper-unit-on-this-core.result }}" = "success"',
                'test "${{ needs.wrapper-unit-on-this-core.result }}" = "skipped"', 1)
    assert gate_problems(spec), "a gate that takes a skip of the unit job where it is required is not caught"


# --------------------------------------------------------------- the list of tests

def _sample_tree(root: Path) -> Path:
    tests = root / "tests"
    (tests / "playwright-upstream").mkdir(parents=True)
    files = {
        "test_a_frame_dispatch.py": "from invisible_core.juggler.connection import Connection\n",
        "test_import_module.py": "import invisible_core.juggler\n",
        "test_from_package.py": "from invisible_core import config, juggler\n",
        "test_builds_by_hand.py": "def test_x():\n    lc = Lifecycle(None)\n",
        "test_injected_by_hand.py": "def test_x():\n    InjectedScript(source='')\n",
        "test_connection_by_hand.py": "def test_x():\n    Connection(pipe)\n",
        "test_unrelated.py": "from invisible_core.config import Config\n",
        "test_only_a_comment.py": "# see invisible_core.juggler and Lifecycle for why\n",
        "helper.py": "from invisible_core.juggler import x\n",
        "playwright-upstream/test_vendored.py": "from invisible_core.juggler import y\n",
    }
    for name, body in files.items():
        (tests / name).write_text(body, encoding="utf-8")
    return root


SAMPLE_LIST = ["tests/test_a_frame_dispatch.py", "tests/test_builds_by_hand.py",
               "tests/test_connection_by_hand.py", "tests/test_from_package.py",
               "tests/test_import_module.py", "tests/test_injected_by_hand.py"]


def test_the_grep_finds_the_files_that_stand_on_the_juggler_client_and_only_those(tmp_path):
    mod = _load_script()
    computed = mod.computed_list(_sample_tree(tmp_path))
    assert computed, "the computed list is empty on a tree that has such files"
    assert computed == SAMPLE_LIST


def test_the_list_is_empty_on_a_tree_with_no_such_file(tmp_path):
    mod = _load_script()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_unrelated.py").write_text("import os\n", encoding="utf-8")
    assert mod.computed_list(tmp_path) == []


def test_a_shorter_list_than_mains_is_caught(tmp_path):
    mod = _load_script()
    assert mod.missing_from(SAMPLE_LIST[:-1], SAMPLE_LIST) == [SAMPLE_LIST[-1]]
    assert mod.missing_from(SAMPLE_LIST, SAMPLE_LIST[:-1]) == []


def _compare(mod, wrapper, main_script=None, no_script=False):
    argv = ["compare", "--wrapper", str(wrapper)]
    argv += ["--main-has-no-script"] if no_script else ["--main-script", str(main_script)]
    return mod.main(argv)


def test_compare_is_red_on_an_empty_list_a_shorter_list_and_an_unreadable_main(tmp_path):
    mod = _load_script()
    wrapper = _sample_tree(tmp_path / "wrapper")
    # main's copy of the script that finds one file MORE than this checkout's
    longer = tmp_path / "main_longer.py"
    longer.write_text("def computed_list(wrapper):\n"
                      "    return %r + ['tests/test_gone.py']\n" % SAMPLE_LIST, encoding="utf-8")
    same = tmp_path / "main_same.py"
    same.write_text("def computed_list(wrapper):\n    return %r\n" % SAMPLE_LIST, encoding="utf-8")
    broken = tmp_path / "main_broken.py"
    broken.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    assert _compare(mod, wrapper, longer) == 1
    assert _compare(mod, wrapper, broken) == 1
    assert _compare(mod, wrapper, tmp_path / "does_not_exist.py") == 1
    assert _compare(mod, wrapper, same) == 0
    assert _compare(mod, wrapper, no_script=True) == 0
    empty = tmp_path / "empty"
    (empty / "tests").mkdir(parents=True)
    assert _compare(mod, empty, same) == 1
    assert _compare(mod, empty, no_script=True) == 1


# ----------------------------------------------------------------------- the judge

def _junit(path: Path, cases: list[str]) -> Path:
    path.write_text('<?xml version="1.0"?><testsuites><testsuite name="pytest">'
                    + "".join(cases) + "</testsuite></testsuites>", encoding="utf-8")
    return path


PASS = '<testcase classname="tests.test_a" name="test_x" time="0.1"/>'
PASS_B = '<testcase classname="tests.test_b.TestK" name="test_y" time="0.1"/>'
FAIL = '<testcase classname="tests.test_a" name="test_f"><failure message="no"/></testcase>'
SKIP = '<testcase classname="tests.test_a" name="test_s"><skipped type="pytest.skip" message="no screencast"/></testcase>'
RERUN = '<testcase classname="tests.test_a" name="test_r"><skipped type="pytest.rerun" message="again"/></testcase>'
FILES = ["tests/test_a.py", "tests/test_b.py"]


def test_the_judge_is_red_on_a_failure_a_skip_a_missing_file_and_an_empty_run(tmp_path):
    mod = _load_script()
    ok = _junit(tmp_path / "ok.xml", [PASS, PASS_B])
    assert mod.judge_junit([ok], FILES, [0]) == []
    assert any("FAILED" in p for p in mod.judge_junit([_junit(tmp_path / "f.xml", [PASS, PASS_B, FAIL])], FILES, [1]))
    assert any("SKIPPED" in p for p in mod.judge_junit([_junit(tmp_path / "s.xml", [PASS, PASS_B, SKIP])], FILES, [0]))
    assert any("tests/test_b.py" in p for p in mod.judge_junit([_junit(tmp_path / "m.xml", [PASS])], FILES, [0]))
    assert any("no test ran" in p for p in mod.judge_junit([_junit(tmp_path / "e.xml", [])], [], [5]))
    assert mod.judge_junit([tmp_path / "never_written.xml"], [], [5, 5])
    assert any("code" in p for p in mod.judge_junit([ok], FILES, [0, 2]))


def test_a_test_that_passed_only_at_a_later_attempt_is_a_problem(tmp_path):
    # ci-13: it used to be dropped as "a failed attempt that was run again", so a rerun
    # turned a red into a green. Red on the old code: judge_junit returned [].
    mod = _load_script()
    problems = mod.judge_junit([_junit(tmp_path / "r.xml", [PASS, PASS_B, RERUN])], FILES, [0])
    assert len(problems) == 1
    assert "tests.test_a::test_r" in problems[0] and "later attempt" in problems[0]


SECONDS = Path(__file__).resolve().parents[1] / "scripts" / "wrapper_test_seconds.json"
BIG = "tests/test_nested_frame_actions.py"


def _table(mod, system):
    return mod.load_seconds(system, SECONDS)


def _fake_collect(table):
    """The nodes a file has, as the table knows them, plus one the table has never seen."""
    def collect(rel):
        row = table[rel]
        return [rel + "::" + n for n in row["nodes"]] + [rel + "::test_a_node_the_table_never_saw"]
    return collect


def _files_of_the_table(mod, system):
    return sorted(f for f in _table(mod, system) if f not in mod.excluded_nodes(system)[0])


def _plan(mod, system, parts):
    table = _table(mod, system)
    return mod.plan_parts(_files_of_the_table(mod, system), system, parts, _fake_collect(table), table)


@pytest.mark.parametrize("system,parts", [("linux", 2), ("windows", 4)])
def test_every_node_runs_in_exactly_one_part_as_a_whole_file_or_a_chunk(system, parts):
    mod = _load_script()
    table = _table(mod, system)
    files = _files_of_the_table(mod, system)
    collect = _fake_collect(table)
    plan = mod.plan_parts(files, system, parts, collect, table)
    assert len(plan) == parts
    whole = [f for p in plan for f in p["files"]]
    chunked = [n for p in plan for n in p["nodes"]]
    assert len(whole) == len(set(whole)) and len(chunked) == len(set(chunked))
    long_files = {f for f in files if table[f]["parallel"] > mod.SPLIT_OVER_SECONDS}
    assert long_files
    assert not set(whole) & long_files, "a long file ran whole"
    assert sorted(set(whole) | long_files) == files, "a file ran in no part"
    _, nodes_out = mod.excluded_nodes(system)
    for f in long_files:
        expected = sorted(n for n in collect(f) if not any(n.startswith(x) for x in nodes_out))
        assert sorted(n for n in chunked if n.split("::", 1)[0] == f) == expected
    assert {n.split("::", 1)[0] for n in chunked} == long_files


@pytest.mark.parametrize("system,parts", [("linux", 2), ("windows", 4), ("windows", 6)])
def test_no_part_is_empty_and_the_parts_are_balanced(system, parts):
    mod = _load_script()
    plan = _plan(mod, system, parts)
    for part in plan:
        assert part["files"] or part["nodes"], plan
        assert mod.files_of(part)
    seconds = [p["seconds"] for p in plan]
    # longest first onto the least loaded part: no part is far from the mean
    assert max(seconds) - min(seconds) <= 0.15 * (sum(seconds) / parts) + 15, seconds


def test_the_plan_is_the_same_on_every_call_and_whatever_the_order_of_the_files():
    mod = _load_script()
    for system, parts in (("linux", 2), ("windows", 4)):
        table = _table(mod, system)
        files = _files_of_the_table(mod, system)
        first = mod.plan_parts(files, system, parts, _fake_collect(table), table)
        assert first == mod.plan_parts(files, system, parts, _fake_collect(table), table)
        assert first == mod.plan_parts(list(reversed(files)), system, parts,
                                       lambda f: list(reversed(_fake_collect(table)(f))), table)


def test_the_file_over_90_seconds_is_split_and_the_short_ones_are_not():
    mod = _load_script()
    table = _table(mod, "windows")
    assert table[BIG]["parallel"] > 90
    plan = _plan(mod, "windows", 4)
    holders = [i for i, p in enumerate(plan) if any(n.startswith(BIG + "::") for n in p["nodes"])]
    assert len(holders) >= 2, "the longest file stayed in one part"
    for p in plan:
        assert BIG not in p["files"]
    asked = []
    files = _files_of_the_table(mod, "windows")
    mod.plan_parts(files, "windows", 4, lambda f: asked.append(f) or _fake_collect(table)(f), table)
    assert sorted(asked) == sorted(f for f in files if table[f]["parallel"] > 90), \
        "the nodes of a file that is not long were asked for"


def test_an_unknown_file_and_an_unknown_node_are_handled():
    mod = _load_script()
    table = {"tests/test_known.py": {"parallel": 40.0, "serial": 4.0},
             BIG: {"parallel": 400.0, "serial": 0.0, "nodes": {"test_a": 100.0, "test_b": 300.0}}}
    files = ["tests/test_known.py", "tests/test_new.py", BIG]
    nodes = [BIG + "::test_a", BIG + "::test_b", BIG + "::test_c_unseen"]
    plan = mod.plan_parts(files, "linux", 2, lambda f: nodes, table)
    assert sorted(f for p in plan for f in p["files"]) == ["tests/test_known.py", "tests/test_new.py"]
    assert sorted(n for p in plan for n in p["nodes"]) == nodes
    # the new file is worth the default (30 s of parallel time); the unseen node is worth the
    # average of the two the table has (200 s of parallel time)
    assert mod.DEFAULT_SECONDS == 30.0
    total = sum(p["seconds"] for p in plan)
    assert total == pytest.approx(40 / 4 + 4 + 30 / 4 + (100 + 300 + 200) / 4, abs=0.2)


def test_what_cannot_run_on_a_system_is_left_out_by_name_with_its_reason():
    mod = _load_script()
    posix = "tests/test_juggler_posix_pipe.py"
    injected = "tests/test_juggler_injected.py"
    trap = injected + "::test_OUR_CLIENT_DOES_NOT_CROSS_into_the_page_realm"
    assert posix in mod.excluded_nodes("windows")[0] and posix not in mod.excluded_nodes("linux")[0]
    assert trap in mod.excluded_nodes("windows")[1] and trap in mod.excluded_nodes("linux")[1]
    assert all(reason.strip() for _, _, reason in mod.EXCLUDED)
    # a whole-file exclusion keeps the file out of the plan; a node exclusion keeps the node out of its chunk
    posix_file = {"parallel": 1.0, "serial": 0.0}
    table = {injected: {"parallel": 200.0, "serial": 0.0, "nodes": {"test_x": 100.0, "test_y": 100.0}},
             posix: posix_file}
    nodes = [injected + "::test_x", injected + "::test_y", trap]
    plan = mod.plan_parts([injected, posix, "tests/test_other.py"], "windows", 2, lambda f: nodes, table)
    assert posix not in [f for p in plan for f in p["files"]]
    assert sorted(n for p in plan for n in p["nodes"]) == nodes[:2]


def test_the_tests_that_ask_for_the_processes_of_a_site_run_on_windows_too():
    # The wrapper of firefox-69 reads the type of each process from the engine's log on Windows
    # (tests/_processes.py), so these tests run there: an exclusion of them hid two tests that run
    # (core PR #318, wrapper-on-this-core windows: "the reason is gone").
    mod = _load_script()
    fission = "tests/test_fission_strategy1_bench.py"
    for system in ("linux", "windows"):
        files, nodes = mod.excluded_nodes(system)
        assert not any(t.startswith(fission) for t in list(files) + list(nodes)), system


def test_the_nodes_of_a_file_are_listed_by_pytest_in_the_leg(tmp_path):
    mod = _load_script()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_two.py").write_text(
        "\n".join(["import pytest", "", "def test_a():", "    pass", "",
                   "@pytest.mark.parametrize('x', [1, 2])", "def test_b(x):", "    pass", ""]),
        encoding="utf-8")
    collect = mod.collect_nodes(tmp_path, dict(os.environ, PYTEST_ADDOPTS=""))
    assert collect("tests/test_two.py") == ["tests/test_two.py::test_a", "tests/test_two.py::test_b[1]",
                                            "tests/test_two.py::test_b[2]"]
    with pytest.raises(RuntimeError):
        collect("tests/test_missing.py")


def test_the_e2e_nodes_are_listed_despite_the_wrappers_addopts(tmp_path):
    # The wrapper's pyproject says `-m 'not slow and not e2e'`: a collection that
    # inherits it lists no e2e node, and every leg died on "no tests collected"
    # (core #272, first run).
    mod = _load_script()
    (tmp_path / "pyproject.toml").write_text(
        "\n".join(["[tool.pytest.ini_options]", "addopts = \"-m 'not slow and not e2e' --strict-markers\"",
                   "markers = [\"e2e: x\", \"slow: x\"]", ""]), encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_e2e_only.py").write_text(
        "\n".join(["import pytest", "", "pytestmark = pytest.mark.e2e", "", "def test_a():", "    pass",
                   "", "def test_b():", "    pass", ""]), encoding="utf-8")
    collect = mod.collect_nodes(tmp_path, dict(os.environ, PYTEST_ADDOPTS=""))
    assert collect("tests/test_e2e_only.py") == ["tests/test_e2e_only.py::test_a",
                                                 "tests/test_e2e_only.py::test_b"]


# ------------------------------------------------- when the job is needed (run for real)

def _decide_script() -> str:
    step = next(x for x in _spec()["jobs"]["wrapper-job-needed"]["steps"] if x.get("id") == "decide")
    return step["run"]


def _decide(tmp_path, event, head_ref, changed, body="", script_text=None):
    """Runs the `decide` step in a throwaway repo where the head commit changes ``changed``."""
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash on this machine")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t" "@example.invalid"]

    def run(*cmd):
        return subprocess.run(list(cmd), cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

    run(*git, "init", "-q")
    # the checkout of a run has the script: the step runs it from `scripts/`, with the interpreter of this test
    (repo / "scripts").mkdir()
    (repo / "scripts" / "wrapper_job_needed.py").write_text(NEEDED.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "keep.txt").write_text("a", encoding="utf-8")
    run(*git, "add", "-A")
    run(*git, "commit", "-q", "-m", "base")
    base = run("git", "rev-parse", "HEAD")
    for rel in changed:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        # the script itself, when it is one of the changed files, stays the script (with one more line): the step runs it
        body_of_rel = NEEDED.read_text(encoding="utf-8") + "\n# changed\n" if rel == "scripts/wrapper_job_needed.py" else "b"
        (repo / rel).write_text(body_of_rel, encoding="utf-8")
    (repo / "keep.txt").write_text("b", encoding="utf-8")
    run(*git, "add", "-A")
    run(*git, "commit", "-q", "-m", "head")
    head = run("git", "rev-parse", "HEAD")
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    script = tmp_path / "decide.sh"
    step = script_text if script_text is not None else _decide_script().replace("python3 ", f'"{Path(sys.executable).as_posix()}" ')
    script.write_text(step, encoding="utf-8", newline="\n")
    env = dict(__import__("os").environ, EVENT=event, HEAD_REF=head_ref, BASE_SHA=base, HEAD_SHA=head, PR_BODY=body,
               RUNNER_TEMP=str(tmp_path), GITHUB_OUTPUT=str(out), GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"))
    done = subprocess.run([bash, str(script)], cwd=repo, env=env, capture_output=True, text=True)
    return done, out.read_text(encoding="utf-8").strip()


def test_the_job_is_needed_on_a_push_a_release_branch_and_a_change_of_the_job_only(tmp_path):
    for n, (event, head_ref, changed, expected) in enumerate([
            ("push", "", [], "run=true"),
            ("pull_request", "release/65.72.0", [], "run=true"),
            ("pull_request", "fix/x", ["docs/a.md", "README.md"], "run=false"),
            ("pull_request", "fix/x", ["tests/test_other.py", "scripts/other.py"], "run=false"),
            # what the installed core is made of (ci-12): the package, its build and dependencies
            ("pull_request", "fix/x", ["src/invisible_core/x.py", "docs/a.md"], "run=true"),
            ("pull_request", "fix/x", ["pyproject.toml"], "run=true")]
            + [("pull_request", "fix/x", [f], "run=true") for f in JOB_FILES]
            # a merge group (the merge queue): the files between its base and its head, the same rule as a pull request
            + [("merge_group", "", ["src/invisible_core/x.py"], "run=true"),
               ("merge_group", "", ["pyproject.toml"], "run=true"),
               ("merge_group", "", ["docs/a.md", "README.md"], "run=false"),
               ("merge_group", "", ["tests/test_other.py", "scripts/other.py"], "run=false")]
            + [("merge_group", "", [f], "run=true") for f in JOB_FILES]):
        done, out = _decide(tmp_path / str(n), event, head_ref, changed)
        assert done.returncode == 0, done.stderr
        assert out == expected, (event, head_ref, changed, out)


def test_an_event_the_decision_does_not_know_is_red_not_not_needed(tmp_path):
    done, out = _decide(tmp_path / "x", "workflow_dispatch", "", ["src/invisible_core/x.py"])
    assert done.returncode != 0 and out == ""


def test_a_diff_that_cannot_be_read_is_red_not_not_needed(tmp_path):
    (tmp_path / "x").mkdir()
    import subprocess
    done, out = _decide(tmp_path / "x", "pull_request", "fix/x", [])
    assert done.returncode == 0 and out == "run=false"
    # the same step, with a base that does not exist
    script = tmp_path / "x" / "decide.sh"
    assert "wrapper_job_needed.py" in script.read_text(encoding="utf-8")
    env = dict(__import__("os").environ, EVENT="pull_request", HEAD_REF="fix/x", BASE_SHA="0" * 40,
               HEAD_SHA="1" * 40, RUNNER_TEMP=str(tmp_path / "x"), GITHUB_OUTPUT=str(tmp_path / "x" / "out2.txt"))
    bad = subprocess.run([__import__("shutil").which("bash"), str(script)], cwd=tmp_path / "x" / "repo",
                         env=env, capture_output=True, text=True)
    assert bad.returncode != 0
    assert not (tmp_path / "x" / "out2.txt").exists() or "run=false" not in (tmp_path / "x" / "out2.txt").read_text()


# ------------------------------------------------------------------ the witnesses

# The text of the wrapper's test files as they are today, cut to what each witness reads.
TODAY = {
    "tests/test_juggler_posix_pipe.py": (
        "import sys\nimport pytest\n\npytestmark = pytest.mark.skipif(\n    sys.platform == \"win32\",\n"
        "    reason=\"the POSIX leg does not execute on Windows\")\n\ndef test_the_posix_pipe_carries_messages_both_ways():\n    pass\n"),
    "tests/test_the_working_clone_has_the_workbench_next_to_it.py": (
        "import pytest\n\ndef test_the_workbench_inventory_and_the_package_cannot_DRIFT():\n"
        "    tool = pathlib.Path(__file__).resolve().parents[3] / \"scripts\" \\\n        / \"inventario_voce6.py\"\n"
        "    if not tool.exists():\n        pytest.skip(\"the workbench is not next to this checkout\")\n\n"
        "def test_other():\n    pass\n"),
    "tests/test_juggler_injected.py": (
        "import pytest\n\ndef test_OUR_CLIENT_DOES_NOT_CROSS_into_the_page_realm():\n"
        "    spec = importlib.util.spec_from_file_location(\n        \"oc\", str(pathlib.Path(__file__).resolve().parents[3]\n"
        "                  / \"tests\" / \"gates\" / \"observable_crossings.py\"))\n"
        "    if spec is None or not pathlib.Path(spec.origin).is_file():\n"
        "        pytest.skip(\"the trap page lives in the workbench, not here\")\n\n"
        "def test_other():\n    pass\n"),
}

# The same text with the reason taken out of it: what each witness must notice.
REASON_REMOVED = {
    "tests/test_juggler_posix_pipe.py": lambda t: t.replace("sys.platform == \"win32\"", "False"),
    "tests/test_the_working_clone_has_the_workbench_next_to_it.py": lambda t: t.replace("pytest.skip(", "print("),
    "tests/test_juggler_injected.py": lambda t: t.replace("pytest.skip(", "print("),
}


def _wrapper_tree(tmp_path, edits=None):
    root = tmp_path / "work" / "wrapper"
    for rel, text in TODAY.items():
        if edits and rel in edits:
            text = edits[rel](text)
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def _entries(mod, system):
    return [t for only, t, _ in mod.EXCLUDED if only in (None, system)]


@pytest.mark.parametrize("system", ["linux", "windows"])
def test_every_exclusion_has_a_witness_and_each_holds_on_todays_text(tmp_path, system):
    mod = _load_script()
    assert set(mod.WITNESSES) == {t for _, t, _ in mod.EXCLUDED}, "an exclusion with no witness, or a witness of none"
    assert mod.witness_problems(_wrapper_tree(tmp_path), system, "firefox-64-candidate") == []


def test_each_witness_is_red_on_a_text_with_its_reason_removed_and_names_the_entry(tmp_path):
    mod = _load_script()
    for n, (only, target, _) in enumerate(mod.EXCLUDED):
        rel = target.split("::")[0]
        for system in ("linux", "windows"):
            if only not in (None, system):
                continue
            root = _wrapper_tree(tmp_path / f"{n}{system}", {rel: REASON_REMOVED[rel]})
            found = mod.witness_problems(root, system, "firefox-64")
            mine = [f for f in found if f.startswith(target + ":")]
            assert len(mine) == 1, (target, system, found)
            assert "the reason is gone: run it, remove the exclusion" in mine[0]


def test_the_workbench_witness_is_red_where_the_workbench_files_are_present(tmp_path):
    mod = _load_script()
    root = _wrapper_tree(tmp_path)
    (root.parent.parent / "scripts").mkdir()
    (root.parent.parent / "scripts" / "inventario_voce6.py").write_text("x", encoding="utf-8")
    found = mod.witness_problems(root, "linux", "firefox-64")
    assert len(found) == 1 and "test_the_workbench_inventory" in found[0] and "exists in this clone" in found[0]


def test_an_exclusion_whose_file_is_gone_or_has_no_witness_is_red(tmp_path):
    mod = _load_script()
    root = _wrapper_tree(tmp_path)
    (root / "tests" / "test_juggler_posix_pipe.py").unlink()
    found = mod.witness_problems(root, "windows", "firefox-64")
    assert len(found) == 1 and "the reason is gone" in found[0] and "posix_pipe" in found[0]
    saved = dict(mod.WITNESSES)
    try:
        mod.WITNESSES.pop("tests/test_juggler_posix_pipe.py")
        assert any("no witness" in f for f in mod.witness_problems(_wrapper_tree(tmp_path / "b"), "windows", "firefox-64"))
    finally:
        mod.WITNESSES.update(saved)


# ------------------------------------------- which branch of the wrapper the job tries
#
# `main`, unless the body of a PULL REQUEST declares `wrapper-ref: <branch>` (one line, the
# branch must exist in the wrapper's repository). The `ls-remote` is a stand-in here.

def _remote(*branches):
    """A stand-in for `git ls-remote --heads <wrapper> refs/heads/<branch>`: answers as git does."""
    def ls_remote(branch):
        return "".join(f"{'a' * 40}\trefs/heads/{b}\n" for b in branches if b == branch)
    return ls_remote


def _no_network(branch):
    raise AssertionError(f"the wrapper's repository was asked about {branch!r}: it must not be")


def test_a_pull_request_without_the_line_tries_the_wrappers_main():
    mod = _load_script()
    for body in (None, "", "Fixes the thing.\n\nNo declaration here.\n",
                 "  wrapper-ref: feat/x\n",                      # not at the start of the line: ordinary text
                 "see wrapper-ref: feat/x in the other pr\n"):
        ref, why = mod.choose_wrapper_ref("pull_request", body, _no_network)
        assert ref == "main", body
        assert "main" in why


def test_a_pull_request_that_declares_an_existing_branch_tries_that_branch(tmp_path, monkeypatch):
    mod = _load_script()
    remote = _remote("main", "feat/x", "fix/a.b_c-1")
    for body, want in (("wrapper-ref: feat/x", "feat/x"),
                       ("Intro\r\n\r\nwrapper-ref: feat/x\r\nmore text\r\n", "feat/x"),     # the web form writes CRLF
                       ("wrapper-ref: fix/a.b_c-1\n", "fix/a.b_c-1"),
                       ("wrapper-ref: main", "main")):
        ref, why = mod.choose_wrapper_ref("pull_request", body, remote)
        assert ref == want and want in why
    # the command: the ref goes to the step output, the reason names the branch in the summary
    body_file = tmp_path / "body.txt"
    body_file.write_text("Text\n\nwrapper-ref: feat/x\n", encoding="utf-8")
    out, summary = tmp_path / "out.txt", tmp_path / "summary.md"
    monkeypatch.setattr(mod, "_ls_remote_heads", remote)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert mod.main(["wrapper-ref", "--event", "pull_request", "--body-file", str(body_file)]) == 0
    assert out.read_text(encoding="utf-8") == "ref=feat/x\n"
    assert "feat/x" in summary.read_text(encoding="utf-8")


def test_the_default_is_written_to_the_output_and_to_the_summary_too(tmp_path, monkeypatch, capsys):
    mod = _load_script()
    out, summary = tmp_path / "out.txt", tmp_path / "summary.md"
    monkeypatch.setattr(mod, "_ls_remote_heads", _no_network)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert mod.main(["wrapper-ref", "--event", "pull_request"]) == 0      # no body file at all
    assert out.read_text(encoding="utf-8") == "ref=main\n"
    assert "main" in summary.read_text(encoding="utf-8")
    assert capsys.readouterr().out.strip() == "main"


@pytest.mark.parametrize("line, reason", [
    ("wrapper-ref:", "malformed"),                       # empty
    ("wrapper-ref: ", "malformed"),                      # empty after the space
    ("wrapper-ref:feat/x", "malformed"),                 # no space
    ("wrapper-ref:  feat/x", "malformed"),               # two spaces
    ("wrapper-ref: feat/x ", "malformed"),               # trailing space
    ("wrapper-ref: feat x", "malformed"),                # a space inside
    ("wrapper-ref: feat/x extra", "malformed"),
    ("wrapper-ref: feat/x;rm -rf", "malformed"),         # characters that are not allowed
    ("wrapper-ref: $(id)", "malformed"),
    ("wrapper-ref: feat/*", "malformed"),
    ("wrapper-ref: feat~1", "malformed"),
    ("wrapper-ref: feat\"x", "malformed"),
    ("Wrapper-Ref: feat/x", "malformed"),                # the grammar is lower case
    ("wrapper-ref : feat/x", "malformed"),
    ("wrapper-ref: -feat", "not a valid branch name"),
    ("wrapper-ref: /feat", "not a valid branch name"),
    ("wrapper-ref: feat/", "not a valid branch name"),
    ("wrapper-ref: feat..x", "not a valid branch name"),
    ("wrapper-ref: feat//x", "not a valid branch name"),
    ("wrapper-ref: feat.lock", "not a valid branch name"),
    ("wrapper-ref: gone/branch", "does not exist"),      # well formed, not in the wrapper's repository
    ("wrapper-ref: feat", "does not exist"),             # `feat/x` exists, `feat` does not: no prefix match
])
def test_a_bad_line_is_red_with_the_reason(line, reason, tmp_path, monkeypatch, capsys):
    mod = _load_script()
    remote = _remote("main", "feat/x")
    with pytest.raises(mod.WrapperRefError, match=reason):
        mod.choose_wrapper_ref("pull_request", f"text\n{line}\nmore\n", remote)
    body_file = tmp_path / "body.txt"
    body_file.write_text(f"text\n{line}\n", encoding="utf-8")
    out, summary = tmp_path / "out.txt", tmp_path / "summary.md"
    monkeypatch.setattr(mod, "_ls_remote_heads", remote)
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert mod.main(["wrapper-ref", "--event", "pull_request", "--body-file", str(body_file)]) == 1
    assert reason in capsys.readouterr().err
    assert not out.exists()                                # no ref is chosen
    assert "RED" in summary.read_text(encoding="utf-8")


def test_two_lines_are_red_even_if_both_are_fine():
    mod = _load_script()
    remote = _remote("feat/x", "feat/y")
    for body in ("wrapper-ref: feat/x\nwrapper-ref: feat/y\n", "wrapper-ref: feat/x\ntext\nwrapper-ref: feat/x\n",
                 "wrapper-ref: feat/x\nwrapper-ref:\n"):
        with pytest.raises(mod.WrapperRefError, match="2 `wrapper-ref:` lines"):
            mod.choose_wrapper_ref("pull_request", body, remote)


def test_a_wrapper_repository_that_cannot_be_asked_is_red_not_main(monkeypatch):
    import subprocess
    mod = _load_script()

    def boom(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: unable to access")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    with pytest.raises(mod.WrapperRefError, match="cannot ask .* for its branches"):
        mod.choose_wrapper_ref("pull_request", "wrapper-ref: feat/x\n")


def test_ls_remote_is_asked_for_the_exact_branch_of_the_wrapper(monkeypatch):
    import subprocess
    mod = _load_script()
    seen = []

    def fake(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{'b' * 40}\trefs/heads/feat/x\n", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake)
    assert mod.choose_wrapper_ref("pull_request", "wrapper-ref: feat/x\n")[0] == "feat/x"
    assert seen == [["git", "ls-remote", "--heads", "https://github.com/oss-invisible/playwright", "refs/heads/feat/x"]]


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "schedule", "release"])
def test_a_push_a_tag_and_every_other_event_try_main_and_ignore_the_line(event):
    mod = _load_script()
    for body in ("wrapper-ref: feat/x\n", "wrapper-ref: not a branch\nwrapper-ref: b\n", None):
        ref, why = mod.choose_wrapper_ref(event, body, _no_network)
        assert ref == "main" and "not read" in why


# ---- the workflow: the checkout takes the output, the body is only in `env:`, one step

def test_the_wrapper_checkout_takes_the_ref_from_the_step_that_chooses_it():
    spec = _spec()
    steps = spec["jobs"][JOB]["steps"]
    chooser = [s for s in steps if "wrapper_on_this_core.py wrapper-ref" in str(s.get("run", ""))]
    assert len(chooser) == 1
    assert _bare(chooser[0]["env"]["EVENT"]) == "github.event_name"
    assert "github.event.pull_request.body" in str(list(chooser[0]["env"].values()))
    assert '--event "$EVENT"' in chooser[0]["run"]
    checkout = next(s for s in steps if (s.get("with") or {}).get("repository") == "oss-invisible/playwright")
    assert _bare(checkout["with"]["ref"]) == f"steps.{chooser[0]['id']}.outputs.ref"
    assert steps.index(chooser[0]) < steps.index(checkout)
    # the wrapper is checked out here and nowhere else in the workflow
    others = [name for name, j in spec["jobs"].items() for s in j.get("steps") or []
              if (s.get("with") or {}).get("repository") == "oss-invisible/playwright"]
    assert others == [JOB, UNIT_JOB]
    assert job_problems(spec) == []


def test_a_workflow_that_checks_out_main_or_interpolates_the_body_is_caught():
    spec = _spec()
    steps = spec["jobs"][JOB]["steps"]
    next(s for s in steps if (s.get("with") or {}).get("repository") == "oss-invisible/playwright")["with"]["ref"] = "main"
    assert any("take its ref from the output" in p for p in job_problems(spec))
    spec = _spec()
    chooser = next(s for s in spec["jobs"][JOB]["steps"] if "wrapper-ref" in str(s.get("run", "")))
    chooser["run"] = "echo '${{ github.event.pull_request.body }}' > body.txt\n" + chooser["run"]
    assert any("interpolated" in p for p in job_problems(spec))
    spec = _spec()
    spec["jobs"][JOB]["steps"] = [s for s in spec["jobs"][JOB]["steps"] if "wrapper-ref" not in str(s.get("run", ""))]
    assert any("take its ref from the output" in p for p in job_problems(spec))
    spec = _spec()
    next(s for s in spec["jobs"][JOB]["steps"] if "wrapper-ref" in str(s.get("run", "")))["env"].pop("PR_BODY")
    assert any("through `env:`" in p for p in job_problems(spec))


def test_the_body_reaches_the_script_by_env_and_a_hostile_body_is_inert(tmp_path):
    """Runs the real step: a body full of shell syntax is written to the file byte for byte, and what the
    script makes of it is a refusal (a malformed line), never an execution."""
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash on this machine")
    step = next(s for s in _spec()["jobs"][JOB]["steps"] if s.get("id") == "wrapper_ref")
    assert "${{" not in step["run"]
    script = tmp_path / "step.sh"
    script.write_text(step["run"].replace("scripts/wrapper_on_this_core.py", SCRIPT.as_posix()), encoding="utf-8", newline="\n")
    marker = tmp_path / "pwned"
    hostile = f"x'; touch {marker.as_posix()} #\n$(touch {marker.as_posix()})\n`touch {marker.as_posix()}`\nwrapper-ref: a b\n"
    out = tmp_path / "out.txt"
    env = dict(os.environ, EVENT="pull_request", PR_BODY=hostile, RUNNER_TEMP=tmp_path.as_posix(), GITHUB_OUTPUT=str(out))
    done = subprocess.run([bash, str(script)], cwd=ROOT, env=env, capture_output=True, text=True)
    assert done.returncode == 1 and "malformed" in done.stderr, (done.stdout, done.stderr)
    assert (tmp_path / "pr_body.txt").read_bytes() == hostile.encode("utf-8")
    assert not marker.exists()
    # and a push, with the same body, gives main
    env.update(EVENT="push")
    done = subprocess.run([bash, str(script)], cwd=ROOT, env=env, capture_output=True, text=True)
    assert done.returncode == 0 and out.read_text(encoding="utf-8") == "ref=main\n", done.stderr


def test_a_pull_request_that_declares_a_wrapper_branch_makes_the_job_needed(tmp_path):
    step = next(x for x in _spec()["jobs"]["wrapper-job-needed"]["steps"] if x.get("id") == "decide")
    assert "PR_BODY" in step["env"] and "${{" not in step["run"]
    for n, (body, expected) in enumerate([("", "run=false"), ("nothing to see\n", "run=false"),
                                          ("wrapper-ref: feat/x\n", "run=true"),
                                          ("a\r\nwrapper-ref: feat/x\r\n", "run=true"),
                                          ("see wrapper-ref: feat/x\n", "run=false")]):
        done, out = _decide(tmp_path / str(n), "pull_request", "fix/x", ["docs/other.md"], body=body)
        assert done.returncode == 0, done.stderr
        assert out == expected, (body, out)
