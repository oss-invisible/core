"""A tag proves itself with the wrapper release that pins it, and is compared with what is already PUBLISHED.

publish.yml's `proof` job runs the wrapper's tests on the tagged core. Two errors made it red or blind on the day of a release, both found by
replaying the real data (not by reading):

  1. On a tag or a manual run the list of tests was compared with `origin/main`. A core release is cut from a release branch, not from main
     (v70.83.0 is v69.82.0 plus four commits that are not on main), so main is the wrong base. The base is what is already PUBLISHED: the
     highest release of the tagged tree's PUBLISHED.json below the version being released (core), and of the wrapper's PUBLISHED.json
     below the declared commit's own version (wrapper's judge).
  2. The proof ran the wrapper's MAIN. At tag time main pins the OLD core, so it is red by construction: wrapper cccef5da (pins
     invisible_core==69.82.0) on core 70.83.0 fails tests/test_persona_cover.py::test_the_stored_file_is_what_a_recompute_gives, while the release
     wrapper b39b7f65e128500175fbef36b718120956c5146b (pins 70.83.0) is green. ci/wrapper-proof.json names the wrapper commit a tag is proved with;
     the tag refuses a checkout that is not that commit or does not pin exactly this core, and ci.yml checks the same file on every pull request.

The pull-request and push-to-main paths are unchanged and held here too. The fixtures come from the real data (source commits in comments).
The step texts of the workflows are executed here, with bash, on small git repositories built for the case: no network, no tag of the real
repository needed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPT = ROOT / "scripts" / "wrapper_on_this_core.py"
SHA_NEW = "b39b7f65e128500175fbef36b718120956c5146b"   # wrapper release 0.60.0 (pins invisible_core==70.83.0): the first content of the proof file
# The series run of the first content: the build run of firefox-70 (oss-invisible/firefox_antidetect_patch release.yml, workflow_dispatch on
# 063dde5e829e465bd3fccb2857b399d130c6b7d4, concluded success), made before the rule, so it has no `release-plan` artifact (total_count 0).
SERIES_RUN = 45854283146
SERIES_COMMIT = "063dde5e829e465bd3fccb2857b399d130c6b7d4"
PROOF = {"wrapper_commit": SHA_NEW, "series_run": SERIES_RUN}

# The `released` versions of PUBLISHED.json of the core at tag v70.83.0 (commit 8ebfd09 of oss-invisible/core), in file order.
CORE_RELEASED_AT_70_83_0 = """
18.0.0 18.1.0 18.2.0 18.3.0 18.4.0 18.5.0 18.6.0 18.7.0 18.8.0 18.9.0 18.10.0 18.11.0 18.12.0 18.13.0 18.14.0 19.14.0 20.14.0 20.15.0
20.16.0 21.16.0 22.16.0 23.16.0 24.16.0 25.16.0 26.16.0 26.17.0 27.17.0 27.18.0 27.19.0 29.19.0 30.19.0 30.20.0 30.21.0 30.22.0 30.23.0
31.23.0 32.23.0 33.23.0 33.24.0 34.24.0 34.25.0 34.26.0 34.27.0 34.28.0 34.29.0 34.30.0 34.31.0 34.32.0 35.32.0 36.32.0 36.33.0 37.33.0
38.33.0 38.34.0 39.34.0 39.35.0 40.35.0 41.35.0 41.36.0 41.37.0 41.38.0 41.39.0 42.39.0 43.39.0 44.39.0 45.39.0 46.39.0 47.39.0 47.40.0
47.41.0 48.41.0 48.42.0 48.43.0 49.44.0 49.45.0 49.46.0 50.47.0 50.48.0 51.48.0 52.48.0 53.48.0 54.48.0 54.49.0 55.50.0 55.51.0 56.51.0
56.52.0 56.53.0 57.53.0 57.54.0 58.55.0 58.56.0 59.57.0 59.58.0 59.59.0 59.60.0 60.61.0 61.62.0 62.63.0 62.64.0 62.65.0 63.66.0 63.67.0
63.68.0 64.69.0 64.70.0 64.71.0 64.72.0 64.73.0 65.74.0 66.75.0 66.76.0 67.77.0 67.78.0 67.79.0 68.80.0 68.81.0 69.82.0
""".split()

# The tail of the `released` versions of PUBLISHED.json of the wrapper at commit b39b7f65 (release 0.60.0; the whole list is 0.3.5 .. 0.59.0,
# with 0.50.1 and no 0.50.0, and "0.9.0" after "0.59.0" when sorted as text).
WRAPPER_RELEASED_AT_0_60_0 = ["0.8.3", "0.9.0", "0.10.0", "0.49.1", "0.50.1", "0.51.0", "0.52.1", "0.53.1", "0.54.0", "0.55.0", "0.56.0",
                              "0.57.0", "0.57.1", "0.58.0", "0.59.0"]

# [project] of oss-invisible/playwright, cut to what the checks read. cccef5da (version 0.59.1) pins the old core; b39b7f65 (0.60.0) the new one.
PYPROJECT_CCCEF5DA = '[project]\nname = "invisible_playwright"\nversion = "0.59.1"\ndependencies = [\n    "invisible_core==69.82.0",\n]\n'
PYPROJECT_B39B7F65 = '[project]\nname = "invisible_playwright"\nversion = "0.60.0"\ndependencies = [\n    "invisible_core==70.83.0",\n]\n'


def _load_script():
    spec = importlib.util.spec_from_file_location("wrapper_on_this_core_under_test_release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_workflow(name: str) -> dict:
    spec = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    if True in spec and "on" not in spec:  # PyYAML reads the key `on` as True
        spec["on"] = spec.pop(True)
    return spec


def _step(workflow: str, job: str, name: str) -> dict:
    return next(s for s in _load_workflow(workflow)["jobs"][job]["steps"] if s.get("name") == name)


def _step_running(workflow: str, job: str, text: str) -> dict:
    return next(s for s in _load_workflow(workflow)["jobs"][job]["steps"] if text in str(s.get("run", "")))


def _published(versions) -> dict:
    return {"released": [{"version": v} for v in versions]}


def _run(argv, capsys=None):
    return _load_script().main(argv)


# ----------------------------------------------------------------------------- previous-release

def test_the_previous_release_of_70_83_0_is_69_82_0():
    mod = _load_script()
    assert mod.previous_release(_published(CORE_RELEASED_AT_70_83_0), "70.83.0") == "v69.82.0"


def test_the_previous_release_is_the_highest_below_and_the_version_itself_and_the_ones_above_do_not_count():
    mod = _load_script()
    both = _published(CORE_RELEASED_AT_70_83_0 + ["70.83.0", "71.84.0"])  # a ledger that already holds the release (a dispatch after it)
    assert mod.previous_release(both, "70.83.0") == "v69.82.0"
    assert mod.previous_release(both, "71.84.0") == "v70.83.0"


def test_the_previous_release_is_by_number_not_by_text():
    mod = _load_script()
    assert mod.previous_release(_published(WRAPPER_RELEASED_AT_0_60_0), "0.60.0") == "v0.59.0"
    assert mod.previous_release(_published(["0.9.0", "0.10.0"]), "0.11.0") == "v0.10.0"


@pytest.mark.parametrize("published, version", [
    (_published(["70.83.0", "71.84.0"]), "70.83.0"),   # nothing lower
    (_published([]), "70.83.0"),
    ({"released": "no"}, "70.83.0"),
    ({}, "70.83.0"),
    (_published(["69.82.0"]), "70.83"),                # not a version
    ({"released": [{"version": "69.82"}]}, "70.83.0"),  # an entry that cannot be read is not skipped
])
def test_no_previous_release_or_one_that_cannot_be_read_is_red(published, version):
    mod = _load_script()
    with pytest.raises(mod.ProofError):
        mod.previous_release(published, version)


def test_the_previous_release_subcommand_prints_the_tag_and_is_red_without_one(tmp_path, capsys):
    ledger = tmp_path / "PUBLISHED.json"
    ledger.write_text(json.dumps(_published(CORE_RELEASED_AT_70_83_0)), encoding="utf-8")
    assert _run(["previous-release", "--published", str(ledger), "--version", "70.83.0"]) == 0
    assert capsys.readouterr().out.strip() == "v69.82.0"
    assert _run(["previous-release", "--published", str(ledger), "--version", "18.0.0"]) == 1
    assert _run(["previous-release", "--published", str(tmp_path / "missing.json"), "--version", "70.83.0"]) == 1
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(PYPROJECT_B39B7F65, encoding="utf-8")
    ledger.write_text(json.dumps(_published(WRAPPER_RELEASED_AT_0_60_0)), encoding="utf-8")
    assert _run(["previous-release", "--published", str(ledger), "--pyproject", str(pyproject)]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "v0.59.0"


def test_the_version_a_release_proves_is_the_tag_or_the_tree(tmp_path):
    mod = _load_script()
    assert mod.proof_version("push", "tag", "v70.83.0") == "70.83.0"
    for bad in ("70.83.0-rc1", "vX", "v70.83"):
        with pytest.raises(mod.ProofError):
            mod.proof_version("push", "tag", bad)
    with pytest.raises(mod.ProofError):  # a branch push has no version to prove
        mod.proof_version("push", "branch", "main")
    tree = tmp_path / "tree" / "src" / "invisible_core"
    tree.mkdir(parents=True)
    for name in ("_version.py", "seal.json"):
        shutil.copy(ROOT / "src" / "invisible_core" / name, tree / name)
    assert mod.proof_version("workflow_dispatch", "branch", "main", root=tmp_path / "tree") == mod.tree_version(ROOT)


# ----------------------------------------------------------------------------- the proof file

def _write_proof(path: Path, value) -> Path:
    path.write_text(json.dumps(value) if not isinstance(value, str) else value, encoding="utf-8")
    return path


def test_the_proof_file_of_this_tree_is_well_formed_and_names_the_wrapper_that_pins_this_core():
    mod = _load_script()
    assert mod.read_wrapper_proof(ROOT / "ci" / "wrapper-proof.json") == SHA_NEW
    assert mod.read_proof(ROOT / "ci" / "wrapper-proof.json") == PROOF
    # core main's _version.py is the number the wrapper release pins (70.83.0): the first content of the file.
    assert mod.tree_version(ROOT) == "70.83.0"


@pytest.mark.parametrize("content", [
    {**PROOF, "wrapper_commit": SHA_NEW[:7]},                     # a short sha
    {**PROOF, "wrapper_commit": SHA_NEW.upper()},                 # not lower case
    {**PROOF, "wrapper_commit": SHA_NEW + "0"},
    {**PROOF, "wrapper_commit": 42},
    {**PROOF, "wrapper_commit": "main"},
    {**PROOF, "wrapper_commit": SHA_NEW, "note": "extra key"},
    {"wrapper_commit": SHA_NEW},                          # no series_run
    {"series_run": SERIES_RUN},                           # no wrapper_commit
    {**PROOF, "series_run": "45854283146"},               # not an integer
    {**PROOF, "series_run": True},
    {**PROOF, "series_run": 0},
    {**PROOF, "series_run": -5},
    {**PROOF, "series_run": 1.5},
    {},
    [SHA_NEW],
    "{not json",
    "",
])
def test_a_malformed_proof_file_is_refused(tmp_path, content):
    mod = _load_script()
    path = _write_proof(tmp_path / "wrapper-proof.json", content)
    with pytest.raises(mod.ProofError):
        mod.read_wrapper_proof(path)
    assert _run(["read-wrapper-proof", "--file", str(path)]) == 1


def test_a_missing_proof_file_is_refused(tmp_path):
    mod = _load_script()
    with pytest.raises(mod.ProofError):
        mod.read_wrapper_proof(tmp_path / "nope.json")
    assert _run(["read-wrapper-proof", "--file", str(tmp_path / "nope.json")]) == 1


# ----------------------------------------------------------------------------- check-wrapper-proof

def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@users.noreply.github.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@users.noreply.github.com"}
    done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _wrapper_checkout(tmp_path: Path, pyproject: str) -> tuple[Path, str]:
    wrapper = tmp_path / "wrapper"
    wrapper.mkdir()
    _git(wrapper, "init", "-q")
    (wrapper / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    _git(wrapper, "add", "pyproject.toml")
    _git(wrapper, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "wrapper")
    return wrapper, _git(wrapper, "rev-parse", "HEAD")


def _check(tmp_path, pyproject, version, declare=None, proof=None):
    wrapper, head = _wrapper_checkout(tmp_path, pyproject)
    path = tmp_path / "wrapper-proof.json"
    if proof is None:
        _write_proof(path, {**PROOF, "wrapper_commit": declare or head})
    elif proof is not False:
        _write_proof(path, proof)
    return _run(["check-wrapper-proof", "--file", str(path), "--wrapper", str(wrapper), "--version", version])


def test_a_wrapper_that_pins_the_old_core_does_not_ship_with_this_core(tmp_path, capsys):
    """cccef5da (the wrapper main of the day, pin 69.82.0) on core 70.83.0: red by construction."""
    assert _check(tmp_path, PYPROJECT_CCCEF5DA, "70.83.0") == 1
    assert "does not ship with this core" in capsys.readouterr().err


def test_the_wrapper_release_that_pins_this_core_is_green(tmp_path, capsys):
    assert _check(tmp_path, PYPROJECT_B39B7F65, "70.83.0") == 0
    assert "ships with this core" in capsys.readouterr().out


@pytest.mark.parametrize("pin", ["invisible_core>=70.83.0", "invisible_core==70.83.0; python_version>='3.11'", "invisible_core==70.83.1",
                                 "invisible_core[x]==70.83.0", "invisible_core"])
def test_only_an_exact_pin_of_this_version_counts(tmp_path, pin):
    assert _check(tmp_path, PYPROJECT_B39B7F65.replace("invisible_core==70.83.0", pin), "70.83.0") == 1


def test_a_wrapper_that_pins_the_core_twice_or_not_at_all_is_red(tmp_path):
    twice = PYPROJECT_B39B7F65.replace('"invisible_core==70.83.0",', '"invisible_core==70.83.0", "invisible-core==70.83.0",')
    (tmp_path / "a").mkdir()
    assert _check(tmp_path / "a", twice, "70.83.0") == 1
    (tmp_path / "b").mkdir()
    assert _check(tmp_path / "b", PYPROJECT_B39B7F65.replace('"invisible_core==70.83.0",', ""), "70.83.0") == 1


def test_a_checkout_that_is_not_the_declared_commit_is_red(tmp_path, capsys):
    assert _check(tmp_path, PYPROJECT_B39B7F65, "70.83.0", declare=SHA_NEW) == 1   # the pin is right, the commit is not the declared one
    assert "not at the declared commit" in capsys.readouterr().err


def test_a_short_sha_and_a_missing_file_are_red_even_if_the_pin_is_right(tmp_path):
    assert _check(tmp_path, PYPROJECT_B39B7F65, "70.83.0", proof={**PROOF, "wrapper_commit": SHA_NEW[:7]}) == 1
    (tmp_path / "x").mkdir()
    assert _check(tmp_path / "x", PYPROJECT_B39B7F65, "70.83.0", proof=False) == 1


def test_a_checkout_with_no_pyproject_is_red(tmp_path):
    wrapper = tmp_path / "wrapper"
    wrapper.mkdir()
    _git(wrapper, "init", "-q")
    path = _write_proof(tmp_path / "wrapper-proof.json", {**PROOF, "wrapper_commit": SHA_NEW})
    assert _run(["check-wrapper-proof", "--file", str(path), "--wrapper", str(wrapper), "--version", "70.83.0"]) == 1


# ----------------------------------------------------------------------------- which wrapper a run checks out

@pytest.mark.parametrize("event, ref_type", [("push", "tag"), ("workflow_dispatch", "branch"), ("workflow_dispatch", None)])
def test_a_tag_and_a_manual_run_check_out_the_commit_of_the_proof_file(tmp_path, event, ref_type):
    mod = _load_script()
    path = _write_proof(tmp_path / "wrapper-proof.json", {**PROOF, "wrapper_commit": SHA_NEW})
    for body in (None, "wrapper-ref: feat/x\n"):  # a declaration in a body is not read on a release
        ref, why = mod.choose_wrapper_ref(event, body, ref_type=ref_type, proof_file=path)
        assert ref == SHA_NEW and "wrapper-proof.json" in why


@pytest.mark.parametrize("event, ref_type", [("push", "tag"), ("workflow_dispatch", "branch")])
def test_a_release_with_no_usable_proof_file_is_red_and_never_falls_back_to_main(tmp_path, event, ref_type):
    mod = _load_script()
    with pytest.raises(mod.WrapperRefError):
        mod.choose_wrapper_ref(event, None, ref_type=ref_type, proof_file=tmp_path / "missing.json")
    path = _write_proof(tmp_path / "wrapper-proof.json", {**PROOF, "wrapper_commit": "abc"})
    with pytest.raises(mod.WrapperRefError):
        mod.choose_wrapper_ref(event, None, ref_type=ref_type, proof_file=path)


def test_a_pull_request_a_push_to_main_and_a_merge_group_do_not_read_the_proof_file(tmp_path):
    mod = _load_script()
    missing = tmp_path / "missing.json"   # reading it would be an error: it is not read
    assert mod.choose_wrapper_ref("pull_request", None, ref_type="branch", proof_file=missing)[0] == "main"
    assert mod.choose_wrapper_ref("push", None, ref_type="branch", proof_file=missing)[0] == "main"
    assert mod.choose_wrapper_ref("merge_group", None, ref_type="branch", proof_file=missing)[0] == "main"
    found = mod.choose_wrapper_ref("pull_request", "wrapper-ref: feat/x\n", ls_remote=lambda b: f"{'a' * 40}\trefs/heads/{b}\n",
                                   ref_type="branch", proof_file=missing)
    assert found[0] == "feat/x"


def test_the_wrapper_ref_command_writes_the_declared_commit_as_the_output_of_a_tag(tmp_path, monkeypatch):
    path = _write_proof(tmp_path / "wrapper-proof.json", {**PROOF, "wrapper_commit": SHA_NEW})
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    assert _run(["wrapper-ref", "--event", "push", "--ref-type", "tag", "--proof-file", str(path)]) == 0
    assert out.read_text(encoding="utf-8").strip() == f"ref={SHA_NEW}"
    assert _run(["wrapper-ref", "--event", "push", "--ref-type", "tag", "--proof-file", str(tmp_path / "missing.json")]) == 1


@pytest.mark.parametrize("name", ["wrapper-on-this-core.yml", "wrapper-unit-on-this-core.yml"])
def test_both_wrapper_jobs_hand_the_ref_type_to_the_chooser_and_check_the_release_they_check_out(name):
    job = name.removesuffix(".yml")
    steps = _load_workflow(name)["jobs"][job]["steps"]
    chooser = next(s for s in steps if "wrapper_on_this_core.py wrapper-ref" in str(s.get("run", "")))
    assert '--ref-type "$REF_TYPE"' in chooser["run"] and "github.ref_type" in str(chooser["env"]["REF_TYPE"]), (
        "the chooser is not told whether the event is a tag: a tag would check out main")
    check = next(s for s in steps if "check-wrapper-proof" in str(s.get("run", "")))
    run = check["run"]
    assert "--file ci/wrapper-proof.json" in run and "--wrapper wrapper" in run and '--version "$VERSION"' in run
    assert "proof-version" in run, "the version the wrapper must pin is not the tag's"
    cond = re.sub(r"\s+", "", str(check.get("if", "")))
    assert "github.event_name=='workflow_dispatch'" in cond and "github.ref_type=='tag'" in cond, "the check does not run on a tag and a dispatch"
    assert steps.index(chooser) < steps.index(check)
    wrapper = next(s for s in steps if (s.get("with") or {}).get("repository") == "oss-invisible/playwright")
    assert steps.index(wrapper) < steps.index(check), "the check runs before the wrapper is checked out"


# ----------------------------------------------------------------------------- the step texts, executed

def _bash() -> str:
    found = shutil.which("bash")
    git = shutil.which("git")
    if os.name == "nt" and git:  # System32's bash.exe is WSL: the one of Git for Windows is next to the git
        candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if candidate.exists():
            return str(candidate)
    assert found, "bash is needed to run the steps of the workflows"
    return found


def _run_step(script: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    py = Path(sys.executable).as_posix()
    script = re.sub(r"\bpython3? (?=(\.\./)?scripts/)", f'"{py}" ', script)
    script = re.sub(r'\bpython (?=("\$RUNNER_TEMP|-c ))', f'"{py}" ', script)   # the judge's own line
    full = {**os.environ, **env, "RUNNER_TEMP": (cwd / "_runner_temp").as_posix(), "GITHUB_ENV": (cwd / "_github_env").as_posix(),
            "GIT_CEILING_DIRECTORIES": cwd.parent.as_posix()}
    (cwd / "_runner_temp").mkdir(exist_ok=True)
    return subprocess.run([_bash(), "--noprofile", "--norc", "-eo", "pipefail", "-c", script], cwd=cwd, env=full, capture_output=True, text=True)


STUB = 'def computed_list(wrapper):\n    return {files!r}\n'
ONE = ["tests/test_x.py"]
MORE = ONE + ["tests/test_the_one_more.py"]


def _core_repo(tmp_path: Path, at_tag: list, at_main: list) -> Path:
    """A core checkout at a release branch: tag v69.82.0 holds a copy of the script that computes ``at_tag``, `origin/main` one that computes
    ``at_main``, HEAD is the real script and the real `released` list of v70.83.0; the wrapper tree has ONE file that stands on the Juggler client."""
    repo = tmp_path / "core"
    (repo / "scripts").mkdir(parents=True)
    _git(repo, "init", "-q")
    script = repo / "scripts" / "wrapper_on_this_core.py"
    script.write_text(STUB.format(files=at_tag), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "tag")
    _git(repo, "tag", "v69.82.0")
    script.write_text(STUB.format(files=at_main), encoding="utf-8")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-am", "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    shutil.copy(SCRIPT, script)
    (repo / "PUBLISHED.json").write_text(json.dumps(_published(CORE_RELEASED_AT_70_83_0)), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "release branch")
    tests = repo / "wrapper" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_x.py").write_text("from invisible_core.juggler import something\n", encoding="utf-8")
    return repo


def _compare(repo: Path, **env) -> subprocess.CompletedProcess:
    run = _step("wrapper-on-this-core.yml", "wrapper-on-this-core", "the list of tests is not shorter than main's")["run"]
    return _run_step(run, repo, {"BEFORE": "0" * 40, "REF_NAME": "", **env})


TAG = {"EVENT": "push", "REF_TYPE": "tag", "REF_NAME": "v70.83.0"}
PULL_REQUEST = {"EVENT": "pull_request", "REF_TYPE": "branch", "REF_NAME": "12/merge"}


def test_on_a_tag_a_base_that_computes_one_file_more_is_red(tmp_path):
    done = _compare(_core_repo(tmp_path, at_tag=MORE, at_main=ONE), **TAG)
    assert done.returncode == 1 and "SHORTER" in done.stderr, done.stderr


def test_on_a_tag_an_equal_base_is_green(tmp_path):
    done = _compare(_core_repo(tmp_path, at_tag=ONE, at_main=ONE), **TAG)
    assert done.returncode == 0, done.stderr


def test_on_a_tag_main_is_not_the_base_the_previous_release_is(tmp_path):
    """A release branch is not main: what main computes (one more file) says nothing about a release cut from a branch main does not have."""
    done = _compare(_core_repo(tmp_path, at_tag=ONE, at_main=MORE), **TAG)
    assert done.returncode == 0, done.stderr
    done = _compare(_core_repo(tmp_path / "other", at_tag=MORE, at_main=ONE), **{**TAG, "REF_NAME": "v70.83.0"})
    assert done.returncode == 1


def test_on_a_manual_run_the_base_is_the_previous_release_of_the_tree_version(tmp_path):
    repo = _core_repo(tmp_path, at_tag=MORE, at_main=ONE)
    tree = repo / "src" / "invisible_core"
    tree.mkdir(parents=True)
    for name in ("_version.py", "seal.json"):
        shutil.copy(ROOT / "src" / "invisible_core" / name, tree / name)
    done = _compare(repo, EVENT="workflow_dispatch", REF_TYPE="branch", REF_NAME="main")
    assert done.returncode == 1 and "SHORTER" in done.stderr, done.stderr
    repo = _core_repo(tmp_path / "eq", at_tag=ONE, at_main=MORE)
    (repo / "src" / "invisible_core").mkdir(parents=True)
    for name in ("_version.py", "seal.json"):
        shutil.copy(ROOT / "src" / "invisible_core" / name, repo / "src" / "invisible_core" / name)
    assert _compare(repo, EVENT="workflow_dispatch", REF_TYPE="branch", REF_NAME="main").returncode == 0


def test_a_tag_with_no_published_release_below_it_is_red(tmp_path):
    repo = _core_repo(tmp_path, at_tag=ONE, at_main=ONE)
    done = _compare(repo, **{**TAG, "REF_NAME": "v18.0.0"})
    assert done.returncode == 1 and "no release below" in done.stderr, done.stderr


def test_a_tag_whose_previous_release_cannot_be_read_is_red(tmp_path):
    repo = _core_repo(tmp_path, at_tag=ONE, at_main=ONE)
    _git(repo, "tag", "-d", "v69.82.0")   # no tag, and no origin to fetch it from
    done = _compare(repo, **TAG)
    assert done.returncode != 0 and "origin" in done.stderr, done.stderr   # the fetch of the tag fails, and the step with it


def test_a_pull_request_still_compares_with_main_and_a_push_with_the_commit_before(tmp_path):
    done = _compare(_core_repo(tmp_path / "a", at_tag=ONE, at_main=MORE), **PULL_REQUEST)
    assert done.returncode == 1 and "SHORTER" in done.stderr, done.stderr
    done = _compare(_core_repo(tmp_path / "b", at_tag=MORE, at_main=ONE), **PULL_REQUEST)
    assert done.returncode == 0, done.stderr   # the tag's script is not read on a pull request
    repo = _core_repo(tmp_path / "c", at_tag=ONE, at_main=ONE)
    before = _git(repo, "rev-parse", "v69.82.0")   # the commit before, whose script computes ONE
    assert _compare(repo, EVENT="push", REF_TYPE="branch", REF_NAME="main", BEFORE=before).returncode == 0
    assert _compare(_core_repo(tmp_path / "d", at_tag=MORE, at_main=MORE), EVENT="push", REF_TYPE="branch", REF_NAME="main",
                    BEFORE=_git(tmp_path / "d" / "core", "rev-parse", "origin/main")).returncode == 1


def test_an_unknown_event_stays_red(tmp_path):
    done = _compare(_core_repo(tmp_path, at_tag=ONE, at_main=ONE), EVENT="schedule", REF_TYPE="branch", REF_NAME="main")
    assert done.returncode == 1 and "not known" in done.stderr


# ----------------------------------------------------------------------------- the judge of the wrapper's unit suite

# A stand-in for the wrapper's ci_verdict.py `judge`: it records what the step handed it (its arguments, and the folder of fragments next to the list
# it was given) in $RUNNER_TEMP/judge-seen.json. The NEW one has the text of the option a judge of the fragments has (`--main-has-no-fragments`, added by
# oss-invisible/playwright 29a93aae, branch ci/required-suites-fragments), which is all the step looks for; the OLD one is a judge with no fragments.
_STUB_JUDGE = """import json, os, pathlib, sys
argv = sys.argv[2:]
folder = pathlib.Path(argv[argv.index("--main-required") + 1]).with_name("required-suites.d")
seen = {"argv": argv, "files": sorted(p.name for p in folder.iterdir()) if folder.is_dir() else None,
        "consumed": (folder / "consumed.json").read_text(encoding="utf-8") if (folder / "consumed.json").exists() else None}
