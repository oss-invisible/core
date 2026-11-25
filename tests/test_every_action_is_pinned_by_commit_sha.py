"""Every `uses:` in a workflow names a commit, never a movable tag or branch.

A tag such as `actions/checkout@v4` is a pointer its owner can move; the code a
runner executes is then whatever the pointer names that day, with the job's
token in reach. The repository setting `sha_pinning_required` makes GitHub
refuse such a run; this test refuses the same thing before it is pushed, with a
message that says which step to fix.

What is held here:

  1. A step or a reusable-workflow call is either a local path (`./...`, the
     code of the same commit) or `owner/repo[/path]@<40 hex>`.
  2. The failure names the file, the job, the step and the offending `uses:`,
     says how to fix it, and names the rule.
  3. The checker itself is proved on invented workflows: a tag, a branch, a
     short SHA, an uppercase SHA and a cross-repository reusable workflow on a
     branch are refused; a full lowercase SHA, a local action and a local
     reusable workflow are accepted.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

RULE = "actions pinned by commit SHA"
PINNED = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(/[^@\s]+)?@[0-9a-f]{40}$")


def _uses(workflow_file: Path):
    """Yield (job, step, uses) for every step and reusable-workflow call."""
    doc = yaml.safe_load(workflow_file.read_text(encoding="utf-8")) or {}
    for job_id, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        if "uses" in job:
            yield job_id, "(reusable workflow call)", job["uses"]
        for index, step in enumerate(job.get("steps") or [], start=1):
            if isinstance(step, dict) and "uses" in step:
                label = f"step {index}"
                if step.get("name"):
                    label += f" ({step['name']})"
                yield job_id, label, step["uses"]


def unpinned(workflow_dir: Path) -> list[str]:
    """One message per `uses:` that is neither local nor pinned by full SHA."""
    failures = []
    for workflow_file in sorted(workflow_dir.glob("*.yml")):
        for job_id, step, uses in _uses(workflow_file):
            uses = str(uses)
            if uses.startswith("./") or PINNED.match(uses):
                continue
            failures.append(
                f"FAIL {workflow_file.name} / {job_id} / {step}: "
                f"`uses: {uses}` is not pinned to a 40-hex commit SHA. "
                f"Fix: resolve the tag through the API "
                f"(`gh api repos/<owner>/<repo>/git/ref/tags/<tag>`, peel an "
                f"annotated tag with `git/tags/<sha>`) and write "
                f"`uses: <owner>/<repo>@<sha> # <tag> (<full version>)`. "
                f"Rule: {RULE}"
            )
    return failures


def test_every_uses_in_the_workflows_is_local_or_pinned_by_commit_sha():
    assert list(WORKFLOWS.glob("*.yml")), "no workflow found: the test reads nothing"
    failures = unpinned(WORKFLOWS)
    assert not failures, "\n" + "\n".join(failures)


SHA = "11d5960a326750d5838078e36cf38b85af677262"


def _write(tmp_path: Path, uses: str, *, as_job: bool = False) -> Path:
    if as_job:
        body = f"jobs:\n  call:\n    uses: {uses}\n"
    else:
        body = (
            "jobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - name: Get the code\n        uses: {uses}\n"
        )
    (tmp_path / "w.yml").write_text(body, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "uses",
    [
        "actions/checkout@v4",
        "actions/checkout@main",
        "actions/checkout@11d5960",
        f"actions/checkout@{SHA.upper()}",
        f"actions/checkout@{SHA}0",
        "actions/checkout",
    ],
)
def test_a_step_that_is_not_pinned_is_refused_with_where_and_how(tmp_path, uses):
    failures = unpinned(_write(tmp_path, uses))
    assert len(failures) == 1
    message = failures[0]
    for part in ("w.yml", "build", "step 1 (Get the code)", f"uses: {uses}", "Fix:", RULE):
        assert part in message


def test_a_cross_repository_reusable_workflow_on_a_branch_is_refused(tmp_path):
    uses = "oss-invisible/core/.github/workflows/noreply-identity.yml@main"
    failures = unpinned(_write(tmp_path, uses, as_job=True))
    assert len(failures) == 1 and "call" in failures[0] and uses in failures[0]


@pytest.mark.parametrize(
    "uses, as_job",
    [
        (f"actions/checkout@{SHA}", False),
        (f"oss-invisible/core/.github/workflows/noreply-identity.yml@{SHA}", True),
        ("./.github/actions/local", False),
        ("./.github/workflows/noreply-identity.yml", True),
    ],
)
def test_a_pinned_or_local_use_is_accepted(tmp_path, uses, as_job):
    assert unpinned(_write(tmp_path, uses, as_job=as_job)) == []
