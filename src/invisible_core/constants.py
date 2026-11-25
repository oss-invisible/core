"""Constants. Everything that describes the engine is a projection of the seal.

No engine fact is hand-edited here. BINARY_VERSION and FIREFOX_UPSTREAM_VERSION
used to be two literals that had to move together, which is how a tag and a base
version could disagree inside one published commit. They, BUILD_ID, CONTRACT_N,
SEAL_DIGEST, SOURCE_COMMIT and everything computed from them (BINARY_BASENAME,
UA_VERSION, USER_AGENT, and ARCHIVE_NAME, which returns the sealed asset name
verbatim whenever the seal has assets) come out of seal.json, which CI generated
from the archives it published, so they cannot disagree with each other or with
the engine.

What IS hand-written, because no seal carries it: the "firefox-" / "-stealth"
affixes and the per-platform archive suffixes that surround the sealed version
in BINARY_BASENAME / ARCHIVE_NAME, RELEASE_URL_TEMPLATE (the repo that hosts the
releases), and the four GEOIP_* constants (a third-party repo, its asset
name and its download URL). Those move only when the hosting repo, the archive
layout or the upstream GeoIP project moves.

The NAMES are the binary/wrapper contract and never change.
"""
from __future__ import annotations

from typing import NamedTuple

from ._retail import table as _retail_table
from ._version import CORE_REVISION, __version__ as PKG_VERSION
from .seal import active_seal, normalize_arch

_SEAL = active_seal()

BINARY_VERSION: str = _SEAL.tag
FIREFOX_UPSTREAM_VERSION: str = _SEAL.upstream_version
# The BuildID of the leg THIS host runs. The published legs (SUPPORTED_LEGS in
# seal.py) are separate CI builds with separate BuildIDs, so there is no
# seal-wide value to export here; the launch-time check compares against the
# per-asset one, not against this.
BUILD_ID: str = _SEAL.build_id
CONTRACT_N: int = _SEAL.contract_n
SEAL_DIGEST: str = _SEAL.digest
SOURCE_COMMIT: str = _SEAL.source_commit

BINARY_BASENAME: str = f"firefox-{FIREFOX_UPSTREAM_VERSION}-stealth"

# The navigator's identity strings are what a page of retail Firefox for Windows
# answers, generated from a dated capture (data/retail/navigator.json, see
# docs/retail-captures.md); the only thing this module adds is the version, which is
# the sealed engine's, never the capture's.
_NAVIGATOR = _retail_table("navigator")

# Spoofed User-Agent. Firefox puts only MAJOR.MINOR in the UA (a real 150.0.1
# build reports "Firefox/150.0"), so the truncation is part of the form.
UA_VERSION: str = ".".join(FIREFOX_UPSTREAM_VERSION.split(".")[:2])
USER_AGENT: str = _NAVIGATOR["user_agent_template"].format(version=UA_VERSION)

# The other halves of the same claim. They live here, beside the
# User-Agent they have to agree with, because they used to be written out
# twice: once in prefs._NAVIGATOR_OVERRIDES and once in the sampler's
# _LOCKED, with nothing tying the two together. USER_AGENT was already
# centralised; these were not, so they were the ones that could drift.
#
# The drift is not hypothetical. The same shape was found and fixed on
# 2026-08-09 for max_touch_points, where a stale second copy silently won
# over the constant that was supposed to own the value, and it was invisible
# because both copies said 0.
PLATFORM_OVERRIDE: str = _NAVIGATOR["platform"]
OSCPU_OVERRIDE: str = _NAVIGATOR["oscpu"]
APPVERSION_OVERRIDE: str = _NAVIGATOR["app_version"]

# Height of the Windows taskbar in CSS pixels at 100% DPI, i.e. how much
# shorter screen.availHeight is than screen.height. It lives here, in the
# module both the sampler and the profile already import, because it was the
# same number written out in three places - the generator, nsScreen.cpp and
# nsGlobalWindowOuter.cpp - with nothing tying them together.
TASKBAR_PX: int = 48

