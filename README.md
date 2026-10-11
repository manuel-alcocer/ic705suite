# ic705suite

Terminal station suite for the **Icom IC-705**, built so more rigs can follow. One core talks
to the radio; a dashboard opens the applications, which all share that one connection:

| Key | Application | What it does |
|---|---|---|
| F2 | **IC-705 View** | the whole front panel (about 50 controls), meters, and a band scope with waterfall: our own wfview for the console |
| F3 | **CW** | Bayesian CW decoder, multi-signal skimmer, keyer, channels with plain-language translation (formerly *cwic705*) |
| F4 | **D-STAR** | terminal mode, built-in gateway, reflectors, D-PRS: *QDStar* in the terminal — **coming soon** |
| F5 | **Antenna** | SWR and noise sweeps, which way to move the coil, per-band history; also a page for the phone (formerly *swr-remote*) |
| F6 | **Logbook** | *hamrlog*: contacts, ADIF, repeaters, address book |
| F7 | **Packet** | BayCom-style terminal: 8 AX.25 channels and the monitor, over a Direwolf TNC (here or on another machine) |

Everything runs in a console (Textual) with the HAMRLOG look, in English or Spanish, and only
with the keyboard — no mouse: Tab, function keys and `/commands` (and the cursor keys on the
rig view's front panel). The dashboard opens each application with its function key, or
chosen with ↑↓ and opened with Enter or Space. Beside the applications it shows the rig, a
small band scope and the basic settings (frequency, band, mode, power, AF, RF, SQL, AGC,
preamp, attenuator, filter): Tab goes to them and they work as the rig view's front panel
(arrows move, Enter edits, Esc goes back); PgUp/PgDn tune from anywhere. Under the
applications, a quick logbook entry: a line in hamrlog's fast-entry format (its profile's field
order, e.g. `EA7XYZ, Pepe, 59, 57, Sevilla`) goes into hamrlog's log with the radio's
frequency, band, mode and power, and today's contacts are listed below it.

It also works on the plain Linux console (no desktop), whose fonts have a few hundred
characters and 16 colours. The suite asks the kernel which characters the loaded font has and
draws with those: the frequency in a block font, the waterfall
shaded with ░ ▒ ▓ (whichever the font has) between the console colours, the spectrum with half
or full blocks, square borders. It is chosen by itself when `TERM=linux`; `[ui] glyphs` or
`--glyphs console|unicode` force it.

```
╭─ Applications ──────────────────────────────────────────╮╭─ Rig ─────────────────────────────╮
│ Key  Application   What it does                         ││ Icom IC-705                       │
│ F2   IC-705 View   Rig control: frequency, band, mode…  ││ Link: lan · connected             │
│ F3   CW            Decoder, skimmer and keyer; channels ││   7.030.00 MHz   40m   CW         │
│ F4   D-STAR        coming soon · Terminal mode, gateway ││   S6     ▮▮▮▮▮▮··············     │
│ F5   Antenna       SWR and noise sweeps, coil; phone    ││   Receive only (--tx to transmit) │
│ F6   Logbook       hamrlog: contacts, ADIF, repeaters   │╰───────────────────────────────────╯
│ F7   Packet        BayCom-style, 8 channels, Direwolf   │╭─ Station ─────────────────────────╮
╰─────────────────────────────────────────────────────────╯│ Database: postgresql+psycopg://…  │
```

## Architecture

```
                   ┌──────────────────── core (station/core.py) ─────────────────────┐
 IC-705 ◄─ rig ───►│ Station: one connection, reconnect, polling, audio, rig commands│
  (any model)      │   ├─ CW engine (apps/cw/session.py)   decoder · skimmer · keyer │
                   │   └─ Antenna service (apps/swr)       sweeps · phone page :8705 │
                   └────────────────┬────────────────────────────────────────────────┘
                                    │ events (JSON) ▲ commands {line, app} · calls
                    LocalLink (in-process) or RemoteLink (WebSocket, --serve/--connect)
                                    │
             Dashboard ─► View · CW · Antenna      Logbook · Packet (local, no radio needed)
```

