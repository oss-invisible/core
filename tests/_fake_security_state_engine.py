"""A stand-in for the engine's security-state GENERATE MODE, a standalone script: `python -I _fake_security_state_engine.py -headless -no-remote -profile <dir>`.

It respects the contract of `invisible_core._security_state_generate` exactly, and refuses (exit 2, message on stderr) whatever the core must not do: other arguments than the three
of the contract, a work profile that holds anything but `user.js`, a `user.js` that is not exactly the four prefs (generate = true, three absolute paths of files that exist).

Then it reads the three inputs and leaves `security_state/` and `securitystate-result.json` in the profile. Its identity is `FAKE_ENGINE_VERSION` / `FAKE_ENGINE_BUILD_ID`; its
behaviour is `FAKE_ENGINE_MODE`: ok (default), exit1, noresult, badjson, wrongbuild, wronghashes, wrongtimestamp, nofilter, hang.
`hang` starts a child that also sleeps and then sleeps itself; the pids go to the profile (`hang.pids`) and, when `FAKE_ENGINE_PIDFILE` is set, to that file (the profile is
deleted by the core, so a test reads them there).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

PREFS = ("zoom.stealth.securitystate.generate", "zoom.stealth.intermediates.file", "zoom.stealth.onecrl.file", "zoom.stealth.crlite.file")
LINE = re.compile(r'^user_pref\(("(?:[^"\\]|\\.)*"), (true|false|"(?:[^"\\]|\\.)*"|-?\d+)\);$')


def refuse(message):
    sys.stderr.write("fake engine: " + message + "\n")
    sys.stderr.flush()
    sys.exit(2)


def read_prefs(profile):
    raw = open(os.path.join(profile, "user.js"), "rb").read()
    if b"\r" in raw or not raw.endswith(b"\n"):
        refuse("user.js is not LF lines ending in a newline")
    prefs = {}
    order = []
    for line in raw.decode("utf-8").split("\n")[:-1]:
        m = LINE.match(line)
        if not m:
            refuse("user.js has a line that is not a user_pref: %r" % line)
        name = json.loads(m.group(1))
        prefs[name] = json.loads(m.group(2))
        order.append(name)
    if order != list(PREFS):
        refuse("user.js holds %r, the contract is exactly %r" % (order, list(PREFS)))
    return prefs


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    args = sys.argv[1:]
    if len(args) != 4 or args[:3] != ["-headless", "-no-remote", "-profile"]:
        refuse("arguments are %r, the contract is -headless -no-remote -profile <dir>" % (args,))
    profile = args[3]
    if not os.path.isabs(profile) or not os.path.isdir(profile):
        refuse("the profile %r is not an existing absolute directory" % profile)
    if sorted(os.listdir(profile)) != ["user.js"]:
        refuse("the work profile holds %r, it must hold only user.js" % sorted(os.listdir(profile)))
    prefs = read_prefs(profile)
    if prefs[PREFS[0]] is not True:
        refuse("%s is %r, not true" % (PREFS[0], prefs[PREFS[0]]))
    for name in PREFS[1:]:
        v = prefs[name]
        if not isinstance(v, str) or not os.path.isabs(v) or not os.path.isfile(v):
            refuse("%s is %r: not the absolute path of an existing file" % (name, v))

    mode = os.environ.get("FAKE_ENGINE_MODE", "ok")
    build_id = os.environ.get("FAKE_ENGINE_BUILD_ID", "20261119174916")
    version = os.environ.get("FAKE_ENGINE_VERSION", "159.0")

    if mode == "hang":
        child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(3600)"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pids = "%d\n%d\n" % (os.getpid(), child.pid)
        open(os.path.join(profile, "hang.pids"), "w").write(pids)
        pidfile = os.environ.get("FAKE_ENGINE_PIDFILE")
        if pidfile:
            open(pidfile, "w").write(pids)
        time.sleep(3600)
        return 0
    if mode == "exit1":
        sys.stderr.write("fake engine: cert_storage refused the import\n")
        return 1

    inter_path, onecrl_path, crlite_path = (prefs[n] for n in PREFS[1:])
    inter_bytes = open(inter_path, "rb").read()
    onecrl_bytes = open(onecrl_path, "rb").read()
    crlite_bytes = open(crlite_path, "rb").read()
    inter, onecrl, crlite = json.loads(inter_bytes), json.loads(onecrl_bytes), json.loads(crlite_bytes)
    beside = os.path.dirname(crlite_path)
    parts = [crlite["full"]] + list(crlite.get("deltas") or [])
    files = {}
    for part in parts:
        files[part["file"]] = open(os.path.join(beside, part["file"]), "rb").read()

    state = os.path.join(profile, "security_state")
    os.mkdir(state)
    open(os.path.join(state, "data.safe.bin"), "wb").write(hashlib.sha256(inter_bytes + onecrl_bytes + crlite_bytes).digest())
    if mode != "nofilter":
        open(os.path.join(state, "crlite.filter"), "wb").write(files[crlite["full"]["file"]])
    for part in crlite.get("deltas") or []:
        open(os.path.join(state, part["file"]), "wb").write(files[part["file"]])

    hashes = [sha256(files[p["file"]]) for p in parts]
    if mode == "wronghashes":
        hashes = hashes[:-1]
    stamps = {"intermediates": inter["collection_timestamp"], "onecrl": onecrl["collection_timestamp"], "cert-revocations": crlite["collection_timestamp"]}
    if mode == "wrongtimestamp":
        stamps["onecrl"] += 1
    result = {
        "format": 1,
        "ok": True,
        "error": None,
        "engine": {"version": version, "build_id": "19990101000000" if mode == "wrongbuild" else build_id},
        "collections": {
            "intermediates": {"collection_timestamp": stamps["intermediates"]},
            "onecrl": {"collection_timestamp": stamps["onecrl"]},
            "cert-revocations": {"collection_timestamp": stamps["cert-revocations"], "channel": "default"},
        },
        "crlite_filter_hashes": hashes,
    }
    if mode == "noresult":
        return 0
    with open(os.path.join(profile, "securitystate-result.json"), "w", encoding="utf-8") as f:
        f.write("{not json" if mode == "badjson" else json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
