"""Does a run have to try the wrapper on this core? The rules of scripts/wrapper_job_needed.py (ci-12).

A core change that breaks the wrapper must be red in the core's own rounds. Core PR #328 changed `src/` and broke the wrapper main's unit tests,
and nothing caught it: the job that tries the wrapper on the core was skipped on the pull request, because the decision wanted a `release/*`
branch, a `wrapper-ref:` line or a change of the job itself, and a change of the package was none of them.

What is held here:

  1. A pull request that changes only `src/invisible_core/x.py` needs the job on the new rule, and did NOT on the old one (the inline step of
     c17bc63, kept below as OLD_DECIDE and run for real: it says `run=false`).
  2. `pyproject.toml` and a lock file of the root are part of what the installed core is made of; a docs-only change, a test of the core and a
     script that is not the job's are not, and say why.
  3. A push, a `release/*` head branch, a `wrapper-ref:` line in the body and a file of the job itself still need it.
  4. The reason is written to the step summary; a diff that cannot be read is exit 1, never "not needed".
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "wrapper_job_needed.py"

#: The `decide` step of `wrapper-job-needed` as it was at c17bc63, before the script: what the rule was when core PR #328 went through.
OLD_DECIDE = r'''# A diff that cannot be read is red (set -e), never "nothing changed".
set -e
run=false
if [ "$EVENT" = "push" ]; then
  run=true
else
  case "$HEAD_REF" in release/*) run=true ;; esac
  # A pull request that declares a wrapper branch wants it tried: a loose look at
  # the body (the grammar is judged by the job itself, which is red on a bad line).
  if printf '%s\n' "${PR_BODY:-}" | grep -Eiq '^wrapper-ref[[:space:]]*:'; then run=true; fi
  git diff --name-only "$BASE_SHA...$HEAD_SHA" > "$RUNNER_TEMP/changed.txt"
  while IFS= read -r path; do
    case "$path" in
      .github/workflows/ci.yml|scripts/wrapper_on_this_core.py|scripts/wrapper_test_seconds.json|tests/test_the_wrapper_job_runs_on_the_core.py)
        run=true ;;
    esac
  done < "$RUNNER_TEMP/changed.txt"
fi
echo "run=$run" >> "$GITHUB_OUTPUT"
echo "wrapper-on-this-core is needed: $run"
'''


def _mod():
    spec = importlib.util.spec_from_file_location("wrapper_job_needed_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _repo(tmp_path: Path, changed: list[str]) -> tuple[Path, str, str]:
    """A throwaway repository whose head commit changes ``changed``: (repo, base, head)."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t" "@example.invalid"]

    def run(*cmd):
        return subprocess.run(list(cmd), cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

    run(*git, "init", "-q")
    (repo / "keep.txt").write_text("a", encoding="utf-8")
    run(*git, "add", "-A")
    run(*git, "commit", "-q", "-m", "base")
    base = run("git", "rev-parse", "HEAD")
    for rel in changed:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("b", encoding="utf-8")
    (repo / "keep.txt").write_text("b", encoding="utf-8")
    run(*git, "add", "-A")
    run(*git, "commit", "-q", "-m", "head")
    return repo, base, run("git", "rev-parse", "HEAD")


def _env(tmp_path, base, head, event="pull_request", head_ref="fix/x", body=""):
    return dict(os.environ, EVENT=event, HEAD_REF=head_ref, BASE_SHA=base, HEAD_SHA=head, PR_BODY=body, RUNNER_TEMP=str(tmp_path),
                GITHUB_OUTPUT=str(tmp_path / "out.txt"), GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _old_rule(tmp_path, changed, head_ref="fix/x", body=""):
    """The old inline step, run for real on a diff: its output."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash on this machine")
    repo, base, head = _repo(tmp_path, changed)
    script = tmp_path / "old_decide.sh"
    script.write_text(OLD_DECIDE, encoding="utf-8", newline="\n")
    done = subprocess.run([bash, str(script)], cwd=repo, env=_env(tmp_path, base, head, head_ref=head_ref, body=body),
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return _read(tmp_path / "out.txt").strip()


def _new_rule(tmp_path, changed, event="pull_request", head_ref="fix/x", body=""):
    """The script, run for real on a diff: (exit code, output, summary)."""
    repo, base, head = _repo(tmp_path, changed)
    done = subprocess.run([sys.executable, str(SCRIPT)], cwd=repo, env=_env(tmp_path, base, head, event, head_ref, body),
                          capture_output=True, text=True)
    return done.returncode, _read(tmp_path / "out.txt").strip(), _read(tmp_path / "summary.md")


def test_a_change_of_the_package_alone_needs_the_job_and_did_not_on_the_old_rule(tmp_path):
    changed = ["src/invisible_core/x.py"]
    assert _old_rule(tmp_path / "old", changed) == "run=false", "the old rule was not the gap this test says it was"
    code, out, summary = _new_rule(tmp_path / "new", changed)
    assert (code, out) == (0, "run=true")
    assert "REQUIRED" in summary and "src/invisible_core/x.py" in summary and "installed core" in summary


@pytest.mark.parametrize("changed", [["pyproject.toml"], ["uv.lock"], ["requirements.txt"], ["requirements-dev.txt"],
                                     ["constraints.txt"], ["pylock.toml"], ["src/invisible_core/seal.json"],
                                     ["src/invisible_core/data/table.json"]])
def test_what_the_installed_core_is_made_of_needs_the_job(tmp_path, changed):
    code, out, summary = _new_rule(tmp_path, changed + ["docs/a.md"])
    assert (code, out) == (0, "run=true"), summary
    assert changed[0] in summary


@pytest.mark.parametrize("changed", [["docs/a.md"], ["README.md", "docs/b.md"], ["tests/test_other.py"], ["scripts/other.py"],
                                     ["ci/regressions.json"], ["PUBLISHED.json"], ["tests/requirements.txt"],
                                     ["srcs/x.py"], ["docs/src/x.py"]])
def test_a_change_that_is_not_the_core_is_not_needed_with_its_reason(tmp_path, changed):
    code, out, summary = _new_rule(tmp_path, changed)
    assert (code, out) == (0, "run=false")
    assert "not needed" in summary and "nothing in the change" in summary


def test_a_push_a_release_branch_a_declared_wrapper_branch_and_a_file_of_the_job_still_need_it(tmp_path):
    cases = [("push", "", [], "", "a push to main"),
             ("pull_request", "release/70.1.0", ["docs/a.md"], "", "release branch"),
             ("pull_request", "fix/x", ["docs/a.md"], "text\r\nwrapper-ref: feat/x\r\n", "wrapper-ref"),
             ("pull_request", "fix/x", ["scripts/wrapper_on_this_core.py"], "", "file of the job itself")]
    for n, (event, head_ref, changed, body, why) in enumerate(cases):
        code, out, summary = _new_rule(tmp_path / str(n), changed, event, head_ref, body)
        assert (code, out) == (0, "run=true"), (event, head_ref, changed)
        assert why in summary
    code, out, _ = _new_rule(tmp_path / "ordinary", ["docs/a.md"], body="see wrapper-ref: feat/x in the other pull request")
    assert (code, out) == (0, "run=false")


def test_the_jobs_own_files_exist_and_include_the_script_and_its_test():
    mod = _mod()
    for rel in mod.JOB_FILES:
        assert (ROOT / rel).exists(), rel
    assert {".github/workflows/ci.yml", "scripts/wrapper_job_needed.py", "tests/test_wrapper_job_needed.py"} <= set(mod.JOB_FILES)


def test_the_decision_in_process_is_the_same_as_the_script_says():
    mod = _mod()
    assert mod.decide("pull_request", "fix/x", "", ["src/invisible_core/x.py"])[0] is True
    assert mod.decide("pull_request", "fix/x", None, ["docs/a.md"])[0] is False
    assert mod.decide("pull_request", "fix/x", "", [])[0] is False
    assert mod.decide("push", "", "", None)[0] is True
    assert mod.core_change("src/x") and mod.core_change("pyproject.toml") and mod.core_change("poetry.lock")
    assert mod.core_change("docs/poetry.lock") is None and mod.core_change("tests/requirements.txt") is None


def test_a_diff_that_cannot_be_read_is_red_not_not_needed(tmp_path):
    repo, _, _ = _repo(tmp_path, [])
    env = _env(tmp_path, "0" * 40, "1" * 40)
    done = subprocess.run([sys.executable, str(SCRIPT)], cwd=repo, env=env, capture_output=True, text=True)
    assert done.returncode == 1 and "cannot be read" in done.stderr
    assert not (tmp_path / "out.txt").exists()
    env = _env(tmp_path, "", "")
    done = subprocess.run([sys.executable, str(SCRIPT)], cwd=repo, env=env, capture_output=True, text=True)
    assert done.returncode == 1 and not (tmp_path / "out.txt").exists()
