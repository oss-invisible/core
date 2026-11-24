"""The launch of the retail browser sets nothing: no preference, no policy, no automation, nothing left running.

scripts/retail/run_retail_capture.py starts the official Firefox as

    firefox.exe -no-remote -profile <empty directory> http://127.0.0.1:<port>/?dpr1

A preference or a policy of ours would make the capture a capture of something else (a policy turns Web Serial
off). No browser starts here: a stand-in plays the browser, and the test looks at what exists on disk, in the
profile and in the install folder, at the moment it is spawned, and at the command and environment it is given.

Held here:
  * on the real script the observation is clean, and the run ends with a result, every process of the install
    folder ended and counted, and the server's records written;
  * the SAME observation is red on a version of the script that writes a user.js, a prefs.js, a policies.json, adds
    an automation argument, or sets a MOZ_ variable, even with the script's own guard taken out;
  * the guard refuses each of those too, before anything is spawned;
  * a browser that restarts a child, one that never ends, one that exits without a result and one that never
    answers are each reported as what they are.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path

import pytest

from _retail_scripts import RETAIL, ROOT, load

pytestmark = pytest.mark.integration

launch = load("run_retail_capture")
SCRIPT = RETAIL / "run_retail_capture.py"
RESULT = json.dumps({"format": "host-gate/1", "meta": {"devicePixelRatio": 1}, "items": {}, "noise": {}}).encode()
FIREFOX = "firefox.exe" if os.name == "nt" else "firefox"


class FakeTable(launch.ProcessTable):
    """The processes of the install folder. `respawn`: a killed process is replaced once; `stubborn`: never dies."""

    def __init__(self, alive=(101, 102), respawn=False, stubborn=False):
        self.alive, self.respawn, self.stubborn, self.killed, self.respawned = set(alive), respawn, stubborn, [], False

    def under(self, directory):
        return sorted(self.alive)

    def kill_tree(self, pid):
        self.killed.append(pid)
        if self.stubborn:
            return
        self.alive.discard(pid)
        if self.respawn and not self.respawned:
            self.respawned = True
            self.alive.add(103)


class Browser:
    """Plays Firefox: records what exists when it is spawned, then loads the page and posts a result."""

    def __init__(self, install: Path, answer: bool = True, exits: bool = False):
        self.install, self.answer, self.exits, self.seen, self.pid = install, answer, exits, None, 101

    def poll(self):
        return 0 if self.exits else None

    def __call__(self, command, env, **_kw):
        profile = Path(command[command.index("-profile") + 1])
        self.seen = {
            "command": list(command),
            "env": dict(env),
            "profile": sorted(p.name for p in profile.iterdir()),
            "install": sorted(p.relative_to(self.install).as_posix() for p in self.install.rglob("*") if p.is_file()),
        }
        if self.answer:
            port = int(command[-1].split(":")[2].split("/")[0])
            threading.Thread(target=self._visit, args=(port, command[-1].split("?", 1)[1]), daemon=True).start()
        return self

    @staticmethod
    def _visit(port, label):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/?" + label, headers={"User-Agent": "stand-in"})
        c.getresponse().read()
        c.request("POST", "/result?" + label, body=RESULT)
        c.getresponse().read()
        c.close()


def problems_of(seen: dict, install: Path) -> list:
    """What a plain launch must not show. The test's own observation, independent of the script's guard."""
    out = []
    for name in ("user.js", "prefs.js", "policies.json"):
        if name in seen["profile"]:
            out.append(f"the profile has {name}")
    if seen["profile"]:
        out.append(f"the profile is not empty: {seen['profile']}")
    if any(f.endswith("policies.json") or f.endswith("distribution.ini") for f in seen["install"]):
        out.append("the install folder has a policy or distribution file")
    command = seen["command"]
    if command[1:3] != ["-no-remote", "-profile"] or len(command) != 5 or not command[4].startswith("http://127.0.0.1:"):
        out.append(f"the command is not the plain one: {command[1:]}")
    banned = ("marionette", "remote-debugging", "remote-allow", "juggler", "headless", "wait-for-browser", "preferences")
    out += [f"automation argument {a}" for a in command if any(b in a.lower() for b in banned)]
    out += [f"environment {k}" for k in seen["env"] if k.upper().startswith("MOZ_")]
    return out


@pytest.fixture
def install(tmp_path):
    core = tmp_path / "core"
    core.mkdir()
    (core / FIREFOX).write_bytes(b"MZ")
    (core / "application.ini").write_text("[App]\nName=Firefox\n", encoding="utf-8")
    return core


def _run(module, install, tmp_path, browser, table=None, timeout=5.0, labels=("1",), env=None, shot=None):
    import time
    return module.run(install, tmp_path / "out", list(labels), timeout, spawn=browser, table=table or FakeTable(),
                      sleep=lambda s: time.sleep(0.005), env={"PATH": "x"} if env is None else env,
                      shot=shot or (lambda p: None))


def _mutated(tmp_path, *, inject: str = "", drop_guard: bool = True):
    """A copy of the script that writes something into the launch (and, by default, has no guard)."""
    text = SCRIPT.read_text(encoding="utf-8")
    anchor = "                profile.mkdir()\n"
    assert anchor in text
    text = text.replace(anchor, anchor + inject, 1)
    if drop_guard:
        guard = "                assert_plain_launch(command, profile, install_dir, env, port, label)\n"
        assert guard in text
        text = text.replace(guard, "                pass\n", 1)
    return _module_from(text, tmp_path / "mutated_run_retail_capture.py")


def _module_from(text: str, path: Path):
    """The script's text as a module of its own, still reading the repository's page and sibling modules."""
    text = (text.replace("sys.path.insert(0, str(Path(__file__).resolve().parent))", f"sys.path.insert(0, {str(RETAIL)!r})")
            .replace("REPO = Path(__file__).resolve().parents[2]", f"REPO = Path({str(ROOT)!r})"))
    path.write_text(text, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- the real script is clean


def test_the_launch_is_the_plain_command_and_the_profile_is_empty(install, tmp_path):
    browser = Browser(install)
    summary = _run(launch, install, tmp_path, browser)
    assert problems_of(browser.seen, install) == []
    cmd = browser.seen["command"]
    assert cmd[0] == str(install / FIREFOX) and cmd[1:3] == ["-no-remote", "-profile"] and len(cmd) == 5
    assert cmd[4].startswith("http://127.0.0.1:") and cmd[4].endswith("/?dpr1")
    assert summary["runs"][0]["ended"] == "result" and launch.succeeded(summary)


def test_the_run_writes_the_result_and_what_the_server_recorded(install, tmp_path):
    _run(launch, install, tmp_path, Browser(install))
    out = tmp_path / "out"
    assert json.loads((out / "dpr1.json").read_bytes())["format"] == "host-gate/1"
    wire = json.loads((out / "wire.json").read_text(encoding="utf-8"))
    assert wire["format"] == "retail-wire/1"
    assert [r["method"] for r in wire["http_requests"]] == ["GET", "POST"]
    assert ["User-Agent", "stand-in"] in wire["http_requests"][0]["headers"]


def test_every_process_of_the_install_folder_is_ended_and_counted(install, tmp_path):
    table = FakeTable()
    summary = _run(launch, install, tmp_path, Browser(install), table=table)
    assert table.alive == set() and summary["remaining_processes"] == 0 and 101 in table.killed and 102 in table.killed


def test_a_child_that_comes_back_is_killed_again(install, tmp_path):
    table = FakeTable(respawn=True)
    summary = _run(launch, install, tmp_path, Browser(install), table=table)
    assert table.respawned and 103 in table.killed and table.alive == set()
    assert summary["remaining_processes"] == 0 and launch.succeeded(summary)


def test_a_process_that_will_not_end_fails_the_run(install, tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "KILL_ROUNDS", 3)
    table = FakeTable(stubborn=True)
    summary = _run(launch, install, tmp_path, Browser(install), table=table)
    assert summary["remaining_processes"] == 2 and not launch.succeeded(summary)


def test_a_browser_that_never_answers_times_out_and_is_photographed(install, tmp_path):
    shots = []
    summary = _run(launch, install, tmp_path, Browser(install, answer=False), timeout=0.3, shot=shots.append)
    assert summary["runs"][0]["ended"] == "timeout" and not launch.succeeded(summary)
    assert [p.name for p in shots] == ["timeout-dpr1.png"] and summary["remaining_processes"] == 0


def test_a_browser_that_exits_without_a_result_is_reported(install, tmp_path):
    table = FakeTable(alive=())
    summary = _run(launch, install, tmp_path, Browser(install, answer=False, exits=True), table=table, timeout=30)
    assert summary["runs"][0]["ended"] == "exited" and not launch.succeeded(summary)


# --------------------------------------------------------------------------- the observation can be red


@pytest.mark.parametrize("inject, expected", [
    ('                (profile / "user.js").write_text(\'user_pref("marionette.enabled", true);\')\n', "user.js"),
    ('                (profile / "prefs.js").write_text("// none")\n', "prefs.js"),
    ('                (profile / "policies.json").write_text("{}")\n', "policies.json"),
    ('                (install_dir / "distribution").mkdir(exist_ok=True)\n                (install_dir / "distribution" / "policies.json").write_text("{}")\n',
     "policy or distribution file"),
])
def test_the_observation_is_red_on_a_script_that_writes_a_pref_or_a_policy(install, tmp_path, inject, expected):
    """The script's own guard is taken out: what is on disk at the spawn is what fails the test."""
    browser = Browser(install)
    _run(_mutated(tmp_path, inject=inject), install, tmp_path, browser)
    assert any(expected in p for p in problems_of(browser.seen, install)), problems_of(browser.seen, install)


