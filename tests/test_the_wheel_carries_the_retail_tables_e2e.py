"""The retail tables ship in the wheel, and the INSTALLED package reads them.

The tables a capture decides are JSON files under ``src/invisible_core/data/retail/``
(``captures/pinned.json``, docs/retail-captures.md). Every other test imports the package from the
source tree, where a data file is always there. A wheel ships what the build backend includes, and a
file it leaves out is missing only for the user: ``import invisible_core.constants`` raises at import
time, in a package no test of the tree has imported.

So this builds the wheel of this checkout, checks what is inside it, installs it into a directory of its
own and imports it from there in a process that is not in the checkout. Needs the build backend
(``hatchling``) and ``build``; ci.yml's `wheel-carries-the-tables` job installs them and runs this file
(it is `e2e`, which the default selection leaves out, like the other tests that need something outside
the process).
"""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "invisible_core" / "data" / "retail"
TABLES = sorted(p.name for p in DATA.glob("*.json"))


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("wheel")
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--wheel", "--outdir", str(out), str(ROOT)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    built = list(out.glob("*.whl"))
    assert len(built) == 1, built
    return built[0]


def test_the_built_wheel_contains_every_retail_table(wheel):
    assert len(TABLES) >= 3, "the tree has no generated table: the test would check nothing"
    names = set(zipfile.ZipFile(wheel).namelist())
    for table in TABLES:
        assert f"invisible_core/data/retail/{table}" in names, f"{table} is not in the wheel"
    assert "invisible_core/_retail.py" in names
    # the captures and their scripts are the repository's, never the user's
    assert not [n for n in names if n.startswith(("captures/", "scripts/")) or "/captures/" in n]


def test_the_installed_package_loads_the_retail_tables(wheel, tmp_path):
    target = tmp_path / "site"
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--no-index", "--quiet", "--target", str(target), str(wheel)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    # A process outside the checkout, with the installed copy FIRST on the path: where the package
    # was imported from is part of the answer, so a tree on the path cannot stand in for it.
    code = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import invisible_core\n"
        "from pathlib import Path\n"
        "from invisible_core import _retail, constants, prefs\n"
        "root = Path(invisible_core.__file__).resolve().parent\n"
        "out = {'file': str(root), 'tables': {}}\n"
        "for name in sys.argv[2:]:\n"
        "    out['tables'][name] = _retail.table(name)\n"
        "out['ua'] = constants.USER_AGENT\n"
        "out['webgpu_limits'] = len(prefs._WIN_WEBGPU_LIMITS.split(chr(10)))\n"
        "out['encode'] = prefs._WIN_ENCODE_SUPPORT.split(chr(10))\n"
        "print(json.dumps(out))\n"
    )
    names = [Path(t).stem for t in TABLES]
    run = subprocess.run([sys.executable, "-c", code, str(target), *names], capture_output=True, text=True,
                         cwd=str(tmp_path))
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-3000:]
    got = json.loads(run.stdout.strip().splitlines()[-1])
    assert Path(got["file"]).is_relative_to(target.resolve()), f"imported from {got['file']}, not from the install"
    for table in TABLES:
        assert got["tables"][Path(table).stem] == json.loads((DATA / table).read_text(encoding="utf-8")), table
    template = got["tables"]["navigator"]["user_agent_template"]
    assert got["ua"].startswith(template.split("{version}")[0]) and "{" not in got["ua"]
    assert got["webgpu_limits"] == 35
    assert got["encode"] and all(row.endswith("|sw") for row in got["encode"])
