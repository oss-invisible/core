"""`close()` returns when the BROWSER has exited, and the launch's reaper does the rest.

Measured on the hosted runners (64.73.0): a browser close took 1.3-2.0 s on Linux and 1.7-3.3 s on Windows (stock Playwright:
0.4-1.1 s). Between the last instant the Firefox parent was seen alive and `close()` returning, 0.2-1.0 s on Linux and 0.8-1.2 s on
Windows went on waiting for the tree to leave by itself (`SessionFiles.remove`, `EngineTree.end(grace=SENDER_GRACE)`) and on our own
tail (the job empty, its release, the removal of the directories).

Now:

* `Connection.close` returns when the parent AND the processes that belong to it (content, GPU, socket, rdd, utility, forkserver) are
  gone, told from the launch's job (Windows) or process group (POSIX). The only processes allowed to be left are the ones Firefox
  starts to outlive itself, known by their COMMAND LINE (`process.outlives_the_browser`: an executable named `pingsender`, or the
  `-backgroundtask` argument), never as "whoever is left";
* the sender is not waited for by `close()` and not ended before it has finished. The launch's reaper (`process.Reaper`, a thread
  that is not a daemon) waits for the tree to be empty (`SENDER_GRACE`), ends what is left, releases the launch and removes the
  session's directories. `Browser.wait_reaped()` / `await_reaped()` wait for all of it; the end of the interpreter does too;
* nothing in the reaper waits a fixed time: a tree that is already empty costs a few milliseconds.

The tests stand a Python process in for the engine (what is measured is the teardown, not the browser), in a client program of
its own with the real spawn of the platform (`_spawn_windows`, `_spawn_posix`: the job and the process group) and the real
`instance.launch` and `Browser`. When its pipe closes the engine starts a stand-in for the sender, a child whose command line carries
`-backgroundtask pingsender` and that holds a file in the session's temporary directory for a while, and exits.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

from invisible_core import process as P

pytestmark = pytest.mark.integration

#: Seconds the stand-in for the sender lives after the browser has exited.
SENDER_LIFE = 2.0

_ENGINE = textwrap.dedent("""
    import json, os, subprocess, sys, time
    if os.name == "nt":
        import msvcrt
        r = msvcrt.open_osfhandle(int(os.environ["PW_PIPE_READ"]), os.O_RDONLY)
        w = msvcrt.open_osfhandle(int(os.environ["PW_PIPE_WRITE"]), 0)
    else:
        r, w = 3, 4
    # What the sender is by: its command line. It holds a file of the session until it has finished, and then says so.
    HOLD = ("import os, sys, time\\n"
            "f = open(os.path.join(os.environ['TMP'], 'ping'), 'wb')\\n"
            "time.sleep(float(sys.argv[1]))\\n"
            "f.close()\\n"
            "open(sys.argv[2], 'w').write('done')\\n")
    kids = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]) for _ in range(2)]
    print("PIDS", os.getpid(), *[k.pid for k in kids], flush=True)
    print("Juggler listening to the pipe", flush=True)
    buffer = b""
    while True:
        data = os.read(r, 65536)
        if not data:
            break
        buffer += data
        while b"\\0" in buffer:
            raw, buffer = buffer.split(b"\\0", 1)
            if raw:
                message = json.loads(raw)
                os.write(w, json.dumps({{"id": message["id"], "result": {{}}}}).encode() + b"\\0")
    for k in kids:
        k.kill()
    spawned = []
    if {sender}:
        spawned.append(subprocess.Popen([sys.executable, "-c", HOLD, "{sender}", @@MARKER@@, "-backgroundtask", "pingsender"]))
    if {content}:
        # A process of the browser that is NOT a sender: it leaves late, and the close has to wait for it.
        spawned.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep({content})"]))
    if spawned:
        print("PIDS", *[s.pid for s in spawned], flush=True)
        time.sleep(0.3)          # both are running before the engine is gone
    print("BYE", time.time(), flush=True)