* **`rig/`** – what a radio is to the suite. `RigModel` describes a model (connections,
  features such as `dstar`, `audio`, `cw_keyer`, `swr_meter`, coverage, modes); `Rig` is the
  interface every driver implements; `IcomRig` speaks CI-V over any transport (network, USB,
  simulator). Applications check `model.features` before offering something, so a new radio
  is a new entry in `rig/icom/models.py` (or a new driver family) and nothing else.
* **`station/`** – the core. The `Station` owns the only connection to the radio (which
  accepts a single remote client), so the CW decoder, an antenna sweep and the rig view can run
  at the same time. Receive-only unless started with `--tx`: no command able to key the
  transmitter leaves the computer (the rig refuses PTT and the keyer).
* **`apps/`** – the applications; `dashboard/` – the first screen; `ui/` – shared look.
* **Database** – one database for the suite, any SQLAlchemy URL. **PostgreSQL** is the
  recommended engine; **SQLite** is the default so it works out of the box. Each application
  keeps its tables there with its own prefix (`cw_*`, `swr_history`). hamrlog keeps its own
  logbook database (it already accepts a PostgreSQL URL in its settings, F9).

## Install and run

On Linux (x86_64) the release carries a self-contained executable, with no Python needed:

```sh
curl -fsSL https://github.com/manuel-alcocer/ic705suite/releases/latest/download/ic705suite-install.sh | bash
```

