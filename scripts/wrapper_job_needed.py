#!/usr/bin/env python3
"""Does this run have to try the wrapper on this core? (`wrapper-job-needed` of .github/workflows/ci.yml)

The wrapper pins this package and is published after it, so a change of the core that breaks the wrapper is
seen by nobody unless the core's own round runs the wrapper on the new core. The jobs that do
(`wrapper-on-this-core`, `wrapper-unit-on-this-core`) are REQUIRED, and a skip of them is red in `gate`, when:

  * the event is a push (to main: the workflow only runs on main);
  * the head branch of the pull request is `release/*`;
  * the pull request body declares a wrapper branch (a line `wrapper-ref: <branch>`; the grammar is judged by the
    job that chooses the branch, which is red on a bad line);
  * the change touches what the installed core is made of: anything under `src/` (the package: the wheel is
    `packages = ["src/invisible_core"]`), `pyproject.toml` (its dependencies, its version, its build), or a lock file
    of the repository root (`*.lock`, `pylock*.toml`, `requirements*.txt`, `constraints*.txt`: none exists today, and
    the day one does, it counts);
  * the change touches the jobs themselves (JOB_FILES: this file, ci.yml, the scripts and tests of the jobs).

Any other pull request (docs, tests of the core, scripts that are not the jobs') is "not needed", with its reason in
the step summary, and the single verdict (`gate`) knows a skip is the right state there.

    wrapper_job_needed.py        reads EVENT, HEAD_REF, BASE_SHA, HEAD_SHA and PR_BODY from the environment (the body
                                 is untrusted: it never goes through an interpolation), asks git for the files of
                                 `BASE_SHA...HEAD_SHA`, writes `run=true|false` to $GITHUB_OUTPUT and the reason to
                                 $GITHUB_STEP_SUMMARY. A diff that cannot be read is exit 1, never "nothing changed".
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys

#: The files of the jobs themselves: a change of any of them runs the jobs.
JOB_FILES = (
    ".github/workflows/ci.yml",
    "scripts/wrapper_on_this_core.py",
    "scripts/wrapper_test_seconds.json",
    "scripts/wrapper_job_needed.py",
    "tests/test_the_wrapper_job_runs_on_the_core.py",
    "tests/test_wrapper_job_needed.py",
)

#: What the installed core is made of, besides `src/`.
CORE_FILES = ("pyproject.toml",)
#: Lock and pin files of the repository root (a file with no `/` in its path).
LOCK_PATTERNS = ("*.lock", "pylock*.toml", "requirements*.txt", "constraints*.txt")

_DECLARATION = re.compile(r"^wrapper-ref[ \t]*:", re.IGNORECASE | re.MULTILINE)


def core_change(path: str) -> str | None:
    """Why this path is part of what the installed core is made of, or None."""
    if path.startswith("src/"):
        return "the package"
    if path in CORE_FILES:
        return "its build and dependencies"
    if "/" not in path and any(fnmatch.fnmatchcase(path, pattern) for pattern in LOCK_PATTERNS):
        return "a lock file"
    return None


def decide(event: str, head_ref: str, body: str | None, changed: list[str] | None) -> tuple[bool, str]:
    """(needed, reason). `changed` is the list of files of a pull request; a push does not look at it."""
    if event == "push":
        return True, "a push to main"
    reasons: list[str] = []
    if (head_ref or "").startswith("release/"):
        reasons.append(f"the head branch `{head_ref}` is a release branch")
    if _DECLARATION.search((body or "").replace("\r\n", "\n").replace("\r", "\n")):
        reasons.append("the pull request declares a wrapper branch (`wrapper-ref:`)")
    for path in changed or []:
        if path in JOB_FILES:
            reasons.append(f"`{path}` is a file of the job itself")
        else:
            what = core_change(path)
            if what:
                reasons.append(f"`{path}` is part of what the installed core is made of ({what})")
    if reasons:
        shown = reasons[:8]
        more = len(reasons) - len(shown)
        return True, "; ".join(shown) + (f"; and {more} more" if more > 0 else "")
    return False, "nothing in the change is part of the installed core, a declared wrapper branch or a file of the job"


def _changed_files(base: str, head: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...{head}"], check=True,
                         capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    event = os.environ.get("EVENT", "")
    changed = None
    if event != "push":
        base, head = os.environ.get("BASE_SHA", ""), os.environ.get("HEAD_SHA", "")
        try:
            if not base or not head:
                raise ValueError("BASE_SHA and HEAD_SHA are not both set")
            changed = _changed_files(base, head)
        except (subprocess.CalledProcessError, ValueError, OSError) as exc:
            print(f"wrapper-job-needed: the diff cannot be read: {exc}", file=sys.stderr)
            return 1
    needed, why = decide(event, os.environ.get("HEAD_REF", ""), os.environ.get("PR_BODY"), changed)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"run={'true' if needed else 'false'}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### the wrapper on this core\n\n**{'REQUIRED' if needed else 'not needed'}**: {why}.\n")
    print(f"wrapper-on-this-core is needed: {str(needed).lower()} ({why})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