def test_the_observation_is_red_on_an_automation_argument(install, tmp_path):
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"-no-remote", "-profile", str(profile)' in text
    module = _module_from(text.replace('"-no-remote", "-profile", str(profile)', '"-no-remote", "-marionette", "-profile", str(profile)')
                          .replace("assert_plain_launch(command, profile, install_dir, env, port, label)", "pass"),
                          tmp_path / "m2_run_retail_capture.py")
    browser = Browser(install)
    _run(module, install, tmp_path, browser)
    assert any("marionette" in p for p in problems_of(browser.seen, install)), problems_of(browser.seen, install)


def test_the_observation_is_red_on_a_moz_variable(install, tmp_path):
    browser = Browser(install)
    _run(_mutated(tmp_path), install, tmp_path, browser, env={"PATH": "x", "MOZ_MARIONETTE": "1"})
    assert any("MOZ_MARIONETTE" in p for p in problems_of(browser.seen, install))


# --------------------------------------------------------------------------- the guard refuses before the spawn


def _guard(install, tmp_path, **changes):
    profile = tmp_path / "profile"
    profile.mkdir(exist_ok=True)
    command = changes.pop("command", None) or launch.launch_command(install / FIREFOX, profile, 4000, "1")
    env = changes.pop("env", {"PATH": "x"})
    launch.assert_plain_launch(command, profile, install, env, 4000, "1")