# --- The browser chrome above the page, Firefox 159 -------------------------
#
# What the window client top to the content top is made of, and what decides
# it. Read from the retail 159.0 browser/omni.ja (sha256 e079c395...a610; the
# analysis is RetailCaptures analysis/window-geometry/chrome-density/README.md,
# files and lines below are inside that omni.ja). Each term is CSS px at scale 1.
#
# The height is a density attribute set by script, not a media query:
# browser.js gUIDensity switches the window to "compact" by itself while
# browser.uidensity has no user value (a fresh profile has none).

#: Tab strip: --tab-min-height (tabs.css L9, L16) + 2 x --tab-margin-block
#: (tab.tokens.css L145, tabs.css L80-86); #tabbrowser-tabs min-height
#: (tabs.css L50, L189). Normal 32 + 2 x 6, compact 28 + 2 x 4.
TABSTRIP_CSS_NORMAL: int = 32 + 2 * 6
TABSTRIP_CSS_COMPACT: int = 28 + 2 * 4

#: Nav bar: --urlbar-height (urlbar/variables.css L14, L31) + 2 x
#: --urlbar-padding-block of 4 (urlbar.tokens.css L26, urlbar.css L197).
#: Normal 32 + 2 x 4, compact 28 + 2 x 4.
NAVBAR_CSS_NORMAL: int = 32 + 2 * 4
NAVBAR_CSS_COMPACT: int = 28 + 2 * 4

#: .browserContainer border-block-start, --border-width (content-area.css L53,
#: L70). Layout snaps it down to whole device pixels, at least one.
CONTENT_BORDER_CSS: int = 1

#: AUTO_COMPACT_REFERENCE_TABSTRIP_HEIGHT (browser.js gUIDensity).
AUTO_COMPACT_REFERENCE_TABSTRIP_CSS: int = 36
#: browser.compactmode.auto.threshold, default "0.05" (firefox.js).
AUTO_COMPACT_THRESHOLD: float = 0.05
#: The window is compact when 36 / H > 0.05, i.e. H < 36 / 0.05 = 720 CSS px;
#: H = 720 is normal (36 / 720 is not greater than 0.05).
AUTO_COMPACT_BELOW_CSS: float = AUTO_COMPACT_REFERENCE_TABSTRIP_CSS / AUTO_COMPACT_THRESHOLD

#: Firefox's smallest main window, chrome inner size in CSS px (width, height).
#: Measured live on retail 159.0 at scales 1 and 1.25, 500 wide at all four
#: (GitHub runs oss-invisible/core 45657528859, 45672953042, 45679625406;
#: RetailCaptures efb8d8f).
MIN_INNER_CSS: tuple = (500, 120)

#: Where a restored main window sits after Juggler's resize, device px on both
#: axes. Measured live at scales 1, 1.25, 1.5 and 2 (same runs as MIN_INNER_CSS).
RESTORED_POSITION: int = 4


def _half_up(value: float) -> int:
    """Round half up, as layout snaps a box edge to a device pixel."""
    return int(value + 0.5)


def ui_density(reference_height_css: float) -> str:
    """The UI density Firefox 159 picks on its own: "compact" below 720 CSS
    px of reference height, "normal" from 720 (browser.js gUIDensity)."""
    if AUTO_COMPACT_REFERENCE_TABSTRIP_CSS / reference_height_css > AUTO_COMPACT_THRESHOLD:
        return "compact"
    return "normal"


def density_reference_height(avail_height_css: float, chrome_inner_height_css: float,
                             maximized: bool) -> float:
    """The height the density rule reads (browser.js _densityReferenceSize):
    for a maximized window the larger of screen.availHeight and the chrome
    window's innerHeight, for a restored one the chrome innerHeight."""
    if maximized:
        return max(avail_height_css, chrome_inner_height_css)
    return chrome_inner_height_css


