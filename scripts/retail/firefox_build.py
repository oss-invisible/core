"""The official Firefox a runner capture is taken from: fetched, verified, extracted, never installed.

    python scripts/retail/firefox_build.py fetch --version 159.0 --work DIR

What it does, in this order, and stops at the first thing that is not as expected:

  1. downloads SHA256SUMS, SHA256SUMS.asc and `win64/en-US/Firefox Setup <version>.exe` from
     archive.mozilla.org (https only, that host and that release directory only);
  2. imports the release signing key committed in captures/runner/ into a fresh keyring, and
     checks that its fingerprint is the one committed next to it;
  3. verifies SHA256SUMS.asc over SHA256SUMS with that key: a good signature of the pinned
     primary key, no bad, expired, revoked or unknown-key status;
  4. checks the installer's sha256 against its line in SHA256SUMS;
  5. extracts the installer with 7z into DIR/extracted (nothing is installed), and reads the
     version and the BuildID from core/application.ini.

It writes DIR/build.json: the url, the installer's sha256, the SHA256SUMS line, the key it was
verified with, the BuildID and the path of firefox.exe.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
KEY_FILE = REPO / "captures" / "runner" / "mozilla-release-key.asc"
FINGERPRINT_FILE = REPO / "captures" / "runner" / "mozilla-release-key.fingerprint"
BASE = "https://archive.mozilla.org/pub/firefox/releases/"
INSTALLER = "win64/en-US/Firefox Setup {version}.exe"
SHA256SUMS = "SHA256SUMS"
SIGNATURE = "SHA256SUMS.asc"
#: Statuses of `gpg --status-fd` that make a signature unusable, whatever else it says.
GPG_BAD = ("BADSIG", "ERRSIG", "EXPKEYSIG", "EXPSIG", "REVKEYSIG", "NO_PUBKEY", "KEYREVOKED")
GPG_CANDIDATES = (r"C:\Program Files\Git\usr\bin\gpg.exe", "/usr/bin/gpg")
SEVENZIP_CANDIDATES = (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe", "/usr/bin/7z")


class VerifyError(Exception):
    """The build is not what the release says it is. Nothing after this point may be trusted."""


# --------------------------------------------------------------------------- hashes


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_sha256sums(text: str) -> Dict[str, str]:
    """path -> sha256 of a SHA256SUMS file (`<64 hex>  <path>` per line). A malformed line is an error."""
    out: Dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        m = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if not m:
            raise VerifyError(f"SHA256SUMS line {number} is not `<sha256>  <path>`")
        digest, path = m.groups()
        if out.setdefault(path, digest) != digest:
            raise VerifyError(f"SHA256SUMS names {path!r} twice with different digests")
    return out


def sums_line(text: str, version: str) -> Tuple[str, str]:
    """(sha256, the line as written) of the en-US win64 installer of `version`."""
    path = INSTALLER.format(version=version)
    digest = parse_sha256sums(text).get(path)
    if digest is None:
        raise VerifyError(f"SHA256SUMS has no line for {path!r}")
    return digest, f"{digest}  {path}"


def check_installer(installer: Path, expected_sha256: str) -> str:
    """The installer's sha256 when it is the one SHA256SUMS names; else a VerifyError."""
    got = sha256_file(installer)
    if got != expected_sha256:
        raise VerifyError(f"installer sha256 {got} is not the {expected_sha256} that SHA256SUMS names")
    return got


# --------------------------------------------------------------------------- signature