def test_the_guard_passes_the_plain_launch(install, tmp_path):
    _guard(install, tmp_path)


@pytest.mark.parametrize("name", ["user.js", "prefs.js", "policies.json"])
def test_the_guard_refuses_a_profile_with_a_pref_or_a_policy(install, tmp_path, name):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / name).write_text("x")
    with pytest.raises(launch.LaunchError, match=name):
        _guard(install, tmp_path)


def test_the_guard_refuses_a_policy_in_the_install_folder(install, tmp_path):
    (install / "distribution").mkdir()
    (install / "distribution" / "policies.json").write_text("{}")
    with pytest.raises(launch.LaunchError, match="policies.json"):
        _guard(install, tmp_path)


@pytest.mark.parametrize("extra", ["-marionette", "--remote-debugging-port", "-headless", "-juggler-pipe", "-P"])
def test_the_guard_refuses_any_argument_beyond_the_plain_command(install, tmp_path, extra):
    plain = launch.launch_command(install / FIREFOX, tmp_path / "profile", 4000, "1")
    with pytest.raises(launch.LaunchError, match="not a plain launch"):
        _guard(install, tmp_path, command=plain[:1] + [extra] + plain[1:])


def test_the_guard_refuses_a_moz_variable(install, tmp_path):
    with pytest.raises(launch.LaunchError, match="(?i)moz_"):
        _guard(install, tmp_path, env={"PATH": "x", "moz_crash_reporter": "1"})


def test_a_run_that_is_refused_spawns_nothing(install, tmp_path):
    (install / "distribution").mkdir()
    (install / "distribution" / "policies.json").write_text("{}")
    browser = Browser(install)
    with pytest.raises(launch.LaunchError):
        _run(launch, install, tmp_path, browser)
    assert browser.seen is None


def test_the_script_asks_for_no_display_scale_but_one():
    assert launch.capture_request.RUNNER_DPRS == (1,)
    assert (ROOT / "captures" / "REQUEST.json").is_file()
    assert sys.modules["run_retail_capture"] is launch