""")

_CLIENT = textwrap.dedent("""
    import asyncio, json, os, sys, time
    import psutil
    from invisible_core.juggler import connection, instance
    from invisible_core.process import SessionToken

    MARKER = os.path.join(os.environ["MARKER_DIR"], "sender")
    # The engine's environment is born filtered, so the sender is told where to write by its command line.
    ENGINE = {engine!r}.replace("@@MARKER@@", repr(MARKER))
    real = {{"nt": connection._spawn_windows, "posix": connection._spawn_posix}}
    spawn = real["nt" if os.name == "nt" else "posix"]

    def stand_in(executable, argv, env):
        return spawn(sys.executable, ["-c", ENGINE], env)

    connection._spawn_windows = connection._spawn_posix = stand_in
    MODE = sys.argv[1]

    # How many times each process's command line is read (the one seam it is read through).
    from invisible_core import process as _process
    READS = {{}}
    _real_command_line = _process._command_line_of

    def _counted(pid):
        READS[pid] = READS.get(pid, 0) + 1
        return _real_command_line(pid)

    _process._command_line_of = _counted

    def pids_of(browser):
        out, bye = [], None
        for line in browser.conn.output.tail(200):
            if line.startswith("PIDS"):
                out += [int(x) for x in line.split()[1:]]
            if line.startswith("BYE"):
                bye = float(line.split()[1])
        return out, bye

    def alive(pids):
        return [p for p in pids if psutil.pid_exists(p) and psutil.Process(p).status() != psutil.STATUS_ZOMBIE]

    def main_sync():
        token = SessionToken.mint()
        marker = MARKER
        env = token.stamp(dict(os.environ))
        browser = instance.launch("engine", prefs={{"a": True}}, env=env, ready_timeout=60)
        dirs = [browser._files.profile, browser._files.tmp]
        time.sleep(0.5)
        return browser, marker, dirs

    def snapshot(browser, marker, dirs):
        pids, bye = pids_of(browser)
        return {{"pids": pids, "bye": bye, "alive": alive(pids), "marker": os.path.exists(marker), "dirs": [d for d in dirs if os.path.exists(d)]}}

    async def amain():
        browser, marker, dirs = main_sync()
        await browser.aclose()
        closed = time.time()
        at_close = snapshot(browser, marker, dirs)
        started = time.monotonic()
        await browser.await_reaped()
        waited = time.monotonic() - started
        after = snapshot(browser, marker, dirs)
        print("REPORT", json.dumps({{"closed": closed, "at_close": at_close, "waited": waited, "after": after,
                                    "marker_text": open(marker).read() if os.path.exists(marker) else None,
                                    "reads": {{str(k): v for k, v in READS.items()}}}}), flush=True)

    def smain():
        browser, marker, dirs = main_sync()
        browser.close()
        closed = time.time()
        at_close = snapshot(browser, marker, dirs)
        if MODE == "exit":
            # Nothing is waited for: the interpreter ends with the reaper still at work.
            print("REPORT", json.dumps({{"closed": closed, "at_close": at_close, "marker": marker, "dirs": dirs}}), flush=True)
            return
        started = time.monotonic()
        browser.wait_reaped()
        waited = time.monotonic() - started
        after = snapshot(browser, marker, dirs)
        print("REPORT", json.dumps({{"closed": closed, "at_close": at_close, "waited": waited, "after": after,
                                    "marker_text": open(marker).read() if os.path.exists(marker) else None,
                                    "reads": {{str(k): v for k, v in READS.items()}}}}), flush=True)

    if MODE == "async":
        asyncio.run(amain())
    else:
        smain()