def chrome_height_device(scale: float, density: str) -> int:
    """The chrome height above the content in device px, from the window
    client top to the content top, the same for a maximized and a restored
    window: tab strip and nav bar each snapped half up, plus the border."""
    if density == "compact":
        tabstrip, navbar = TABSTRIP_CSS_COMPACT, NAVBAR_CSS_COMPACT
    elif density == "normal":
        tabstrip, navbar = TABSTRIP_CSS_NORMAL, NAVBAR_CSS_NORMAL
    else:
        raise ValueError("density must be 'normal' or 'compact', not %r" % (density,))
    border = max(1, int(CONTENT_BORDER_CSS * scale))
    return _half_up(tabstrip * scale) + _half_up(navbar * scale) + border


class WindowFrame(NamedTuple):
    """The window frame of Windows Firefox at one display scale, in DEVICE
    pixels."""
    #: Windows' invisible resize border, on the left, right and bottom of every
    #: window and on top of a popup. A maximized window sits this far OFF the
    #: screen on three sides. The same for a restored window.
    border: int
    #: A maximized window: from the screen top to the content top (tab strip
    #: plus toolbar, seen from the screen). This is the Firefox 151
    #: measurement, which is what the published engine reads. Firefox 159
    #: answers the density rule instead (ui_normal / ui_compact, see
    #: declared_maximized_ui); switching is pending a decision
    #: (MAXIMIZED_UI_FROM_DENSITY).
    maximized_ui: int
    #: A popup: from the window top, border included, to the content top.
    popup_ui: int
    #: Where a popup opened without left/top is put, on both axes.
    popup_position: int
    #: Chrome height above the content with the normal density, maximized or
    #: restored (chrome_height_device).
    ui_normal: int
    #: Chrome height above the content with the compact density.
    ui_compact: int
    #: Where a restored main window sits, on both axes (RESTORED_POSITION).
    restored_position: int


def _frame(border: int, maximized_ui: int, popup_ui: int, scale: float) -> WindowFrame:
    return WindowFrame(
        border=border, maximized_ui=maximized_ui, popup_ui=popup_ui, popup_position=4,
        ui_normal=chrome_height_device(scale, "normal"),
        ui_compact=chrome_height_device(scale, "compact"),
        restored_position=RESTORED_POSITION)


#: The window frame per display scale, measured on retail Firefox 151 on
#: Windows 11 on 2026-10-08, lending the display scale (the workbench's
#: scripts/window_geometry/scales.py). Measured, not chosen: a page reads
#: screenX, outerWidth - availWidth and mozInnerScreenY in one line each, and
#: retail answers -8, 16 and 85 at 100%, -7, 15 and 85.6 at 125%. In device
#: pixels the four rows are one rule; every CSS value is the device value
#: divided by the scale with Gecko's rounding, done by the engine.
#:
#: The keys are the DOMAIN: the four scales the screen table gives personas
#: (cpt_screen_given_class_tier.json). A scale outside it has no measured frame
#: and is refused, never interpolated (engine rule 2).
#:
#: ui_normal / ui_compact are computed (chrome_height_device), not typed: 85 /
#: 73, 106 / 91, 127 / 109, 170 / 146, the first seven measured live on
#: Firefox 159.0 (the runs listed at MIN_INNER_CSS), 170 the model's value.
#:
#: ⛔ It replaces CHROME_W = 0 and CHROME_H = 85 with window_x = window_y = 0,
#: from a 2026-08-09 measurement of a window that was not a maximized Windows
#: Firefox (a 768-pixel-tall screen, no resize border): it described a window
#: at the screen origin with no side border, which Windows never draws
#: maximized, and every popup answered the same numbers.
WINDOW_FRAME_BY_DPR: dict = {
    1.0: _frame(8, 85, 72, 1.0),
    1.25: _frame(9, 107, 90, 1.25),
    1.5: _frame(11, 127, 106, 1.5),
    2.0: _frame(13, 169, 139, 2.0),
}

#: Whether the maximized window's chrome height declared to the engine is the
#: density rule's (Firefox 159) instead of the Firefox 151 `maximized_ui`.
#: False until the switch is decided: the published engine reads
#: `maximized_ui`, so nothing changes while this is False.
MAXIMIZED_UI_FROM_DENSITY: bool = False


