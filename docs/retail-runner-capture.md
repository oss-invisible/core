# Capturing retail Firefox on a GitHub runner

One run of `.github/workflows/retail-capture.yml` visits the host-gate page with the official retail Firefox on a
GitHub Windows runner and produces a dated capture in the format of [retail-captures.md](retail-captures.md), plus
what the browser sends on the wire. It is the capture a table of the registry can name when the registry says the
`github-runner` is enough.

## Trigger

The workflow runs when a pull request, or a push to `main`, changes `captures/REQUEST.json`:

```json
{"firefox_version": "159.0", "dpr": [1]}
```

Nothing else starts it: no schedule, no manual run. `scripts/retail/capture_request.py` validates the file in the
first step: a release version (`159.0`, `159.0.1`; no beta, no ESR, no path) and `dpr` equal to `[1]` (a runner has
a display scale of 1 and the capture sets no preference to change it). One job on `windows-latest`, one browser.

## The build

The job runs the official en-US win64 installer of the requested version, which it checks before it uses a byte of it
(`scripts/retail/firefox_build.py fetch`):

1. `SHA256SUMS`, `SHA256SUMS.asc` and `win64/en-US/Firefox Setup <version>.exe` are downloaded from
   `archive.mozilla.org/pub/firefox/releases/<version>/` (that host and that directory only, https).
2. The release signing key, `captures/runner/mozilla-release-key.asc`, is imported into an empty keyring and its
   primary fingerprint must be the one in `captures/runner/mozilla-release-key.fingerprint`.
3. `SHA256SUMS.asc` must verify over `SHA256SUMS`: a good signature by that primary key, and none of the statuses
   bad, expired, revoked or unknown key.
4. The installer's sha256 must be the one on its line of `SHA256SUMS`.
5. The installer is extracted with 7z. Nothing is installed. `core/application.ini` must say Firefox and the
   requested version, and gives the BuildID.

A failed check stops the job before the next step; the installer of a bad signature is never downloaded, and one of a
bad hash is never extracted. When Mozilla signs with a new subkey the key file is refreshed from a keyserver by the
fingerprint in the file beside it; the fingerprint file does not change.

## The launch

`scripts/retail/run_retail_capture.py` starts exactly

```
firefox.exe -no-remote -profile <empty directory> http://127.0.0.1:<port>/?dpr1
```

A fresh profile with no preference (no `user.js`, no `prefs.js` written by us), nothing in
`distribution/policies.json` (a policy turns Web Serial off), no Marionette, no remote protocol, no WebDriver, no
`MOZ_*` variable. `assert_plain_launch` checks the command, the profile, the install folder and the environment
before the spawn and refuses otherwise.

The run waits until the page posts its result (timeout 300 s, with a screenshot of the screen when it is reached),
then ends the launched tree and every process whose executable is in the install folder, again until none is left, and
counts them. A result is accepted only with zero processes left.

## What is captured

* The page `captures/pages/host-gate-2.html`: `host-gate-1.html` plus a block, run just before the result is
  posted, that makes requests one after the other. The result is still format `host-gate/1` with the same items; the
  block adds no item and no noise key. (`host-gate-1.html` is not edited: the hashes of the existing captures name it.)
* `scripts/retail/capture_server.py` serves the page on `127.0.0.1`, stores the POST as `dpr1.json`, and records, as
  `wire.json` (`retail-wire/1`):
  * `http_requests`: every request in arrival order with its request line and its header lines in the order and the
    case they were sent (`raw_head`, and `headers` as `[name, value]` pairs). They are the navigation, the page's
    subresources (style sheet, font, script, image, audio, fetch, a POST, XMLHttpRequest, in that order), the
    favicon, and the result's POST;
  * `tls_connections`: for each connection to the TLS port that the page fetches over https, the raw bytes of the
    first TLS record (the ClientHello) as `raw_hex`, and `scripts/retail/clienthello.py`'s reading of it: record,
    handshake, cipher suites, extensions in order, JA3 string. The server answers a fatal alert, so the handshake fails
    and the page reads nothing from it;
  * `certificate_requests`: the `seq` of every request that is an OCSP, CA-issuer or CRL fetch (by path or media
    type). It is empty when the browser makes none to this server.

## The output

`scripts/retail/make_runner_capture.py` writes `captures/firefox-<version>/github-runner/<date>-dpr1/`:

* `items.json` and `meta.json`, made by `import_host_gate.py` (so the format and the public redaction rule are the
  importer's). `meta.json` has host kind `github-runner`, `gpu_family` `none`, the OS build, `build_id` read from
  `application.ini`, `installer` with its url, sha256, the line of `SHA256SUMS` and the key it was verified with,
  `runner` with the image name and version, and `page` host-gate-2;
* `wire.json`.

The workflow uploads, as the artifact `retail-capture-<version>` (90 days, also when a step is red): that directory laid
out as under `captures/`, the raw result `raw/dpr1.json` (everything the page returned, which the public rule has not
yet been applied to: it is the raw capture), `raw/wire.json`, `raw/run.json`, `build.json` and, after a timeout,
`raw/timeout-dpr1.png`. `meta.raw.kept` names the artifact of the run.

## Which tables it regenerates

```
python scripts/retail/check_runner_capture.py captures/firefox-159.0/github-runner/<date>-dpr1
```

For every `generated` entry of `captures/pinned.json` it runs the generator as if this capture were each of its
sources and compares with the committed table: `regenerates` (the committed bytes), `differs` (the keys that differ),
`cannot` (the generator refuses it, and says why in words: an item the capture does not have, or one of another shape,
such as the WebGL lists of a host with no WebGL context) or `error` (the generator raised an exception it does not
know: a bug, never a statement about the capture). An entry whose `host_needed` names the `github-runner` (R01, the
navigator; R32, R36 and R37) is runner-capturable: the check is red when one is not regenerated. The tables that need
Windows 11, a GPU or another display scale (R19 encode, R22, R23) are reported and never make it red, except for an
`error`, which is red for every table. The workflow runs this as its last step, and
`tests/test_a_runner_capture_regenerates_the_runner_tables.py` runs it on every runner capture committed under
`captures/`.

## Committing a runner capture

A pull request copies the artifact's `captures/` directory into the repository, names the capture as a source of the
tables it regenerates in `captures/pinned.json` (the registry test requires every capture to be named), and keeps
`wire.json` next to it. The importer's rule has already been applied to `items.json`; `raw/dpr1.json` is not committed.