It goes into `~/.local/bin` (`--system` for `/usr/local`, with sudo; `--prefix DIR`
elsewhere) with an entry in the desktop's menu. The same script, or the `install.sh` inside
the release's `ic705suite-X.Y.Z-linux-x86_64.tar.gz`, upgrades (`--upgrade`), shows the
installed version (`--status`) and uninstalls (`--uninstall`; `--purge` deletes the suite's
settings and data too, but never hamrlog's logbook). Downloads are checked against the
release's `SHA256SUMS.txt`.

On Windows 10/11 (x86_64), likewise, from PowerShell (no administrator rights needed):

```powershell
irm https://github.com/manuel-alcocer/ic705suite/releases/latest/download/ic705suite-install.ps1 | iex
```

It goes into `%LOCALAPPDATA%\Programs\ic705suite` (`-InstallDir DIR` elsewhere), on the
user's PATH and in the Start menu. The `install.ps1` inside the release's
`ic705suite-X.Y.Z-windows-x86_64.zip` upgrades (`-Upgrade`), shows the installed version
(`-Status`) and uninstalls (`-Uninstall`; `-Purge` deletes the suite's settings and data too,
never hamrlog's logbook). It runs in Windows Terminal or the classic console.

Both installers check what is installed, and after installing or upgrading ask for the main
settings at the terminal (`ic705suite --configure`): language, callsign, how to reach the
radio, its address, user and password, and whether it may transmit. Settings already there
are shown first and are the defaults (Enter keeps each one); a new file starts from the
template. Before uninstalling they show them (`ic705suite --show-config`). `--no-configure`
(`-NoConfigure`) skips the questions, as does running without a terminal.

From the source:

```sh
uv sync --extra gui                    # gui: the graphical client; postgres: PostgreSQL
uv run ic705suite --configure          # asks for the main settings (or --init-config: template)
uv run ic705suite --update-scp         # MASTER.SCP for the CW skimmer
uv run ic705suite                      # dashboard, core in this process
uv run ic705suite --sim                # try it without a radio
uv run ic705suite --gui                # the graphical client (Qt), same keys, and the mouse
uv run ic705suite cw                   # open one application directly (view cw swr log packet)
uv run ic705suite --lang es            # Spanish (or [ui] language / IC705SUITE_LANG)
uv run ic705suite --glyphs console     # plain Linux console (automatic with TERM=linux)
uv run ic705suite --tx                 # enable transmission (check your SWR first!)
                                       # SWR sweeps also need R (remote control) in Antenna
```

On the first run the settings of **cwic705** (`~/.config/cwic705/config.toml` and its
MASTER.SCP) are imported; the antenna history of **swr-remote**
(`~/.local/share/swr-remote/history.json`) goes into the database the first time the Antenna
application needs it. The old files are left untouched.

Where the files go:

| | Linux | Windows |
|---|---|---|
| Settings, MASTER.SCP | `~/.config/ic705suite` | `%APPDATA%\ic705suite` |
| Database, CW channels | `~/.local/share/ic705suite` | `%LOCALAPPDATA%\ic705suite` |
| Log | `~/.local/state/ic705suite` | `%LOCALAPPDATA%\ic705suite\logs` |

(The XDG variables move them on Linux; hamrlog keeps its logbook in its own folder.)

### The graphical client

`ic705suite --gui` opens the same suite in a window (Qt): the dashboard and every
application (rig view, CW, Antenna, Logbook, Packet), with the same menus, options and
messages, and **the same keys**: each page takes the bindings of its terminal screen, so they
cannot drift apart. The shared code is shared, not copied: the front panel's model, the rig
keys, the skimmer's marks, the packet terminal's client; the logbook (hamrlog) runs its own
terminal application's methods behind Qt widgets that offer the same methods
(`gui/borrow.py`). The mouse comes on top: a double click opens an application or a QSO's
card, a click picks a front-panel option, a skimmer station, a band or a row and the wheel
turns an option (double click edits it), a click on a band scope tunes there and the wheel on
the scope or on the frequency digits tunes by the step; the macros and the Antenna actions
have buttons; sweeps are drawn as a chart. It works with a local core or `--connect` to a
server, like the terminal client; the installers add a menu entry (Start menu on Windows).
Dark or light: `[ui] theme = "dark" | "light" | "auto"` (auto, the default, follows the
desktop), `--theme` for one run, `ic705suite --configure`, or the **☀ / ☾ / ◐** button at the
top right of the window (light, dark, auto: each click goes to the next), which changes it at
once and keeps it in the configuration file; the waterfalls stay dark. Beside it, **?** opens
About, with the links to the project page and the help; every status line ends with the
suite's version.

### Core on a Raspberry Pi, client here

```sh
uv run ic705suite --serve              # on the Pi, next to the radio ([server] listen/token)
uv run ic705suite --connect rpi.local  # on the desktop ([client] token or --token)
```

The antenna page is served by the core: `http://<core address>:8705` on the phone
(`[swr] host = "127.0.0.1"` keeps it to that computer). The core runs on Linux or Windows.

## Configuration

```toml
[rig]
model = "ic705"
connection = "lan"            # lan, usb (next version) or sim
host = "192.168.1.50"
username = "user"
password = "password"
tx_enabled = false

[database]
url = "postgresql+psycopg://user:password@host/ic705suite"   # default: SQLite file

[swr]
host = "0.0.0.0"              # antenna page: "127.0.0.1" for this computer only
port = 8705
max_power = 30                # percent, for SWR sweeps
```

`ic705suite --init-config` writes the full template with every section (CW decoder, skimmer,
channels, macros, server, client, UI).

## Applications

**View (F2).** Frequency in large digits with VFO, mode, filter and RIT; meters (S, Po, SWR,
ALC, COMP, Vd, Id); the band scope as a spectrum over a colour waterfall (two waterfall rows
per character cell; F7/F8 jump to the previous/next signal on it, `[` `]` contrast); and
every front-panel control the rig
offers, grouped as Receive (AF, RF, SQL, P.AMP, ATT, AGC, filter and width), DSP (NB, NR,
notches, Twin PBT, filter shape), Transmit (power, mic, compressor, VOX, monitor), CW (speed,
pitch, break-in), VFO (split, RIT, XIT, tuning step, data mode, lock) and Scope (mode, span,
reference, speed, hold, VBW).

Keys (no mouse). The front panel has two modes. **Moving**: the cursor keys go from option
to option (↑↓ in a column, ←→ between columns). **Editing**: Space or Enter on an option; the
cursor keys change it at once (→↑ up, ←↓ down, Shift ten steps) or type the value (`40`,
`-120`, `slow`, `on`); Enter confirms and goes back to moving, Esc cancels and puts the old
value back. In both modes: PgUp/PgDn tune by the step, F2 step, F3 mode, F4/F5 band, F6
filter, F7/F8 previous/next signal on the scope, `[` `]` waterfall contrast, `/` the command
line (`/f 7.030`, `/b 40`, `/mode USB`, `/set agc slow`, `/set rit_offset -120`).
The scope streams only while a view is open. In receive-only mode VOX and break-in cannot be
switched on (they would let the radio transmit by itself) and the tuner's TUNE is refused.

Command codes come from Icom's IC-705 CI-V reference guide, cross-checked with wfview and
hamlib (`rig/icom/controls.py`).

**CW (F3).** As cwic705: F10 skimmer (↑↓ or Tab choose a station, Enter or `/go N` tunes
it), F11 channels (↑↓ or Tab, Enter), ↑↓ (when not skimming) or Ctrl+P/Ctrl+N recall earlier
lines, `/help` or F1 opens a window
with every command and key (also over the skimmer). A station calling CQ, looking for a
contact, stands out in green: its row of the skimmer, its number on the waterfall's scale, a
CALLING CQ badge in the detail window and every CQ in the received text. A station running a
contest (CQ TEST, and its quick exchanges in between) stands out the same way in magenta,
with a CONTEST badge. While it is
open the radio is kept in CW. Leaving it returns to the dashboard; the channels stay in the
core and leaving the suite offers to archive them.

**Antenna (F5).** F2–F9 choose the band, ↑↓ go through the bands and the presets; **P** lists
the presets: your own targets, each a name, a band and a frequency in it ("SSB at home", 40 m,
7.150), swept on its band aiming at its frequency and keeping its own saved coil position (N, E
and D add, edit and delete them; the phone page shows them too). **H** opens the history of
the chosen band or preset: every sweep (with its curve) and saved position, newest first. Enter
shows one, with its curve and photo: **C** changes the position it was made at, **O** its note,
**F** adds or replaces a photo (a file's path in the terminal, the file dialog in the graphical
client; on the phone page the camera, also straight after a sweep), **V** opens the photo in
the image viewer, **X** removes it and **D** deletes the entry. The photos are kept in the
core's data folder (`swr/photos`). **N** noise sweep (receive
only), **S** SWR sweep
(RTTY carrier at `max_power`, 2 s PTT watchdog, settings restored afterwards). An SWR sweep
needs both `--tx` and the **remote control** switched on with **R** in this application: it is
off whenever the suite starts and only this application can switch it on (it asks first).
While it is off the phone page only watches and can stop a sweep. With `remote_password` set in
`[swr]` (also asked by `--configure`), the phone page can switch it on itself once the password
is typed there, and off at any time; five wrong passwords lock it for five minutes. The page is
plain HTTP on the local network: use a password of its own, not one you use elsewhere.
**C** records the coil position on the chosen band (with the last SWR sweep's results when it
was on that band). The verdict says whether to raise or lower the coil and, with the history,
roughly how much. **A** lists the antennas: each keeps its own history and saved settings
(where its coil was left on each band) and says how its adjustment moves the resonance (a
higher number lowering or raising the frequency, or not known yet, which the advice then
learns from the saved positions). Enter makes one the active antenna; N, E and D add, edit
and delete them. The phone page can switch the active antenna too.

**Logbook (F6).** hamrlog as it was, with its own settings (F9). It does not need the radio,
so it opens even when it is unreachable.

**Packet (F7).** A BayCom-style terminal on a Direwolf TNC, which the core reaches over its
AGWPE port (`[packet] host` and `tcp_port`, also asked by `--configure`): on this machine or
on another one, such as a Raspberry Pi with a Digirig and another radio. Until `[packet]` is
written, hamrpkt's own settings (its node and callsign) are used. The monitor (channel 0)
stays on top; below it, the chosen channel: **F1-F8** choose one of the 8 channels, one
session each. What is typed goes to that channel's station; `/c CALL [v DIGI …]` calls from
this channel, `/d` hangs up, `/ui DEST text` sends an unconnected frame, `/help` lists the
rest. A station calling us takes the first free channel. **F9** hides the monitor, **F10**
lists the stations heard (Enter calls one). The bar marks the channels with unread text. The
sessions live in the core: leaving the screen, or closing a remote client, does not drop
them. It needs no radio of the suite's own (the IC-705 will come as a TNC radio later).

## Development

```sh
uv run pytest                          # ~570 tests: simulator, fake Icom server, Textual pilot
IC705SUITE_TEST_POSTGRES=postgresql+psycopg://user@host/db uv run pytest tests/test_postgres.py tests/log
python -m ic705suite.apps.cw.bench     # CW decoder benchmark (character error rate)
```

The tests run on Linux and Windows for every push and pull request
(`.github/workflows/ci.yml`).

Releases are made by python-semantic-release (`.github/workflows/release.yml`) when the
tests pass on `main`, so commit messages follow [Conventional Commits](https://www.conventionalcommits.org/),
with the area as scope:

```
feat(cw): skimmer marks stations calling CQ          -> minor version (x.Y.z)
fix(windows): the server stops cleanly on Ctrl+C     -> patch version (x.y.Z)
feat(rig)!: ... / BREAKING CHANGE: in the body       -> major version (minor while at 0.x)
docs: ... / test: ... / ci: ... / chore: ... / refactor: ...   -> no release
```

It writes the version into `src/ic705suite/__init__.py` and `CHANGELOG.md`, commits
`chore(release): X.Y.Z`, tags `vX.Y.Z` and pushes. Then, on Linux and on Windows, the
workflow tests, builds the executable with PyInstaller (`packaging/pyinstaller/ic705suite.spec`),
checks that it starts with the simulator (`packaging/windows/smoke.py` on Windows), packs it
with its installer (`packaging/linux/install.sh`, `packaging/windows/install.ps1`), tries
installing and uninstalling it and publishes the release, its notes taken from the changelog,
every file with a signed build provenance (`gh attestation verify FILE --repo
manuel-alcocer/ic705suite`).
Run by hand (Actions > Release) it releases `main` at once, or, given a tag, builds and
publishes that tag again. What the next version would be:

```sh
uvx --from python-semantic-release==10.7.0 semantic-release --noop version --print
```

Building by hand:

```sh
uv sync --extra postgres && uv pip install pyinstaller
uv run pyinstaller --clean --noconfirm packaging/pyinstaller/ic705suite.spec
./dist/ic705suite --version            # about 47 MB: Python, Textual, numpy, libsndfile, libpq
```

Code, comments and base strings are in English; translations are gettext `.po` files read
directly (`src/ic705suite/locales/es/`; hamrlog keeps its own). A test fails when a
marked string has no Spanish translation.

Layout:

```
src/ic705suite/
  cli.py launcher.py      command line; dashboard and applications in turn
  config.py db.py paths.py i18n.py locales/
  rig/                    base.py (Rig, RigModel), bands.py, icom/ (civ, rig, lan, usb, sim, models)
  station/                station.py, core.py (events), server.py (WebSocket), links.py
  dashboard/  ui/         first screen; shared styles, dialogs, status line, panel model
  gui/                    the graphical client (Qt): pages mirroring the terminal screens
  apps/view/              rig view
  apps/cw/                decoder, bayes, skimmer, channels, interpret, tui...
  apps/swr/               service (sweeps, history), analysis, web (phone page), app (TUI)
  apps/packet/            service (the TNC in the core), client, app (TUI)
  apps/log/               hamrlog
```

## Licence

[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0),
with additional terms from the author (see `LICENSE`). It may be used, studied, changed and
shared for any noncommercial purpose (personal use, hobby, amateur radio, education...).
Selling it, copies of it or works based on it, and any other commercial use, needs the
author's written permission; whoever sells it without permission may be claimed up to the
whole of the profits obtained.

Versions published before this licence (up to commit 9760095) were GPL-3.0-or-later. The
suite brings together cwic705, hamrlog and hamrpkt and, soon, QDStar, all by the same author.
