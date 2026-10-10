"""Offer the latest release when the interface opens.

Before the interface takes the terminal, hamrlog looks at the latest GitHub
release and, when it is newer, asks in the terminal whether to download and
install it:

* Installed with the Linux installer: the release tarball is downloaded,
  checked against SHA256SUMS.txt and installed with the ``install.sh`` it
  carries; then the new hamrlog opens in place of the old one.
* Installed with the Windows installer: the setup program is downloaded,
  checked and started (progress bar only); hamrlog closes so its files can
  be replaced, and the installer opens it again when it finishes.
* Anything else (pip, uv, a distribution package, the source): it only says
  that there is a new version, since it does not know how it was installed.

The network is asked at most once a day, with a short timeout; what it said
is kept in ``updates.json`` in the data folder, so answering "no" asks again
on the next start without going online. ``HAMRLOG_NO_UPDATE_CHECK`` or the
setting in F9 turns it all off.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import __version__, appconfig
from .i18n import _
from .paths import data_dir

RELEASES_URL_ENV = "HAMRLOG_RELEASES_URL"
DEFAULT_RELEASES_URL = "https://github.com/manuel-alcocer/hamrlog/releases"
#: Set (to anything) to skip the check, for scripts and for the relaunch.
DISABLE_ENV = "HAMRLOG_NO_UPDATE_CHECK"

CHECK_EVERY = dt.timedelta(days=1)
TIMEOUT_S = 3.0
STATE_FILE = "updates.json"

LINUX_ASSET = "hamrlog-{version}-linux-x86_64.tar.gz"
WINDOWS_ASSET = "hamrlog-{version}-setup.exe"
_VERSION_IN_SUMS = re.compile(r"hamrlog-(\d+(?:\.\d+)*)-setup\.exe")

YES_WORDS = frozenset({"", "y", "yes", "s", "sí", "si"})
SKIP_WORDS = frozenset({"o", "omit", "omitir", "skip"})


@dataclass(frozen=True, slots=True)
class Release:
    """The latest release, as its SHA256SUMS.txt describes it."""

    version: str
    sums: dict[str, str]


@dataclass(frozen=True, slots=True)
class Installation:
    """How this hamrlog was installed, which says how to upgrade it.

    Attributes:
        kind: "linux" (the Linux installer), "windows" (the setup program)
            or "other".
        location: The install prefix on Linux, the program folder on Windows.
    """

    kind: str
    location: Path | None = None


def releases_url() -> str:
    return os.environ.get(RELEASES_URL_ENV, DEFAULT_RELEASES_URL).rstrip("/")


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def is_newer(candidate: str, current: str) -> bool:
    return version_key(candidate) > version_key(current)


def parse_sums(text: str) -> dict[str, str]:
    """``sha256sum`` output as {file name: digest}."""
    sums = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def latest_release(timeout: float = TIMEOUT_S) -> Release | None:
    """The latest published release; None when it cannot be reached."""
    url = f"{releases_url()}/latest/download/SHA256SUMS.txt"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            text = response.read().decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    found = _VERSION_IN_SUMS.search(text)
    if found is None:
        return None
    return Release(found.group(1), parse_sums(text))


def installation() -> Installation:
    """Which installer put the running hamrlog where it is."""
    if not getattr(sys, "frozen", False):
        return Installation("other")
    executable = Path(sys.executable)
    if sys.platform == "win32":
        if (executable.parent / "unins000.exe").is_file():
            return Installation("windows", executable.parent)
    elif sys.platform.startswith("linux"):
        prefix = executable.parent.parent
        if (prefix / "lib" / "hamrlog" / "version").is_file():
            return Installation("linux", prefix)
    return Installation("other")


# --------------------------------------------------------------------------- #
# What the last check found
# --------------------------------------------------------------------------- #

def _state_path() -> Path:
    return data_dir() / STATE_FILE


def load_state() -> dict:
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(state: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def known_release(now: dt.datetime, fetch: Callable[[], Release | None]) -> Release | None:
    """The latest release, from the network once a day and from the state
    file in between."""
    state = load_state()
    try:
        checked = dt.datetime.fromisoformat(state.get("checked", ""))
    except (TypeError, ValueError):
        checked = None
    if checked is not None and now - checked < CHECK_EVERY and state.get("version"):
        return Release(state["version"], state.get("sums") or {})

    release = fetch()
    if release is not None:
        state.update(checked=now.isoformat(), version=release.version, sums=release.sums)
        save_state(state)
    return release


# --------------------------------------------------------------------------- #
# Download and install
# --------------------------------------------------------------------------- #

class UpdateError(Exception):
    """The new version could not be downloaded or installed."""


def download(release: Release, name: str, folder: Path) -> Path:
    """Fetch a release file into ``folder`` and check its SHA-256."""
    expected = release.sums.get(name)
    if not expected:
        raise UpdateError(_("The release has no {name}.").format(name=name))
    target = folder / name
    url = f"{releases_url()}/download/v{release.version}/{name}"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
            target.write_bytes(response.read())
    except (OSError, ValueError) as error:
        raise UpdateError(_("Could not download {name}: {error}").format(
            name=name, error=error)) from None
    if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
        target.unlink(missing_ok=True)
        raise UpdateError(_("{name} does not match its SHA-256 sum.").format(name=name))
    return target


def install_linux(release: Release, prefix: Path) -> Path:
    """Install the release over the one in ``prefix``; returns the new binary."""
    binary = prefix / "bin" / "hamrlog"
    if not os.access(binary.parent, os.W_OK):
        raise UpdateError(_(
            "No permission to write to {folder}. Upgrade with: curl -fsSL "
            "{url}/latest/download/hamrlog-install.sh | sudo bash -s -- --system --upgrade"
        ).format(folder=binary.parent, url=releases_url()))
    with tempfile.TemporaryDirectory(prefix="hamrlog-update-") as folder:
        tarball = download(release, LINUX_ASSET.format(version=release.version), Path(folder))
        with tarfile.open(tarball) as archive:
            if hasattr(tarfile, "data_filter"):
                archive.extractall(folder, filter="data")
            else:  # pragma: no cover - Python without the extraction filters
                archive.extractall(folder)  # noqa: S202 - checked against SHA256SUMS
        bundle = Path(folder) / tarball.name.removesuffix(".tar.gz")
        # Its own messages (where to find hamrlog, how to start it) are for a
        # first install; here they only show when something goes wrong.
        result = subprocess.run(
            ["bash", str(bundle / "install.sh"), "--prefix", str(prefix)],
            check=False, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise UpdateError(
                (result.stdout + result.stderr).strip() + "\n"
                + _("The installer failed; hamrlog was not upgraded.")
            )
    return binary


def setup_arguments(app_dir: Path) -> list[str]:
    """Silent setup in the same mode as the install it replaces, opening
    hamrlog again at the end."""
    local = os.environ.get("LOCALAPPDATA")
    per_user = bool(local) and Path(local).resolve() in app_dir.resolve().parents
    return [
        "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH",
        "/CURRENTUSER" if per_user else "/ALLUSERS",
    ]


def start_windows_setup(release: Release, app_dir: Path) -> None:
    """Download the setup program and start it on its own: hamrlog has to
    exit for its executable to be replaced."""
    folder = Path(tempfile.mkdtemp(prefix="hamrlog-update-"))
    setup = download(release, WINDOWS_ASSET.format(version=release.version), folder)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen([str(setup), *setup_arguments(app_dir)], creationflags=flags,  # noqa: S603
                     close_fds=True)


# --------------------------------------------------------------------------- #
# The question at start
# --------------------------------------------------------------------------- #

def should_check() -> bool:
    if os.environ.get(DISABLE_ENV):
        return False
    if not appconfig.load().check_updates:
        return False
    # Nobody to ask: a script, a pipe, a service.
    return sys.stdin.isatty() and sys.stdout.isatty()


def offer_update(
    argv: list[str],
    *,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    fetch: Callable[[], Release | None] = latest_release,
    now: dt.datetime | None = None,
    found: Installation | None = None,
) -> int | None:
    """Ask about a newer release and install it when told to.

    Returns:
        None to go on opening this hamrlog, or the exit code to end with:
        the new hamrlog already ran (Linux) or the installer took over
        (Windows).
    """
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    release = known_release(now, fetch)
    if release is None or not is_newer(release.version, __version__):
        return None
    state = load_state()
    if state.get("skipped") == release.version:
        return None

    found = found or installation()
    if found.kind == "other":
        say(_("hamrlog {new} is available (you have {current}): {url}").format(
            new=release.version, current=__version__, url=f"{releases_url()}/latest"))
        say(_("Upgrade it the same way you installed it."))
        return None

    try:
        answer = ask(_(
            "hamrlog {new} is available (you have {current}). "
            "Download and install it now? [Y/n/o = omit this version] "
        ).format(new=release.version, current=__version__)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        say("")
        return None
    if answer in SKIP_WORDS:
        state["skipped"] = release.version
        save_state(state)
        return None
    if answer not in YES_WORDS:
        return None

    try:
        if found.kind == "linux":
            say(_("Downloading hamrlog {new}...").format(new=release.version))
            binary = install_linux(release, found.location)
            say(_("hamrlog upgraded to {new}.").format(new=release.version))
            # The new hamrlog, with the same arguments; it need not ask again.
            env = {**os.environ, DISABLE_ENV: "1"}
            return subprocess.call([str(binary), *argv], env=env)
        say(_("Downloading hamrlog {new}...").format(new=release.version))
        start_windows_setup(release, found.location)
        say(_("The installer is running; hamrlog will open again when it finishes."))
        return 0
    except UpdateError as error:
        say(str(error))
        say(_("Opening hamrlog {current}.").format(current=__version__))
        return None