pathlib.Path(os.environ["RUNNER_TEMP"], "judge-seen.json").write_text(json.dumps(seen), encoding="utf-8")
"""
_NEW_JUDGE = "# judge of main, a judge with fragments: --main-has-no-fragments\n" + _STUB_JUDGE
_OLD_JUDGE = "# judge of main, from before the fragments\n" + _STUB_JUDGE


def _judge_files(where: Path, who: str, judge: str | None = None, folder: bool = False) -> None:
    (where / "scripts").mkdir(exist_ok=True)
    (where / "ci").mkdir(exist_ok=True)
    (where / "scripts" / "ci_verdict.py").write_text(judge or f"# judge of {who}\n", encoding="utf-8")
    if folder:
        (where / "ci" / "required-suites.d").mkdir(exist_ok=True)
        (where / "ci" / "required-suites.d" / "consumed.json").write_text(f'{{"folder": "{who}"}}\n', encoding="utf-8")
        (where / "ci" / "required-suites.d" / f"{who.replace(' ', '-')}.json").write_text('{"wrapper.unit.all.linux": 2}\n', encoding="utf-8")
    (where / "ci" / "required-suites.json").write_text(f'{{"list": "{who}"}}\n', encoding="utf-8")
    (where / "ci" / "retired-tests.json").write_text(f'{{"retired": "{who}"}}\n', encoding="utf-8")


def _wrapper_with_origin(tmp_path: Path, version: str, released: list, judge: str | None = None, release_folder: bool = False,
                         main_folder: bool = False) -> Path:
    """A wrapper checkout (one commit, as `actions/checkout` leaves it) whose origin has main and the tag v<previous>."""
    origin = tmp_path / "origin"
    origin.mkdir(parents=True)
    _git(origin, "init", "-q", "-b", "main")
    _judge_files(origin, "the release", judge, release_folder)   # what every released tag holds
    _git(origin, "add", "-A")
    _git(origin, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "release")
    for v in released:
        _git(origin, "tag", f"v{v}")
    shutil.rmtree(origin / "ci" / "required-suites.d", ignore_errors=True)
    _judge_files(origin, "main", judge, main_folder)   # main, and the declared commit on it, have moved on
    (origin / "pyproject.toml").write_text(f'[project]\nname = "invisible_playwright"\nversion = "{version}"\n', encoding="utf-8")
    (origin / "PUBLISHED.json").write_text(json.dumps(_published(released)), encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "the declared commit")
    top = tmp_path / "core"
    (top / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, top / "scripts" / "wrapper_on_this_core.py")
    wrapper = top / "wrapper"
    _git(top, "init", "-q")
    _git(top, "clone", "-q", "--no-tags", origin.as_posix(), "wrapper")
    return wrapper


def _judge_ref(tmp_path: Path, version: str, released: list, **env) -> tuple[subprocess.CompletedProcess, str]:
    wrapper = _wrapper_with_origin(tmp_path, version, released)
    run = _step_running("wrapper-unit-on-this-core.yml", "wrapper-unit-on-this-core", "previous-release")["run"]
    done = _run_step(run, wrapper, env)
    github_env = wrapper / "_github_env"
    return done, (github_env.read_text(encoding="utf-8") if github_env.exists() else "")


def test_the_judge_of_the_unit_suite_on_a_tag_is_the_previous_release_of_the_wrapper(tmp_path):
    done, written = _judge_ref(tmp_path, "0.60.0", WRAPPER_RELEASED_AT_0_60_0, EVENT="push", REF_TYPE="tag")
    assert done.returncode == 0, done.stderr
    assert written.strip() == "JUDGE_REF=refs/tags/v0.59.0"
    done, written = _judge_ref(tmp_path / "d", "0.60.0", WRAPPER_RELEASED_AT_0_60_0, EVENT="workflow_dispatch", REF_TYPE="branch")
    assert done.returncode == 0 and written.strip() == "JUDGE_REF=refs/tags/v0.59.0", done.stderr


def test_the_judge_of_the_unit_suite_on_a_pull_request_and_a_push_is_still_the_wrapper_main(tmp_path):
    for n, (event, ref_type) in enumerate([("pull_request", "branch"), ("push", "branch"), ("merge_group", "branch")]):
        done, written = _judge_ref(tmp_path / str(n), "0.60.0", WRAPPER_RELEASED_AT_0_60_0, EVENT=event, REF_TYPE=ref_type)
        assert done.returncode == 0, done.stderr
        assert written.strip() == "JUDGE_REF=origin/main"


def test_a_wrapper_with_no_release_below_it_has_no_judge_and_is_red(tmp_path):
    done, written = _judge_ref(tmp_path, "0.3.5", WRAPPER_RELEASED_AT_0_60_0, EVENT="push", REF_TYPE="tag")
    assert done.returncode == 1 and "JUDGE_REF" not in written


def _read_for_the_judge(tmp_path: Path, **env) -> dict:
    """Run the judge step up to the line that runs the judge, and say what it read: the judge script, the list, retired-tests."""
    wrapper = _wrapper_with_origin(tmp_path, "0.60.0", WRAPPER_RELEASED_AT_0_60_0)
    chooser = _step_running("wrapper-unit-on-this-core.yml", "wrapper-unit-on-this-core", "previous-release")["run"]
    judge = _step_running("wrapper-unit-on-this-core.yml", "wrapper-unit-on-this-core", 'ci_verdict.py" judge')["run"]
    done = _run_step(chooser, wrapper, env)
    assert done.returncode == 0, done.stderr
    ref = (wrapper / "_github_env").read_text(encoding="utf-8").strip().split("=", 1)[1]
    lines = [line for line in judge.splitlines() if 'ci_verdict.py" judge' not in line]   # everything but the run of the judge itself
    done = _run_step("\n".join(lines), wrapper, {**env, "JUDGE_REF": ref})
    assert done.returncode == 0, done.stderr
    main = wrapper / "_runner_temp" / "main"
    return {name: (main / name).read_text(encoding="utf-8").strip() for name in ("ci_verdict.py", "required-suites.json", "retired-tests.json")}


def test_on_a_tag_the_judge_is_mains_and_the_list_is_the_previous_releases(tmp_path):
    """Every judge is read from main (a release must meet the newer rules too); only the list a release is compared with is the previous one."""
    for n, env in enumerate(({"EVENT": "push", "REF_TYPE": "tag"}, {"EVENT": "workflow_dispatch", "REF_TYPE": "branch"})):
        read = _read_for_the_judge(tmp_path / str(n), **env)
        assert read == {"ci_verdict.py": "# judge of main", "required-suites.json": '{"list": "the release"}',
                        "retired-tests.json": '{"retired": "the release"}'}, read


def test_on_a_pull_request_and_a_push_the_judge_and_the_list_both_come_from_main(tmp_path):
    for n, event in enumerate(("pull_request", "push", "merge_group")):
        read = _read_for_the_judge(tmp_path / str(n), EVENT=event, REF_TYPE="branch")
        assert read == {"ci_verdict.py": "# judge of main", "required-suites.json": '{"list": "main"}',
                        "retired-tests.json": '{"retired": "main"}'}, read


def _run_the_judge_step(tmp_path: Path, judge: str, release_folder: bool, main_folder: bool, **env) -> tuple[dict, Path]:
    """Both steps of the unit job, as written, with the judge replaced by a stand-in that records what it was given."""
    wrapper = _wrapper_with_origin(tmp_path, "0.60.0", WRAPPER_RELEASED_AT_0_60_0, judge, release_folder, main_folder)
    chooser = _step_running("wrapper-unit-on-this-core.yml", "wrapper-unit-on-this-core", "previous-release")["run"]
    step = _step_running("wrapper-unit-on-this-core.yml", "wrapper-unit-on-this-core", 'ci_verdict.py" judge')["run"]
    done = _run_step(chooser, wrapper, env)
    assert done.returncode == 0, done.stderr
    ref = (wrapper / "_github_env").read_text(encoding="utf-8").strip().split("=", 1)[1]
    done = _run_step(step, wrapper, {**env, "JUDGE_REF": ref, "SUITE_OS": "linux"})
    assert done.returncode == 0, done.stderr
    return json.loads((wrapper / "_runner_temp" / "judge-seen.json").read_text(encoding="utf-8")), wrapper


RELEASE_EVENT = {"EVENT": "push", "REF_TYPE": "tag"}
PULL_REQUEST_EVENT = {"EVENT": "pull_request", "REF_TYPE": "branch"}


@pytest.mark.parametrize("env, who", [(RELEASE_EVENT, "the-release"), (PULL_REQUEST_EVENT, "main")])
def test_a_ref_that_has_the_folder_of_fragments_has_it_copied_next_to_the_list_and_no_flag_is_passed(tmp_path, env, who):
    seen, wrapper = _run_the_judge_step(tmp_path, _NEW_JUDGE, release_folder=True, main_folder=True, **env)
    assert seen["files"] == ["consumed.json", f"{who}.json"], seen   # the folder of THE REF the list was read from, whole
    assert json.loads(seen["consumed"]) == {"folder": who.replace("-", " ")}
    assert "--main-has-no-fragments" not in seen["argv"], seen
    assert seen["argv"][seen["argv"].index("--main-required") + 1].endswith("main/required-suites.json")


@pytest.mark.parametrize("env", [RELEASE_EVENT, PULL_REQUEST_EVENT])
def test_a_ref_with_no_folder_and_a_judge_that_knows_the_flag_passes_it(tmp_path, env):
    seen, _ = _run_the_judge_step(tmp_path, _NEW_JUDGE, release_folder=False, main_folder=False, **env)
    assert seen["files"] is None and seen["argv"][-1] == "--main-has-no-fragments", seen


@pytest.mark.parametrize("env", [RELEASE_EVENT, PULL_REQUEST_EVENT])
def test_a_ref_with_no_folder_and_a_judge_from_before_the_fragments_passes_nothing(tmp_path, env):
    seen, _ = _run_the_judge_step(tmp_path, _OLD_JUDGE, release_folder=False, main_folder=False, **env)
    assert seen["files"] is None and "--main-has-no-fragments" not in seen["argv"], seen
    assert seen["argv"][-1].endswith("main/required-suites.json"), seen


def test_a_release_whose_tag_has_the_folder_while_main_has_not_reads_the_folder_of_the_tag(tmp_path):
    """The folder follows the ref the list is read from, not main: a tag newer than the fragments hands its own."""
    seen, _ = _run_the_judge_step(tmp_path, _NEW_JUDGE, release_folder=True, main_folder=False, **RELEASE_EVENT)
    assert seen["files"] == ["consumed.json", "the-release.json"] and "--main-has-no-fragments" not in seen["argv"], seen


def test_the_fragments_are_copied_by_a_step_that_is_red_when_the_archive_or_the_extract_fails():
    run = _step_running("wrapper-unit-on-this-core.yml", "wrapper-unit-on-this-core", 'ci_verdict.py" judge')["run"]
    assert 'git archive "$JUDGE_REF" ci/required-suites.d | tar -x' in run
    assert "|| true" not in run and "|| :" not in run, "a failed archive or extract would read as `no folder`"
    assert "set +e" not in run and "set +o pipefail" not in run


def test_the_unit_judge_script_is_always_origin_main_and_the_list_ref_cannot_be_unset():
    steps = _load_workflow("wrapper-unit-on-this-core.yml")["jobs"]["wrapper-unit-on-this-core"]["steps"]
    run = next(s for s in steps if 'ci_verdict.py" judge' in str(s.get("run", "")))["run"]
    assert "git show origin/main:scripts/ci_verdict.py" in run and "JUDGE_REF:scripts" not in run, "the judge script is not main's"
    assert re.search(r':\s*"\$\{JUDGE_REF:\?', run), "an unset JUDGE_REF would read the index (`git show :path`) instead of failing"
    assert '"$JUDGE_REF:ci/required-suites.json"' in run and '"$JUDGE_REF:ci/retired-tests.json"' in run
    chooser = next(s for s in steps if "previous-release" in str(s.get("run", "")))["run"]
    assert chooser.index("fetch --no-tags --depth=1 origin main:refs/remotes/origin/main") < chooser.index("if ["), (
        "origin/main is fetched on a tag only if the branch that fetches it is taken")


# ----------------------------------------------------------------------------- ci.yml: the same checks on every pull request and push

def test_ci_checks_the_proof_file_on_every_pull_request_and_push_in_steps_of_an_existing_job():
    ci = _load_workflow("ci.yml")
    on = ci["on"]
    assert "pull_request" in on and "push" in on and "merge_group" in on
    job = ci["jobs"]["wrapper-job-needed"]
    steps = job["steps"]
    step = next((s for s in steps if "check-wrapper-proof" in str(s.get("run", ""))), None)
    assert step, "wrapper-job-needed has no step that checks ci/wrapper-proof.json"
    assert all("if" not in s for s in steps) and "if" not in job, "the check can be skipped on a pull request or a push"
    reader = next(s for s in steps if "read-wrapper-proof --file ci/wrapper-proof.json" in str(s.get("run", "")))
    assert re.search(r'echo "sha=\$SHA" >> "\$GITHUB_OUTPUT"', reader["run"]) and reader.get("id")
    assert re.search(r'check-wrapper-proof --file ci/wrapper-proof.json --wrapper wrapper-proof\s*\\?\s*--version "\$\(python3 scripts/wrapper_on_this_core\.py tree-version\)"', step["run"])
    assert "wrapper-job-needed" in ci["jobs"]["gate"]["needs"], "`gate` does not wait for the job that carries the check"
    assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] <= 5, "the check is not in a light job"
    others = [n for n, j in ci["jobs"].items() if n != "wrapper-job-needed" and "wrapper-proof.json" in str(j)]
    assert not others, f"the check is also in {others}: one place"
    assert "actions/checkout" in str(steps[0].get("uses", ""))
    assert [s for s in steps if "wrapper_job_needed.py" in str(s.get("run", ""))][-1] is steps[-1], (
        "the decision of `wrapper-job-needed` is not the last step any more")


def test_the_wrapper_commit_is_fetched_with_the_jobs_token_by_a_checkout_at_the_declared_sha_and_never_anonymously():
    """Anonymous calls from the runners share 60 an hour per address: the step could go red for a reason outside our code."""
    job = _load_workflow("ci.yml")["jobs"]["wrapper-job-needed"]
    steps = job["steps"]
    for s in steps:
        run = str(s.get("run", ""))
        assert not re.search(r"git\b[^\n]*\bfetch\b[^\n]*https?://", run), f"an anonymous fetch in {s.get('name')!r}"
        assert "curl" not in run and "wget" not in run
    reader = next(s for s in steps if "read-wrapper-proof" in str(s.get("run", "")))
    checkout = next(s for s in steps if (s.get("with") or {}).get("repository") == "oss-invisible/playwright")
    assert checkout["uses"] == steps[0]["uses"], "the checkout is not the same pinned action the file already uses"
    assert re.fullmatch(r"actions/checkout@[0-9a-f]{40}", checkout["uses"])
    assert re.sub(r"\s+", "", checkout["with"]["ref"]) == "${{steps." + reader["id"] + ".outputs.sha}}", "the checkout is not at the declared sha"
    assert checkout["with"]["path"] == "wrapper-proof"
    assert re.sub(r"\s+", "", str(checkout["with"].get("token", "${{github.token}}"))) == "${{github.token}}"
    check = next(s for s in steps if "check-wrapper-proof" in str(s.get("run", "")))
    assert steps.index(reader) < steps.index(checkout) < steps.index(check)


# ----------------------------------------------------------------------------- the series run the proof file names

import io  # noqa: E402
import zipfile  # noqa: E402

SEAL = json.loads((ROOT / "src" / "invisible_core" / "seal.json").read_text(encoding="utf-8"))
OTHER_COMMIT = "a" * 40


def _plan(**over) -> dict:
    plan = {"run_id": 45900000001, "series_commit": SEAL["series_commit"], "wrapper_commit": SHA_NEW, "core_ref": "oss-invisible/core@ci/x",
            "kind": "candidate"}
    plan.update(over)
    return plan


def _zip_of(plan) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("release-plan.json", json.dumps(plan))
    return buffer.getvalue()


def _run_record(number=45900000001, conclusion="success", status="completed", head=SERIES_COMMIT) -> dict:
    # the shape of GET /repos/oss-invisible/firefox_antidetect_patch/actions/runs/45854283146 (read 28/11): id, status, conclusion, head_sha
    return {"id": number, "status": status, "conclusion": conclusion, "head_sha": head, "name": "release", "event": "workflow_dispatch"}


def test_the_seal_of_this_tree_names_the_series_commit_the_first_content_was_made_before_the_rule_for():
    assert SEAL["series_commit"] == SERIES_COMMIT


def test_the_writer_writes_the_two_keys_from_a_candidate_plan(tmp_path, capsys):
    plan = tmp_path / "release-plan.json"
    plan.write_text(json.dumps(_plan()), encoding="utf-8")
    out = tmp_path / "wrapper-proof.json"
    assert _run(["write-wrapper-proof", "--plan", str(plan), "--seal", str(ROOT / "src" / "invisible_core" / "seal.json"), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {"wrapper_commit": SHA_NEW, "series_run": 45900000001}
    assert _load_script().read_proof(out) == {"wrapper_commit": SHA_NEW, "series_run": 45900000001}


@pytest.mark.parametrize("over, why", [
    ({"kind": "build"}, "not a candidate"),
    ({"kind": "release"}, "not a candidate"),
    ({"series_commit": OTHER_COMMIT}, "the plan of another series commit"),
    ({"wrapper_commit": "abc"}, "a short wrapper sha"),
    ({"run_id": "45900000001"}, "a run id that is not a number"),
    ({"core_ref": ""}, "no core ref"),
])
def test_the_writer_refuses_a_plan_that_is_not_a_candidates_or_not_for_this_seal(tmp_path, over, why):
    plan = tmp_path / "release-plan.json"
    plan.write_text(json.dumps(_plan(**over)), encoding="utf-8")
    out = tmp_path / "wrapper-proof.json"
    assert _run(["write-wrapper-proof", "--plan", str(plan), "--seal", str(ROOT / "src" / "invisible_core" / "seal.json"), "--out", str(out)]) == 1, why
    assert not out.exists(), "a refused plan left a proof file"


def test_the_writer_refuses_a_plan_with_a_missing_key_and_a_plan_that_is_not_json(tmp_path):
    seal = str(ROOT / "src" / "invisible_core" / "seal.json")
    out = tmp_path / "wrapper-proof.json"
    plan = tmp_path / "p.json"
    plan.write_text(json.dumps({k: v for k, v in _plan().items() if k != "wrapper_commit"}), encoding="utf-8")
    assert _run(["write-wrapper-proof", "--plan", str(plan), "--seal", seal, "--out", str(out)]) == 1
    plan.write_text("{nope", encoding="utf-8")
    assert _run(["write-wrapper-proof", "--plan", str(plan), "--seal", seal, "--out", str(out)]) == 1
    assert _run(["write-wrapper-proof", "--plan", str(tmp_path / "none.json"), "--seal", seal, "--out", str(out)]) == 1
    assert not out.exists()


def _check_plan(proof, run, plan=None, seal=SEAL, zipped=True):
    return _load_script().check_release_plan(proof, seal, run, _zip_of(plan) if (plan is not None and zipped) else None)


def test_a_plan_that_names_the_wrapper_commit_and_the_seal_of_a_successful_run_is_green():
    proof = {"wrapper_commit": SHA_NEW, "series_run": 45900000001}
    assert _check_plan(proof, _run_record(), _plan()) == []


@pytest.mark.parametrize("proof_over, run, plan_over, needle", [
    ({"wrapper_commit": "c" * 40}, _run_record(), {}, "proved wrapper"),                   # the file names another wrapper commit
    ({}, _run_record(conclusion="failure"), {}, "did not conclude success"),
    ({}, _run_record(conclusion=None, status="in_progress"), {}, "did not conclude success"),
    ({}, _run_record(conclusion="cancelled"), {}, "did not conclude success"),
    ({}, _run_record(number=7), {}, "not that of run"),                                    # the record is another run's
    ({}, _run_record(), {"series_commit": OTHER_COMMIT}, "is for series commit"),          # the plan was made for another seal
    ({}, _run_record(), {"run_id": 45900000002}, "the plan names run"),
])
def test_the_check_is_red_on_a_wrapper_commit_mismatch_a_run_that_did_not_succeed_or_a_plan_of_another_seal(proof_over, run, plan_over, needle):
    proof = {"wrapper_commit": SHA_NEW, "series_run": 45900000001, **proof_over}
    problems = _check_plan(proof, run, _plan(**plan_over))
    assert problems and any(needle in p for p in problems), problems


def test_the_check_is_red_on_a_plan_that_is_not_a_candidates_or_cannot_be_read():
    proof = {"wrapper_commit": SHA_NEW, "series_run": 45900000001}
    assert any("candidate" in p for p in _check_plan(proof, _run_record(), _plan(kind="build")))
    bad_zip = _load_script().check_release_plan(proof, SEAL, _run_record(), b"not a zip")
    assert bad_zip and "cannot be read" in bad_zip[0]
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("other.json", "{}")
    assert _load_script().check_release_plan(proof, SEAL, _run_record(), empty.getvalue())


def test_no_plan_is_accepted_only_for_the_release_made_before_the_rule_and_only_on_its_own_seal():
    mod = _load_script()
    before = {"wrapper_commit": SHA_NEW, "series_run": SERIES_RUN}
    assert mod.MADE_BEFORE_THE_RULE == {SERIES_COMMIT: SERIES_RUN}, "one entry, keyed to the series commit of firefox-70"
    assert mod.check_release_plan(before, SEAL, _run_record(SERIES_RUN), None) == []
    # another run, with no plan
    assert any("no `release-plan` artifact" in p for p in mod.check_release_plan({**before, "series_run": SERIES_RUN + 1}, SEAL, _run_record(SERIES_RUN + 1), None))
    # the same run number under another seal
    other_seal = {**SEAL, "series_commit": OTHER_COMMIT}
    assert any("no `release-plan` artifact" in p for p in mod.check_release_plan(before, other_seal, _run_record(SERIES_RUN, head=OTHER_COMMIT), None))
    # the right run, but it did not run the seal's commit, or did not succeed
    assert any("not the series commit" in p for p in mod.check_release_plan(before, SEAL, _run_record(SERIES_RUN, head=OTHER_COMMIT), None))
    assert any("did not conclude success" in p for p in mod.check_release_plan(before, SEAL, _run_record(SERIES_RUN, conclusion="failure"), None))


def test_artifact_id_names_the_live_release_plan_and_two_are_red():
    mod = _load_script()
    assert mod.artifact_id({"total_count": 0, "artifacts": []}) == ""
    assert mod.artifact_id({"artifacts": [{"id": 9, "name": "release-plan", "expired": False}, {"id": 8, "name": "junit-py", "expired": False}]}) == "9"
    assert mod.artifact_id({"artifacts": [{"id": 9, "name": "release-plan", "expired": True}]}) == ""
    with pytest.raises(mod.ProofError):
        mod.artifact_id({"artifacts": [{"id": 1, "name": "release-plan"}, {"id": 2, "name": "release-plan"}]})
    with pytest.raises(mod.ProofError):
        mod.artifact_id({})


# the step of ci.yml, executed with a stand-in for `gh` that answers from files (no network)
_FAKE_GH = r"""gh() {
  [ "$1" = "api" ] || { echo "gh $*: not an api call" >&2; return 2; }
  case "$2" in
    repos/oss-invisible/firefox_antidetect_patch/actions/runs/*/artifacts*) cat "$FAKE/artifacts.json" ;;
    repos/oss-invisible/firefox_antidetect_patch/actions/artifacts/*/zip) cat "$FAKE/plan.zip" ;;
    repos/oss-invisible/firefox_antidetect_patch/actions/runs/*) cat "$FAKE/run.json" ;;
    *) echo "gh: unexpected $2" >&2; return 1 ;;
  esac
}
"""


def _series_step(tmp_path: Path, proof: dict, run: dict, artifacts: dict, plan_zip: bytes | None, seal=SEAL) -> subprocess.CompletedProcess:
    core = tmp_path / "core"
    (core / "scripts").mkdir(parents=True)
    (core / "ci").mkdir()
    (core / "src" / "invisible_core").mkdir(parents=True)
    shutil.copy(SCRIPT, core / "scripts" / "wrapper_on_this_core.py")
    (core / "ci" / "wrapper-proof.json").write_text(json.dumps(proof), encoding="utf-8")
    (core / "src" / "invisible_core" / "seal.json").write_text(json.dumps(seal), encoding="utf-8")
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "run.json").write_text(json.dumps(run), encoding="utf-8")
    (fake / "artifacts.json").write_text(json.dumps(artifacts), encoding="utf-8")
    if plan_zip is not None:
        (fake / "plan.zip").write_bytes(plan_zip)   # no file: the download fails
    step = _step("ci.yml", "wrapper-job-needed", "the series run it names ran that wrapper commit")
    assert "GH_TOKEN" in step["env"] and "github.token" in step["env"]["GH_TOKEN"]
    return _run_step(_FAKE_GH + step["run"], core, {"FAKE": fake.as_posix()})


LISTED = {"total_count": 1, "artifacts": [{"id": 777, "name": "release-plan", "expired": False}]}
NONE_LISTED = {"total_count": 0, "artifacts": []}


def test_the_step_is_green_when_the_downloaded_plan_matches(tmp_path):
    proof = {"wrapper_commit": SHA_NEW, "series_run": 45900000001}
    done = _series_step(tmp_path, proof, _run_record(), LISTED, _zip_of(_plan()))
    assert done.returncode == 0, done.stderr
    assert "names this wrapper commit" in done.stdout


def test_the_step_is_red_on_a_wrapper_commit_mismatch(tmp_path):
    proof = {"wrapper_commit": "c" * 40, "series_run": 45900000001}
    done = _series_step(tmp_path, proof, _run_record(), LISTED, _zip_of(_plan()))
    assert done.returncode == 1 and "proved wrapper" in done.stderr, done.stderr


def test_the_step_is_red_on_a_run_that_failed(tmp_path):
    proof = {"wrapper_commit": SHA_NEW, "series_run": 45900000001}
    done = _series_step(tmp_path, proof, _run_record(conclusion="failure"), LISTED, _zip_of(_plan()))
    assert done.returncode == 1 and "did not conclude success" in done.stderr, done.stderr


def test_the_step_is_red_when_the_download_fails_and_never_reads_it_as_no_plan(tmp_path):
    proof = {"wrapper_commit": SHA_NEW, "series_run": 45900000001}
    done = _series_step(tmp_path, proof, _run_record(), LISTED, None)   # the listing names an artifact whose zip cannot be downloaded
    assert done.returncode != 0
    done = _series_step(tmp_path / "again", {**proof, "series_run": SERIES_RUN}, _run_record(SERIES_RUN), LISTED, None)
    assert done.returncode != 0, "a failed download of the pre-rule run's artifact was read as `no plan`"


def test_the_step_is_red_on_a_run_that_has_no_plan_unless_it_is_the_one_made_before_the_rule(tmp_path):
    proof = {"wrapper_commit": SHA_NEW, "series_run": 45900000001}
    done = _series_step(tmp_path, proof, _run_record(), NONE_LISTED, None)
    assert done.returncode == 1 and "no `release-plan` artifact" in done.stderr, done.stderr
    done = _series_step(tmp_path / "pre", {"wrapper_commit": SHA_NEW, "series_run": SERIES_RUN}, _run_record(SERIES_RUN), NONE_LISTED, None)
    assert done.returncode == 0, done.stderr
    assert "made before the rule" in done.stdout


def test_the_step_is_red_on_a_proof_file_with_no_series_run(tmp_path):
    done = _series_step(tmp_path, {"wrapper_commit": SHA_NEW}, _run_record(), NONE_LISTED, None)
    assert done.returncode == 1 and "series_run" in done.stderr


def test_the_series_step_runs_on_every_event_in_the_same_light_job_with_the_jobs_token():
    job = _load_workflow("ci.yml")["jobs"]["wrapper-job-needed"]
    steps = job["steps"]
    step = _step("ci.yml", "wrapper-job-needed", "the series run it names ran that wrapper commit")
    assert "if" not in step and step in steps
    assert steps.index(step) > steps.index(next(s for s in steps if "check-wrapper-proof" in str(s.get("run", ""))))
    assert steps[-1] is not step, "the decision of `wrapper-job-needed` is not the last step any more"
    run = step["run"]
    assert "check-release-plan" in run and "artifact-id" in run and "read-series-run" in run
    assert "git fetch" not in run and "curl" not in run
    assert [s for s in steps if "wrapper_job_needed.py" in str(s.get("run", ""))][-1] is steps[-1]


def test_the_tag_path_reads_the_two_keys_but_does_not_call_the_series():
    for name in ("wrapper-on-this-core.yml", "wrapper-unit-on-this-core.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert "check-release-plan" not in text and "firefox_antidetect_patch" not in text, f"{name} asks the series repository on the tag"