""")


def _run(tmp_path, how, *, sender=SENDER_LIFE, content=0):
    script = tmp_path / "client.py"
    script.write_text(_CLIENT.format(engine=_ENGINE.format(sender=sender, content=content)), encoding="utf-8")
    markers = tmp_path / "markers"
    markers.mkdir(exist_ok=True)
    done = subprocess.run([sys.executable, str(script), how], capture_output=True, text=True, timeout=180,
                          env=dict(os.environ, MARKER_DIR=str(markers)))
    lines = [x for x in done.stdout.splitlines() if x.startswith("REPORT ")]
    assert done.returncode == 0 and lines, (
        "the client failed (%s):\n%s\n%s" % (done.returncode, done.stdout[-2000:], done.stderr[-4000:]))
    return json.loads(lines[-1][len("REPORT "):])


@pytest.fixture(scope="module", params=["sync", "async"])
def report(request, tmp_path_factory):
    """One run of the client per path (the sync `close` and the async `aclose`), a sender that lives 2 s after the browser."""
    return _run(tmp_path_factory.mktemp("exited_" + request.param), request.param)


@pytest.mark.wall_clock
def test_close_returns_when_the_browser_has_exited_not_when_the_sender_has(report):
    """Red on the close that waits for the whole tree: it returned 2 s after the engine, with the sender done."""
    late = report["closed"] - report["at_close"]["bye"]
    assert report["at_close"]["bye"] is not None, "the stand-in engine never said it was leaving: %r" % report
    assert late < 0.5, "close() returned %.0f ms after the browser exited (the sender lives %.0f ms)" % (late * 1000, SENDER_LIFE * 1000)
    assert report["at_close"]["marker"] is False, "close() waited for the sender: it had finished when it returned"
    sender = report["at_close"]["pids"][-1]
    assert sender in report["at_close"]["alive"], "the sender was gone when close() returned (it was ended, or waited for)"
    # What belongs to the browser is gone: the engine and its two children.
    assert not set(report["at_close"]["pids"][:3]) & set(report["at_close"]["alive"]), (
        "a process of the browser was alive when close() returned: %r" % report["at_close"])


def test_the_sender_is_not_killed_it_finishes_with_its_own_result(report):
    """It holds a file of the session and says so when it has finished: that is only written by a run that was not ended."""
    assert report["marker_text"] == "done", "the sender was ended before it finished: %r" % report
    assert report["after"]["marker"] is True


def test_after_the_reaper_no_process_of_the_tree_remains_and_the_directories_are_gone(report):
    assert report["after"]["alive"] == [], "processes of the closed browser are alive: %s" % report["after"]["alive"]
    assert report["after"]["dirs"] == [], "the closed browser left its directories: %s" % report["after"]["dirs"]
    # The wait is for the sender, which had about a second and a half left: not for a grace, not for a fixed time.
    assert report["waited"] < SENDER_LIFE + 1.0, "the reaper took %.1f s for a sender that lives %.0f s" % (report["waited"], SENDER_LIFE)


def test_waiting_for_the_reaper_of_an_empty_tree_costs_no_fixed_wait(tmp_path):
    """Nothing outlives the browser: the reaper has nothing to wait for, and `SENDER_GRACE` (10 s) is a bound, not a sleep."""
    report = _run(tmp_path, "sync", sender=0)
    assert report["after"]["alive"] == [] and report["after"]["dirs"] == []
    assert report["waited"] < 0.3, "the reaper of an empty tree took %.0f ms" % (report["waited"] * 1000)


@pytest.mark.wall_clock
def test_a_process_of_the_browser_that_is_not_a_sender_is_waited_for(tmp_path):
    """Recognised by what it is, never as whoever is left: a child the engine leaves with no `pingsender` and no `-backgroundtask`
    on its command line is part of the browser, and `close()` returns after it has gone."""
    report = _run(tmp_path, "sync", sender=0, content=1.5)
    held = report["closed"] - report["at_close"]["bye"]
    assert held > 1.0, "close() returned %.0f ms after the browser's last word, with a process of it still running" % (held * 1000)
    assert report["at_close"]["alive"] == [], "a process of the browser was alive when close() returned: %r" % report["at_close"]


@pytest.mark.wall_clock
@pytest.mark.skipif(sys.platform != "win32", reason="a Windows process has its final command line from its creation")
def test_on_windows_a_command_line_is_read_once_per_process_during_a_close(tmp_path):
    """The loop that waits for the browser to be gone looks every 10 ms: a process that is not a sender stays in it for as long as it
    lives (here 1.5 s), and its command line (a read of the process's PEB, under the GIL) was read on every look."""
    report = _run(tmp_path, "sync", sender=0, content=1.5)
    assert report["reads"], "no command line was read: the seam is not the one the tree reads through"
    assert max(report["reads"].values()) <= 1, "a command line was read more than once: %r" % report["reads"]


@pytest.mark.wall_clock
def test_the_end_of_the_interpreter_waits_for_the_reaper_and_leaves_nothing(tmp_path):
    """A program that closes its browser and ends, without waiting for anything: the reaper is not a daemon, so the interpreter
    does not end under it. Afterwards no process of the tree is alive and the session's directories are gone, and the sender
    had finished (it was the end of the interpreter that waited, not a kill)."""
    import psutil

    report = _run(tmp_path, "exit")
    assert report["closed"] - report["at_close"]["bye"] < 0.5 and report["at_close"]["marker"] is False, (
        "the close waited for the sender, so this proves nothing about the end of the interpreter: %r" % report)
    left = [p for p in report["at_close"]["pids"] if psutil.pid_exists(p) and psutil.Process(p).status() != psutil.STATUS_ZOMBIE]
    assert left == [], "processes of the tree outlived the interpreter: %s" % left
    assert [d for d in report["dirs"] if os.path.exists(d)] == [], "the session's directories outlived the interpreter"
    assert open(report["marker"]).read() == "done", "the sender was ended instead of being let finish"


# ── the pieces ──────────────────────────────────────────────────────────────

#: The sender's command line on Windows, as seen 67 times in one GitHub run (the directories and the uuid tail made up): the executable is the
#: browser's own, and the task is `--backgroundtask pingsender`.
WINDOWS_SENDER = [
    r"D:\cache\invisible-playwright\Cache\firefox-64_159.0_20261114003728\firefox.exe",
    "--backgroundtask", "pingsender",
    "https://incoming.telemetry.mozilla.org/submit/telemetry/de30146e-596a-4c1b-9d27-5e8f0a1b2c3d/event/Firefox/159.0/"
    "release/20261114003728?v=4",
]
WINDOWS_PARENT = [WINDOWS_SENDER[0], "-no-remote", "-profile", r"D:\temp\invisible_profile_1_ab",
                  "-juggler-pipe"]
WINDOWS_CONTENT = [WINDOWS_SENDER[0], "-contentproc", "--channel=1234.0.1234", "-childID", "1", "-isForBrowser", "-prefsHandle", "800",
                   "-prefMapHandle", "804", "-jsInitHandle", "808", "-parentBuildID", "20261114003728", "tab"]


@pytest.mark.unit
@pytest.mark.parametrize("command", [
    WINDOWS_SENDER,
    # Linux: no real line has been captured yet (a hunt will); both forms Firefox can be started in are accepted until then:
    # the `pingsender` binary as argv[0], and the browser itself with the background task.
    ["pingsender", "https://example.invalid/", "ping.json"],
    ["C:\\Program Files\\Mozilla Firefox\\pingsender.exe", "https://example.invalid/", "ping.json"],
    ["/opt/firefox/pingsender", "x"],
    ["/opt/firefox/PingSender.EXE"],
    ["firefox", "-backgroundtask", "pingsender"],
    ["/opt/firefox/firefox", "--backgroundtask", "pingsender", "https://incoming.telemetry.mozilla.org/submit/x"],
    ["C:\\fx\\firefox.exe", "--backgroundtask", "pingsender"],
    ["firefox.exe", "/BackgroundTask", "x"],
])
def test_a_sender_and_a_background_task_are_known_by_their_command_line(command):
    assert P.outlives_the_browser(command) is True


@pytest.mark.unit
@pytest.mark.parametrize("command", [
    WINDOWS_PARENT,
    WINDOWS_CONTENT,
    [],
    None,
    ["firefox", "-contentproc", "-childID", "1", "tab"],
    ["firefox", "-no-remote", "-profile", "C:\\invisible_profile_1"],
    ["/opt/firefox/firefox", "-profile", "/tmp/pingsender"],       # the name of a directory is not the name of a program
    ["firefox", "-backgroundtasks"],
    ["firefox", "backgroundtask"],
    ["pingsender-not-really", "x"],
])
def test_a_process_of_the_browser_is_not_taken_for_a_sender(command):
    assert P.outlives_the_browser(command) is False


@pytest.mark.unit
@pytest.mark.parametrize("final, reads", [(True, 1), (False, 25)])
def test_a_command_line_is_read_once_where_it_is_final_and_asked_again_where_it_is_not(monkeypatch, final, reads):
    """Windows (a process has its final command line from birth): one read per pid, yes or no. POSIX (fork, then exec): a no is
    asked again, because a child not yet executed still reads as its parent, and a yes is kept."""
    seen = []
    monkeypatch.setattr(P, "_command_line_is_final", lambda: final)
    monkeypatch.setattr(P, "_command_line_of", lambda pid: (seen.append(pid), WINDOWS_CONTENT)[1])
    tree = P.EngineTree(os.getpid())
    answers = [tree._survives(1234) for _ in range(25)]
    assert answers == [False] * 25
    assert len(seen) == reads, "%d reads for a process looked at 25 times" % len(seen)
    seen.clear()
    monkeypatch.setattr(P, "_command_line_of", lambda pid: (seen.append(pid), WINDOWS_SENDER)[1])
    assert [tree._survives(99) for _ in range(25)] == [True] * 25
    assert len(seen) == 1, "a sender is known once it is read"


@pytest.mark.unit
def test_a_command_line_that_cannot_be_read_is_asked_again_on_every_platform(monkeypatch):
    seen = []

    def denied(pid):
        seen.append(pid)
        raise PermissionError("not ours")

    monkeypatch.setattr(P, "_command_line_of", denied)
    tree = P.EngineTree(os.getpid())
    assert [tree._survives(7) for _ in range(3)] == [False] * 3
    assert len(seen) == 3


@pytest.mark.unit
@pytest.mark.skipif(sys.platform == "win32", reason="argv[0] by `executable=` is POSIX")
def test_a_group_is_gone_when_only_a_sender_is_left_in_it():
    """The group's own answer, with no engine and no connection: the leader has exited, a child that is not a sender is in the group
    (not gone), and then one that is (gone, and not ended)."""
    leader = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess, sys\n"
         "subprocess.Popen(['pingsender', '-c', 'import time; time.sleep(30)'], executable=sys.executable)\n"
         "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1)'])\n"
         "print('up', flush=True)\n"],
        process_group=0, stdout=subprocess.PIPE, text=True)
    try:
        assert leader.stdout.readline().strip() == "up"
        leader.wait(timeout=10)
        tree = P.EngineTree(leader.pid, group=True, poll=leader.poll)
        assert tree.wait_browser_gone(time.monotonic() + 0.2) is False, "a process that is not a sender was let be"
        started = time.monotonic()
        assert tree.wait_browser_gone(time.monotonic() + 10) is True
        assert time.monotonic() - started < 3
        assert tree._group_alive(), "the sender was ended"
    finally:
        try:
            os.killpg(leader.pid, 9)
        except OSError:
            pass