def find_tool(candidates: Tuple[str, ...], name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for path in candidates:
        if Path(path).is_file():
            return path
    raise VerifyError(f"{name} is not installed")


def _gpg(gpg: str, cwd: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    """gpg run in `cwd` with a keyring of its own (`gnupg/`) and only relative names: the gpg of Git for
    Windows is an MSYS program, which reads neither a backslash path nor a `C:/` one."""
    return subprocess.run([gpg, "--homedir", "gnupg", "--batch", "--no-tty", *args], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


def read_fingerprint(path: Path) -> str:
    text = re.sub(r"\s+", "", Path(path).read_text(encoding="ascii")).upper()
    if not re.fullmatch(r"[0-9A-F]{40}", text):
        raise VerifyError(f"{path.name} does not hold one 40-hex fingerprint")
    return text


def verify_signature(sums: Path, signature: Path, key: Path = KEY_FILE, fingerprint: Path = FINGERPRINT_FILE,
                     gpg: Optional[str] = None) -> Dict[str, str]:
    """Check `signature` over `sums` with `key`, whose primary fingerprint must be the one in `fingerprint`.
    Returns {primary_fingerprint, signing_key}; raises VerifyError on anything else."""
    gpg = gpg or find_tool(GPG_CANDIDATES, "gpg")
    pinned = read_fingerprint(fingerprint)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        home = Path(tmp)
        (home / "gnupg").mkdir()
        shutil.copyfile(key, home / "key.asc")
        shutil.copyfile(sums, home / "sums")
        shutil.copyfile(signature, home / "sums.asc")
        imported = _gpg(gpg, home, "--import", "key.asc")
        if imported.returncode != 0:
            raise VerifyError(f"the key file did not import: {imported.stderr.strip()[-200:]}")
        listed = _gpg(gpg, home, "--with-colons", "--fingerprint", "--list-keys").stdout
        primaries, expect_primary = [], False
        for line in listed.splitlines():
            kind = line.split(":", 1)[0]
            if kind == "pub":
                expect_primary = True
            elif kind == "fpr" and expect_primary:
                primaries.append(line.split(":")[9].upper())
                expect_primary = False
        if primaries != [pinned]:
            raise VerifyError(f"the key file holds {primaries}, not the pinned {pinned}")
        run = _gpg(gpg, home, "--status-fd=1", "--verify", "sums.asc", "sums")
        statuses = [ln.split(" ")[1:] for ln in run.stdout.splitlines() if ln.startswith("[GNUPG:] ")]
        bad = sorted({s[0] for s in statuses if s and s[0] in GPG_BAD})
        if bad:
            raise VerifyError(f"the signature is not good: {', '.join(bad)}")
        valid = [s for s in statuses if s and s[0] == "VALIDSIG"]
        if run.returncode != 0 or not any(s[0] == "GOODSIG" for s in statuses) or len(valid) != 1:
            raise VerifyError(f"SHA256SUMS.asc does not verify (gpg exit {run.returncode})")
        fields = valid[0]  # VALIDSIG <signing fpr> <date> <ts> <expire> <ver> <rsvd> <pk> <hash> <class> <primary fpr>
        primary = fields[10].upper() if len(fields) > 10 else ""
        if primary != pinned:
            raise VerifyError(f"signed by {primary or 'an unknown key'}, not by the pinned {pinned}")
        return {"primary_fingerprint": primary, "signing_key": fields[1].upper()}


# --------------------------------------------------------------------------- download, extract


def check_url(url: str, version: str) -> str:
    if not url.startswith(f"{BASE}{version}/") or ".." in url:
        raise VerifyError(f"{url!r} is not in the release directory of {version} on archive.mozilla.org")
    return url


def download(url: str, dest: Path, attempts: int = 3) -> None:
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=120) as response, open(dest, "wb") as out:  # noqa: S310 (https, checked)
                shutil.copyfileobj(response, out, 1 << 20)
            return
        except OSError as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise VerifyError(f"could not download {url}: {last}")


def extract(installer: Path, dest: Path, sevenzip: Optional[str] = None) -> None:
    """Unpack the installer with 7z. Nothing is run and nothing is installed."""
    sevenzip = sevenzip or find_tool(SEVENZIP_CANDIDATES, "7z")
    run = subprocess.run([sevenzip, "x", "-y", f"-o{dest}", str(installer)], capture_output=True, text=True, timeout=600)
    if run.returncode != 0:
        raise VerifyError(f"7z failed ({run.returncode}): {run.stdout[-300:]}{run.stderr[-300:]}")


def read_application_ini(path: Path) -> Dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(path, encoding="utf-8")
    return dict(parser["App"])


# --------------------------------------------------------------------------- the whole fetch


def fetch(version: str, work: Path, get: Callable[[str, Path], None] = download, gpg: Optional[str] = None,
          sevenzip: Optional[str] = None, unpack: Callable[..., None] = extract, key: Path = KEY_FILE,
          fingerprint: Path = FINGERPRINT_FILE) -> Dict[str, Any]:
    """Download, verify and extract `version` into `work`; returns what build.json holds."""
    work.mkdir(parents=True, exist_ok=True)
    installer = work / "installer.exe"
    sums, asc = work / SHA256SUMS, work / SIGNATURE
    release = f"{BASE}{version}/"
    get(check_url(release + SHA256SUMS, version), sums)
    get(check_url(release + SIGNATURE, version), asc)
    signature = verify_signature(sums, asc, key=key, fingerprint=fingerprint, gpg=gpg)
    sha, line = sums_line(sums.read_text(encoding="utf-8"), version)
    url = check_url(release + INSTALLER.format(version=version).replace(" ", "%20"), version)
    get(url, installer)
    check_installer(installer, sha)
    extracted = work / "extracted"
    if extracted.exists():
        shutil.rmtree(extracted)
    unpack(installer, extracted, sevenzip)
    core = extracted / "core"
    exe, ini = core / "firefox.exe", core / "application.ini"
    if not exe.is_file() or not ini.is_file():
        raise VerifyError("the installer has no core/firefox.exe and core/application.ini")
    app = read_application_ini(ini)
    if app.get("Version") != version or app.get("Name") != "Firefox":
        raise VerifyError(f"application.ini says {app.get('Name')} {app.get('Version')}, not Firefox {version}")
    info = {"firefox_version": version, "url": url,
            "installer_sha256": sha, "sums_line": line,
            "signature": {"verified": True, **signature}, "build_id": app["BuildID"],
            "build_id_source": "core/application.ini of the extracted official installer",
            "install_dir": "extracted/core"}
    (work / "build.json").write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return info


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--version", required=True)
    f.add_argument("--work", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        info = fetch(args.version, args.work)
    except VerifyError as exc:
        print(f"firefox_build: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(info, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
