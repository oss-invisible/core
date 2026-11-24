"""One capture run of the official Firefox: launch it plain, wait for its result, end every process.

    python scripts/retail/run_retail_capture.py --install DIR --out DIR [--dpr 1] [--timeout 300]

`--install` is the extracted installer's `core` folder (scripts/retail/firefox_build.py makes it).
The launch is exactly

    firefox.exe -no-remote -profile <empty directory> http://127.0.0.1:<port>/?dpr1

and nothing else is set: no user.js, no prefs.js and no policy that this script writes, no
distribution/policies.json (a policy turns Web Serial off), no Marionette, no remote protocol, no
WebDriver, no MOZ_* variable. `assert_plain_launch` checks all of that before the process is
spawned and refuses otherwise. The run ends when the page POSTs its result (or at the timeout);
then every process of the install folder is killed, tree by tree, and counted until none remains.

Writes OUT/dpr<label>.json (the raw result of the page, host-gate/1) and OUT/wire.json (what
scripts/retail/capture_server.py recorded). Exit 0 only with a result for every scale and no
process left.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_server  # noqa: E402
import capture_request  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "captures" / "pages" / "host-gate-2.html"
#: Files that would be a preference or a policy, in a profile or in the install folder.
FORBIDDEN_IN_PROFILE = ("user.js", "prefs.js", "policies.json", "handlers.json", "containers.json")
FORBIDDEN_IN_INSTALL = (Path("distribution") / "policies.json", Path("distribution") / "distribution.ini")
POLL_SECONDS = 0.5
KILL_ROUNDS = 20
EXIT_CHECK_POLLS = 10


class LaunchError(Exception):
    """The launch is not the plain one, or the run did not end clean."""


def launch_command(firefox_exe: Path, profile: Path, port: int, label: str) -> List[str]:
    """The whole command line. It is this and nothing more."""
    return [str(firefox_exe), "-no-remote", "-profile", str(profile), f"http://127.0.0.1:{port}/?dpr{label}"]


def assert_plain_launch(command: Sequence[str], profile: Path, install_dir: Path, env: Mapping[str, str],
                        port: int, label: str) -> None:
    """Refuse a launch that sets anything. Run before the spawn, on exactly what will be spawned."""
    problems: List[str] = []
    expected = launch_command(Path(command[0]), profile, port, label)
    if list(command) != expected:
        problems.append(f"the command is {list(command)}, not {expected}")
    if profile.exists() and any(profile.iterdir()):
        problems.append(f"the profile is not empty: {sorted(p.name for p in profile.iterdir())}")
    for name in FORBIDDEN_IN_PROFILE:
        if (profile / name).exists():
            problems.append(f"the profile has {name}")
    for relative in FORBIDDEN_IN_INSTALL:
        if (install_dir / relative).exists():
            problems.append(f"the install folder has {relative.as_posix()}")
    moz = sorted(k for k in env if k.upper().startswith("MOZ_"))
    if moz:
        problems.append(f"the environment sets {moz}")
    if problems:
        raise LaunchError("not a plain launch: " + "; ".join(problems))


# --------------------------------------------------------------------------- processes


class ProcessTable:
    """Which processes run from a folder, and how to end one with its children."""

    def under(self, directory: Path) -> List[int]:
        if os.name == "nt":
            script = ("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and "
                      "$_.ExecutablePath.StartsWith($env:CAPTURE_INSTALL_DIR, [System.StringComparison]::OrdinalIgnoreCase) } "
                      "| ForEach-Object { $_.ProcessId }")
            run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 env=dict(os.environ, CAPTURE_INSTALL_DIR=str(directory) + os.sep),
                                 capture_output=True, text=True, timeout=120)
            return [int(line) for line in run.stdout.split() if line.isdigit()]
        found = []
        for entry in Path("/proc").glob("[0-9]*"):
            try:
                target = Path(os.readlink(entry / "exe"))
            except OSError:
                continue
            if directory in target.parents:
                found.append(int(entry.name))
        return found

    def kill_tree(self, pid: int) -> None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=60)
        else:
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def end_all(table: ProcessTable, install_dir: Path, root_pid: Optional[int], rounds: int = KILL_ROUNDS,
            pause: float = 0.5, sleep: Callable[[float], None] = time.sleep) -> List[int]:
    """Kill the launched process's tree and every process of the install folder, again until none is
    left (a browser restarts a child that was just killed) or `rounds` have passed. Returns the pids
    still alive: an empty list is the count the run must end with."""
    if root_pid is not None:
        table.kill_tree(root_pid)
    left: List[int] = []
    for _ in range(rounds):
        left = table.under(install_dir)
        if not left:
            return []
        for pid in left:
            table.kill_tree(pid)
        sleep(pause)
    return table.under(install_dir)


def screenshot(path: Path) -> None:
    """The screen as it is, for the person reading a run that timed out (a dialog nobody answered).
    Best effort: it never fails the run."""
    if os.name != "nt":
        return
    script = ("Add-Type -AssemblyName System.Windows.Forms, System.Drawing; "
              "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
              "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
              "$g = [System.Drawing.Graphics]::FromImage($bmp); "
              "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); "
              "$bmp.Save($env:CAPTURE_SCREENSHOT)")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       env=dict(os.environ, CAPTURE_SCREENSHOT=str(path)), capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass


# --------------------------------------------------------------------------- the run


def run(install_dir: Path, out_dir: Path, labels: Sequence[str], timeout: float,
        spawn: Callable[..., Any] = subprocess.Popen, table: Optional[ProcessTable] = None,
        clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
        page: Path = PAGE, env: Optional[Mapping[str, str]] = None,
        shot: Callable[[Path], None] = screenshot) -> Dict[str, Any]:
    """Take one capture per label. Returns the summary written next to the results."""
    table = table or ProcessTable()
    env = dict(os.environ if env is None else env)
    firefox_exe = install_dir / ("firefox.exe" if os.name == "nt" else "firefox")
    out_dir.mkdir(parents=True, exist_ok=True)
    http, tls, recorder = capture_server.start(page, out_dir)
    port = http.server_address[1]
    summary: Dict[str, Any] = {"runs": [], "remaining_processes": 0}
    try:
        for label in labels:
            recorder.result_event.clear()
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                profile = Path(tmp) / "profile"
                profile.mkdir()
                command = launch_command(firefox_exe, profile, port, label)
                assert_plain_launch(command, profile, install_dir, env, port, label)
                started = clock()
                process = spawn(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
                ended = "result"
                polls = 0
                while not recorder.result_event.is_set():
                    if clock() - started > timeout:
                        ended = "timeout"
                        break
                    polls += 1
                    if polls % EXIT_CHECK_POLLS == 0 and process.poll() is not None and not table.under(install_dir):
                        ended = "exited"  # the launcher is gone and so is every process of the install folder
                        break
                    sleep(POLL_SECONDS)
                if ended == "timeout":
                    shot(out_dir / f"timeout-dpr{label}.png")
                left = end_all(table, install_dir, getattr(process, "pid", None), sleep=sleep)
            summary["runs"].append({"dpr": label, "ended": ended, "seconds": round(clock() - started, 1),
                                    "processes_left": len(left)})
            summary["remaining_processes"] += len(left)
        wire = recorder.dump()
    finally:
        capture_server.stop(http, tls)
    (out_dir / "wire.json").write_text(json.dumps(wire, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary["http_requests"] = len(wire["http_requests"])
    summary["tls_connections"] = len(wire["tls_connections"])
    summary["certificate_requests"] = len(wire["certificate_requests"])
    return summary


def succeeded(summary: Dict[str, Any]) -> bool:
    """A result for every scale, and no process of the install folder left."""
    return summary["remaining_processes"] == 0 and bool(summary["runs"]) and all(r["ended"] == "result" for r in summary["runs"])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--dpr", nargs="+", type=float, default=[1.0])
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args(argv)
    try:
        labels = [capture_request.label(d) for d in capture_request.validate_dpr(args.dpr)]
        summary = run(args.install.resolve(), args.out, labels, args.timeout)
    except (LaunchError, capture_request.RequestError) as exc:
        print(f"run_retail_capture: {exc}", file=sys.stderr)
        return 2
    (args.out / "run.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if succeeded(summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
