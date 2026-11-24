"""The official build a runner capture is taken from is checked against Mozilla's own SHA256SUMS and
its signature before one byte of it is unpacked.

`scripts/retail/firefox_build.py` downloads SHA256SUMS, SHA256SUMS.asc and the installer, verifies the
signature with the release key committed in captures/runner/ (whose fingerprint is checked against the one
committed beside it), checks the installer's sha256 against its line, and only then extracts it.

Held here, with a throwaway key made by gpg so the tests need no network:
  * a good signature of the pinned key passes; one changed byte of SHA256SUMS, a signature by another key,
    and a key file that is not the pinned one are each refused;
  * an installer with another sha256 than its line is refused, and nothing is unpacked;
  * a bad signature stops the run before the installer is downloaded;
  * the committed release key is the key of the committed fingerprint.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from _retail_scripts import ROOT, load

pytestmark = pytest.mark.integration

fb = load("firefox_build")
VERSION = "159.0"
INSTALLER_PATH = f"win64/en-US/Firefox Setup {VERSION}.exe"
INSTALLER = b"MZ not a real installer, only bytes with a known digest"
INSTALLER_SHA = hashlib.sha256(INSTALLER).hexdigest()


def _gpg(cwd: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    gpg = fb.find_tool(fb.GPG_CANDIDATES, "gpg")
    return subprocess.run([gpg, "--homedir", "gnupg", "--batch", "--no-tty", "--pinentry-mode", "loopback",
                           "--passphrase", "", *args], cwd=cwd, capture_output=True, text=True, input=stdin, timeout=180)


def _make_key(base: Path, who: str) -> dict:
    """A throwaway signing key in a keyring of its own: its public key file, fingerprint file and home."""
    home = base / who
    (home / "gnupg").mkdir(parents=True)
    made = _gpg(home, "--quick-generate-key", f"{who} <{who}@example.org>", "rsa2048", "sign", "never")
    assert made.returncode == 0, made.stderr
    listing = _gpg(home, "--with-colons", "--fingerprint", "--list-keys").stdout
    fpr = next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:"))
    exported = _gpg(home, "--armor", "--export", fpr).stdout
    (home / "key.asc").write_text(exported, encoding="ascii")
    (home / "key.fingerprint").write_text(fpr + "\n", encoding="ascii")
    return {"home": home, "key": home / "key.asc", "fingerprint": home / "key.fingerprint", "fpr": fpr}


def _sign(key: dict, sums_text: str, target: Path) -> tuple[Path, Path]:
    sums = target / "SHA256SUMS"
    sums.write_text(sums_text, encoding="utf-8", newline="\n")
    (key["home"] / "SHA256SUMS").write_text(sums_text, encoding="utf-8", newline="\n")
    signed = _gpg(key["home"], "--armor", "--detach-sign", "--output", "SHA256SUMS.asc", "--yes", "SHA256SUMS")
    assert signed.returncode == 0, signed.stderr
    asc = target / "SHA256SUMS.asc"
    asc.write_bytes((key["home"] / "SHA256SUMS.asc").read_bytes())
    return sums, asc


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    base = tmp_path_factory.mktemp("keys")
    made = {"release": _make_key(base, "release"), "other": _make_key(base, "other")}
    yield made
    gpg = Path(fb.find_tool(fb.GPG_CANDIDATES, "gpg"))
    gpgconf = gpg.with_name(gpg.name.replace("gpg", "gpgconf", 1))
    for key in made.values():  # an agent left running would hold the directory on Windows
        subprocess.run([str(gpgconf), "--homedir", "gnupg", "--kill", "gpg-agent"], cwd=key["home"], capture_output=True)


def _sums_text(sha: str = INSTALLER_SHA) -> str:
    return (f"{'a' * 64}  jsshell/jsshell-linux-x86_64.zip\n{sha}  {INSTALLER_PATH}\n{'b' * 64}  win64/en-US/Firefox Setup {VERSION}.msi\n")


def _verify(keys, tmp_path, sums_text=None, signer="release", pinned="release", tamper=None):
    sums, asc = _sign(keys[signer], sums_text or _sums_text(), tmp_path)
    if tamper:
        sums.write_text(tamper(sums.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    return fb.verify_signature(sums, asc, key=keys["release"]["key"], fingerprint=keys[pinned]["fingerprint"])


# --------------------------------------------------------------------------- SHA256SUMS


def test_a_sums_file_is_read_line_by_line():
    parsed = fb.parse_sha256sums(_sums_text())
    assert parsed[INSTALLER_PATH] == INSTALLER_SHA and len(parsed) == 3
    sha, line = fb.sums_line(_sums_text(), VERSION)
    assert (sha, line) == (INSTALLER_SHA, f"{INSTALLER_SHA}  {INSTALLER_PATH}")


@pytest.mark.parametrize("text", ["not a sums line\n", f"{'a' * 63}  short.exe\n", f"{'A' * 64}  upper.exe\n"])
def test_a_malformed_sums_line_is_refused(text):
    with pytest.raises(fb.VerifyError, match="SHA256SUMS line 1"):
        fb.parse_sha256sums(text)


def test_a_path_named_twice_with_two_digests_is_refused():
    with pytest.raises(fb.VerifyError, match="twice"):
        fb.parse_sha256sums(f"{'a' * 64}  x.exe\n{'b' * 64}  x.exe\n")


def test_a_release_without_its_installer_line_is_refused():
    with pytest.raises(fb.VerifyError, match="no line"):
        fb.sums_line(f"{'a' * 64}  other.exe\n", VERSION)


def test_an_installer_with_a_bad_hash_is_refused(tmp_path):
    path = tmp_path / "installer.exe"
    path.write_bytes(INSTALLER)
    assert fb.check_installer(path, INSTALLER_SHA) == INSTALLER_SHA
    with pytest.raises(fb.VerifyError, match="is not the"):
        fb.check_installer(path, "0" * 64)
    path.write_bytes(INSTALLER + b"x")  # one byte more
    with pytest.raises(fb.VerifyError, match="is not the"):
        fb.check_installer(path, INSTALLER_SHA)


# --------------------------------------------------------------------------- the signature


def test_a_good_signature_of_the_pinned_key_passes(keys, tmp_path):
    got = _verify(keys, tmp_path)
    assert got["primary_fingerprint"] == keys["release"]["fpr"].upper()


def test_a_bad_signature_is_refused(keys, tmp_path):
    """SHA256SUMS changed after it was signed: one digit of one line."""
    with pytest.raises(fb.VerifyError, match="BADSIG|does not verify"):
        _verify(keys, tmp_path, tamper=lambda t: t.replace(INSTALLER_SHA[:8], "deadbeef", 1))


def test_a_signature_by_another_key_is_refused(keys, tmp_path):
    with pytest.raises(fb.VerifyError, match="not good|NO_PUBKEY|pinned"):
        _verify(keys, tmp_path, signer="other")


def test_a_key_file_that_is_not_the_pinned_key_is_refused(keys, tmp_path):
    sums, asc = _sign(keys["release"], _sums_text(), tmp_path)
    with pytest.raises(fb.VerifyError, match="not the pinned"):
        fb.verify_signature(sums, asc, key=keys["release"]["key"], fingerprint=keys["other"]["fingerprint"])


def test_a_fingerprint_file_that_is_not_a_fingerprint_is_refused(keys, tmp_path):
    sums, asc = _sign(keys["release"], _sums_text(), tmp_path)
    bad = tmp_path / "fp"
    bad.write_text("not a fingerprint\n", encoding="ascii")
    with pytest.raises(fb.VerifyError, match="fingerprint"):
        fb.verify_signature(sums, asc, key=keys["release"]["key"], fingerprint=bad)


def test_the_committed_release_key_is_the_committed_fingerprint(tmp_path):
    """The anchor itself: the key file in the repository imports to one key, the pinned one."""
    gpg = fb.find_tool(fb.GPG_CANDIDATES, "gpg")
    (tmp_path / "gnupg").mkdir()
    (tmp_path / "key.asc").write_bytes(fb.KEY_FILE.read_bytes())
    run = subprocess.run([gpg, "--homedir", "gnupg", "--batch", "--no-tty", "--import", "key.asc"], cwd=tmp_path,
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    listing = subprocess.run([gpg, "--homedir", "gnupg", "--batch", "--with-colons", "--fingerprint", "--list-keys"],
                             cwd=tmp_path, capture_output=True, text=True).stdout
    primaries = [ln.split(":")[9] for ln in listing.splitlines() if ln.startswith("fpr:")][:1]
    assert primaries == [fb.read_fingerprint(fb.FINGERPRINT_FILE)]
    assert "Mozilla Software Releases" in listing


# --------------------------------------------------------------------------- the whole fetch


def _serve(sums: Path | None, asc: Path | None, installer: bytes, asked: list):
    def get(url: str, dest: Path) -> None:
        asked.append(url)
        name = url.rsplit("/", 1)[1]
        if name == "SHA256SUMS":
            dest.write_bytes(sums.read_bytes())
        elif name == "SHA256SUMS.asc":
            dest.write_bytes(asc.read_bytes())
        else:
            assert url.endswith("Firefox%20Setup%20159.0.exe"), url
            dest.write_bytes(installer)
    return get


def _unpacker(unpacked: list):
    def unpack(installer: Path, dest: Path, _sevenzip) -> None:
        unpacked.append(installer)
        (dest / "core").mkdir(parents=True)
        (dest / "core" / "firefox.exe").write_bytes(b"MZ")
        (dest / "core" / "application.ini").write_text("[App]\nName=Firefox\nVersion=159.0\nBuildID=20260101000000\n", encoding="utf-8")
    return unpack


def _fetch(keys, tmp_path, asked=None, installer=INSTALLER, tamper=None, unpacked=None):
    served = tmp_path / "served"
    served.mkdir()
    sums, asc = _sign(keys["release"], _sums_text(), served)
    if tamper:
        sums.write_text(tamper(sums.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    asked = [] if asked is None else asked
    unpacked = [] if unpacked is None else unpacked
    info = fb.fetch(VERSION, tmp_path / "work", get=_serve(sums, asc, installer, asked), unpack=_unpacker(unpacked),
                    key=keys["release"]["key"], fingerprint=keys["release"]["fingerprint"])
    return info, asked, unpacked


def test_a_verified_build_is_unpacked_and_described(keys, tmp_path):
    info, asked, unpacked = _fetch(keys, tmp_path)
    assert len(unpacked) == 1 and len(asked) == 3
    assert info["installer_sha256"] == INSTALLER_SHA and info["sums_line"] == f"{INSTALLER_SHA}  {INSTALLER_PATH}"
    assert info["build_id"] == "20260101000000" and info["signature"]["verified"] is True
    assert info["url"].startswith(f"{fb.BASE}{VERSION}/win64/en-US/") and "%20" in info["url"]
    assert (tmp_path / "work" / "build.json").is_file()


def test_a_bad_installer_hash_unpacks_nothing(keys, tmp_path):
    unpacked: list = []
    with pytest.raises(fb.VerifyError, match="is not the"):
        _fetch(keys, tmp_path, installer=INSTALLER + b"tampered", unpacked=unpacked)
    assert unpacked == []
    assert not (tmp_path / "work" / "extracted").exists()


def test_a_bad_signature_stops_before_the_installer_is_downloaded(keys, tmp_path):
    asked: list = []
    with pytest.raises(fb.VerifyError):
        _fetch(keys, tmp_path, asked=asked, tamper=lambda t: t.replace(INSTALLER_SHA[:8], "deadbeef", 1))
    assert len(asked) == 2 and not any("Setup" in u for u in asked), asked


def test_a_url_outside_the_release_directory_is_refused():
    with pytest.raises(fb.VerifyError):
        fb.check_url("https://example.org/pub/firefox/releases/159.0/SHA256SUMS", VERSION)
    with pytest.raises(fb.VerifyError):
        fb.check_url(f"{fb.BASE}158.0/SHA256SUMS", VERSION)
    with pytest.raises(fb.VerifyError):
        fb.check_url(f"{fb.BASE}{VERSION}/../158.0/SHA256SUMS", VERSION)


def test_the_script_pins_what_the_workflow_relies_on():
    text = (ROOT / "scripts" / "retail" / "firefox_build.py").read_text(encoding="utf-8")
    assert 'BASE = "https://archive.mozilla.org/pub/firefox/releases/"' in text
    assert (ROOT / "captures" / "runner" / "mozilla-release-key.asc").is_file()
    assert fb.read_fingerprint(fb.FINGERPRINT_FILE) == "14F26682D0916CDD81E37B6D61B7B526D98F0353"