def declared_maximized_ui(frame: WindowFrame, avail_height_css: float) -> int:
    """The chrome height of a maximized window, in device px, to declare.

    While MAXIMIZED_UI_FROM_DENSITY is False it is frame.maximized_ui. When
    True it is the density rule's value for the work area's height: a
    maximized window's chrome innerHeight is never larger than screen
    availHeight, so availHeight is the reference."""
    if not MAXIMIZED_UI_FROM_DENSITY:
        return frame.maximized_ui
    density = ui_density(density_reference_height(avail_height_css, avail_height_css, True))
    return frame.ui_compact if density == "compact" else frame.ui_normal


def ARCHIVE_NAME(platform_key: str, machine: str) -> str:
    """The platform-specific archive filename, taken from the seal when it has
    assets (the sealed name is the published name, by construction) and
    reconstructed only for a local seal."""
    pk = platform_key.lower()
    arch = normalize_arch(machine)
    if not _SEAL.is_local:
        return _SEAL.asset_for(pk, machine).name
    if pk == "win32":
        return f"{BINARY_BASENAME}-win-{arch}.zip"
    if pk == "linux":
        return f"{BINARY_BASENAME}-linux-{arch}.tar.gz"
    raise NotImplementedError(f"unsupported platform: {platform_key}")

# GitHub release URL template. Binaries are hosted on the source repo
# (firefox_antidetect_patch) since firefox-14 - the same repo that builds them,
# so every consumer fetches from one place: the Playwright wrapper, and until
# its 2026-08-18 deletion, the direct-launch profile-manager too. (firefox-13
# and earlier lived on invisible_playwright.)
RELEASE_URL_TEMPLATE = (
    "https://github.com/oss-invisible/firefox_antidetect_patch/releases/download/{tag}/{asset}"
)

# ─────────────────────────────────────────────────────────────────────────
#  GeoIP database (timezone="auto" → resolve IANA zone from proxy egress IP)
# ─────────────────────────────────────────────────────────────────────────
# daijro/geoip-all-in-one merges IP2Location LITE + GeoLite2 + DB-IP into a
# single mmdb (country ISO + coordinates + IANA timezone via tzfpy), rebuilt
# weekly. GPL-3.0, so we DOWNLOAD it at runtime into the user cache (like the
# Firefox binary) rather than bundling it into this MIT package. The `-all`
# variant covers IPv4+IPv6. download.py NEVER pins a tag (daijro prunes old
# releases, so a pinned tag eventually 404s): on every launch it resolves the
# CURRENT latest tag from the `releases/latest/download` permalink (no GitHub
# API, no rate limit) and pulls it if newer than the cache.
GEOIP_REPO: str = "daijro/geoip-all-in-one"
GEOIP_ASSET: str = "geoip-aio-all.mmdb.zip"
GEOIP_MMDB_NAME: str = "geoip-aio-all.mmdb"
GEOIP_RELEASE_URL_TEMPLATE: str = (
    "https://github.com/daijro/geoip-all-in-one/releases/download/{tag}/{asset}"
)

