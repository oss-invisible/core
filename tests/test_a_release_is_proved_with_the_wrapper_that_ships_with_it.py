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
    # core main's _version.py is the number the wrapper release pins (70.83.0): the first content of the file.
    assert mod.tree_version(ROOT) == "70.83.0"


@pytest.mark.parametrize("content", [
    {"wrapper_commit": SHA_NEW[:7]},                     # a short sha
    {"wrapper_commit": SHA_NEW.upper()},                 # not lower case
    {"wrapper_commit": SHA_NEW + "0"},
    {"wrapper_commit": 42},
    {"wrapper_commit": "main"},
    {"wrapper_commit": SHA_NEW, "note": "extra key"},
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
        _write_proof(path, {"wrapper_commit": declare or head})
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
    assert _check(tmp_path, PYPROJECT_B39B7F65, "70.83.0", proof={"wrapper_commit": SHA_NEW[:7]}) == 1
    (tmp_path / "x").mkdir()
    assert _check(tmp_path / "x", PYPROJECT_B39B7F65, "70.83.0", proof=False) == 1


def test_a_checkout_with_no_pyproject_is_red(tmp_path):
    wrapper = tmp_path / "wrapper"
    wrapper.mkdir()
    _git(wrapper, "init", "-q")
    path = _write_proof(tmp_path / "wrapper-proof.json", {"wrapper_commit": SHA_NEW})
    assert _run(["check-wrapper-proof", "--file", str(path), "--wrapper", str(wrapper), "--version", "70.83.0"]) == 1


# ----------------------------------------------------------------------------- which wrapper a run checks out

@pytest.mark.parametrize("event, ref_type", [("push", "tag"), ("workflow_dispatch", "branch"), ("workflow_dispatch", None)])
def test_a_tag_and_a_manual_run_check_out_the_commit_of_the_proof_file(tmp_path, event, ref_type):
    mod = _load_script()
    path = _write_proof(tmp_path / "wrapper-proof.json", {"wrapper_commit": SHA_NEW})
    for body in (None, "wrapper-ref: feat/x\n"):  # a declaration in a body is not read on a release
        ref, why = mod.choose_wrapper_ref(event, body, ref_type=ref_type, proof_file=path)
        assert ref == SHA_NEW and "wrapper-proof.json" in why


@pytest.mark.parametrize("event, ref_type", [("push", "tag"), ("workflow_dispatch", "branch")])
def test_a_release_with_no_usable_proof_file_is_red_and_never_falls_back_to_main(tmp_path, event, ref_type):
    mod = _load_script()
    with pytest.raises(mod.WrapperRefError):
        mod.choose_wrapper_ref(event, None, ref_type=ref_type, proof_file=tmp_path / "missing.json")
    path = _write_proof(tmp_path / "wrapper-proof.json", {"wrapper_commit": "abc"})
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
    path = _write_proof(tmp_path / "wrapper-proof.json", {"wrapper_commit": SHA_NEW})
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
    script = re.sub(r"\bpython (?=(\.\./)?scripts/)", f'"{py}" ', script)
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

def _judge_files(where: Path, who: str) -> None:
    (where / "scripts").mkdir(exist_ok=True)
    (where / "ci").mkdir(exist_ok=True)
    (where / "scripts" / "ci_verdict.py").write_text(f"# judge of {who}\n", encoding="utf-8")
    (where / "ci" / "required-suites.json").write_text(f'{{"list": "{who}"}}\n', encoding="utf-8")
    (where / "ci" / "retired-tests.json").write_text(f'{{"retired": "{who}"}}\n', encoding="utf-8")


def _wrapper_with_origin(tmp_path: Path, version: str, released: list) -> Path:
    """A wrapper checkout (one commit, as `actions/checkout` leaves it) whose origin has main and the tag v<previous>."""
    origin = tmp_path / "origin"
    origin.mkdir(parents=True)
    _git(origin, "init", "-q", "-b", "main")
    _judge_files(origin, "the release")   # what every released tag holds
    _git(origin, "add", "-A")
    _git(origin, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "release")
    for v in released:
        _git(origin, "tag", f"v{v}")
    _judge_files(origin, "main")          # main, and the declared commit on it, have moved on
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

def test_ci_checks_the_proof_file_on_every_pull_request_and_push_in_a_step_of_an_existing_job():
    ci = _load_workflow("ci.yml")
    on = ci["on"]
    assert "pull_request" in on and "push" in on and "merge_group" in on
    job = ci["jobs"]["wrapper-job-needed"]
    step = next((s for s in job["steps"] if "check-wrapper-proof" in str(s.get("run", ""))), None)
    assert step, "wrapper-job-needed has no step that checks ci/wrapper-proof.json"
    assert "if" not in step and "if" not in job, "the check can be skipped on a pull request or a push"
    run = step["run"]
    assert "read-wrapper-proof --file ci/wrapper-proof.json" in run
    assert "fetch -q --depth=1 https://github.com/oss-invisible/playwright" in run, "the commit is not looked up in the wrapper's repository"
    assert re.search(r'check-wrapper-proof --file ci/wrapper-proof.json --wrapper "\$WRAPPER_DIR"\s*\\?\s*--version "\$\(python3 scripts/wrapper_on_this_core\.py tree-version\)"', run)
    assert "wrapper-job-needed" in ci["jobs"]["gate"]["needs"], "`gate` does not wait for the job that carries the check"
    assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] <= 5, "the check is not in a light job"
    others = [n for n, j in ci["jobs"].items() if n != "wrapper-job-needed" and "wrapper-proof.json" in str(j)]
    assert not others, f"the check is also in {others}: one place"
    steps = job["steps"]
    assert steps.index(step) > 0 and "actions/checkout" in str(steps[0].get("uses", ""))
    assert [s for s in steps if "wrapper_job_needed.py" in str(s.get("run", ""))][-1] is steps[-1], (
        "the decision of `wrapper-job-needed` is not the last step any more")