# ─────────────────────────────────────────────────────────────────────────
#  Preloaded intermediate certificates (Mozilla's Remote Settings, collection security-state/intermediates)
# ─────────────────────────────────────────────────────────────────────────
# A retail Firefox keeps the intermediates of the public web preloaded in cert_storage, from this collection; the engine no longer syncs it, so the core
# downloads it from the same service Firefox uses (from the USER's Python, never from the engine), verifies it, and hands it to the engine's generate mode
# as a file (the pref INTERMEDIATES_PREF, written only on the work profile of a generation). The addresses are the ones the engine's own source reads: the
# server is the default of `services.settings.server` (`toolkit/modules/AppConstants.sys.mjs`, REMOTE_SETTINGS_SERVER_URLS), the changes feed is
# `Utils.CHANGES_PATH` (`services/settings/Utils.sys.mjs`), the changeset path is `RemoteSettingsClient._fetchChangeset`, and the bundle is
# `Downloader.cacheAll` (`services/settings/Attachments.sys.mjs`).
REMOTE_SETTINGS_SERVER: str = "https://firefox.settings.services.mozilla.com/v2"
INTERMEDIATES_COLLECTION_ID: str = "intermediates"
#: The server's root answers `capabilities.attachments.base_url`, where every attachment (and the bundle) lives.
INTERMEDIATES_SERVER_INFO_URL: str = REMOTE_SETTINGS_SERVER + "/"
#: Under that base: ONE zip with every attachment of the collection (`<record id>.meta.json` + `<record id>`).
INTERMEDIATES_BUNDLE_PATH: str = "bundles/security-state--intermediates.zip"
#: The file the engine reads, and the pref that names it (an absolute path).
INTERMEDIATES_FILE_NAME: str = "intermediates.json"
INTERMEDIATES_PREF: str = "zoom.stealth.intermediates.file"

# ─────────────────────────────────────────────────────────────────────────
#  The security state of a retail profile, as ready files (intermediates, OneCRL, CRLite)
# ─────────────────────────────────────────────────────────────────────────
# A retail Firefox 159 keeps three collections of the bucket `security-state` in cert_storage (`security/manager/ssl/RemoteSecuritySettings.sys.mjs`):
# `intermediates` (above), `onecrl` (revocations of intermediates and roots, records without attachments, every record passed to `setRevocations` with
# STATE_ENFORCE) and `cert-revocations` (the CRLite filters: of the records whose `filter_expression` holds for the channel `security.pki.crlite_channel`,
# exactly one full filter, `incremental: false`, and the deltas, each attachment checked for sha256 and size). The core downloads them from the same service,
# hands them ONCE to the engine in its generate mode (the four prefs below, on an empty work profile), and keeps what the engine wrote
# (`<profile>/security_state/` and `securitystate-result.json`); a launch copies that directory into its profile and sets no pref of the security state.
SECURITY_STATE_BUCKET: str = "security-state"
#: The timestamps of every collection of the bucket, in one answer of the changes feed (a few hundred bytes).
SECURITY_STATE_LATEST_URL: str = (
    REMOTE_SETTINGS_SERVER + "/buckets/monitor/collections/changes/changeset?_expected=0&bucket=security-state"
)
#: One collection of the bucket (`expected` = its timestamp from the feed, or 0).
SECURITY_STATE_CHANGESET_URL_TEMPLATE: str = (
    REMOTE_SETTINGS_SERVER + "/buckets/security-state/collections/{collection}/changeset?_expected={expected}"
)
ONECRL_COLLECTION_ID: str = "onecrl"
CRLITE_COLLECTION_ID: str = "cert-revocations"
#: The default of `security.pki.crlite_channel` on desktop (`modules/libpref/init/StaticPrefList.yaml`); the core sets no other.
CRLITE_CHANNEL: str = "default"
#: cert_storage treats the filters as absent ten days after the last `setFullCRLiteFilter`/`addCRLiteDelta` (`cert_storage/src/lib.rs`, `is_crlite_fresh` on `last_crlite_update`).
CRLITE_MAX_AGE_S: int = 10 * 24 * 3600
#: The files the engine reads in its generate mode (formats in `_revocations.py` and `_intermediates.py`).
ONECRL_FILE_NAME: str = "onecrl.json"
CRLITE_FILE_NAME: str = "crlite.json"
#: The generate mode: the switch and the three inputs (absolute paths). Written only on the work profile of a generation, never on a launch.
SECURITY_STATE_GENERATE_PREF: str = "zoom.stealth.securitystate.generate"
ONECRL_PREF: str = "zoom.stealth.onecrl.file"
CRLITE_PREF: str = "zoom.stealth.crlite.file"
#: What the generate mode leaves in the work profile: cert_storage's directory (copied as is into every profile) and the engine's report.
SECURITY_STATE_DIR_NAME: str = "security_state"
SECURITY_STATE_RESULT_NAME: str = "securitystate-result.json"
