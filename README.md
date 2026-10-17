# ic705suite

Terminal station suite for the **Icom IC-705**, built so more rigs can follow. One core talks
to the radio; a dashboard opens the applications, which all share that one connection:

| Key | Application | What it does |
|---|---|---|
| F2 | **IC-705 View** | the whole front panel (about 50 controls), meters, and a band scope with waterfall: our own wfview for the console |
| F3 | **CW** | Bayesian CW decoder, multi-signal skimmer, keyer, channels with plain-language translation (formerly *cwic705*) |
| F4 | **D-STAR** | reflectors in Terminal Mode, the overs heard (name, place, message, distance) and their history (formerly *QDStar*; D-PRS and the USB gateway to come) |
| F5 | **Antenna** | SWR and noise sweeps, which way to move the coil, per-band history; also a page for the phone (formerly *swr-remote*) |
| F6 | **Logbook** | *hamrlog*: contacts, ADIF, repeaters, address book |
| F7 | **Packet** | BayCom-style terminal: 8 AX.25 channels and the monitor, over a Direwolf TNC (here or on another machine) |
| F8 | **FT8** | the band's activity decoded every 15 s with WSJT-X's jt9, CQ and answers with WSJT-X's automatic sequence |

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
│ F4   D-STAR        Reflectors in Terminal Mode, overs   ││   S6     ▮▮▮▮▮▮··············     │
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
The **loudspeaker** button there plays the radio's receive audio on this computer's speakers,
from a local core or from the server (the core sends it only to the clients that listen),
with its volume beside it (`[ui] volume`, 0 to 100, kept when it is moved). It plays through
PortAudio (the `sounddevice` package): its wheels bring it on Windows and macOS, on Linux it
is the system's (`portaudio` on Arch, `libportaudio2` on Debian and Ubuntu).
Beside it, the **microphone** button talks through the radio while it is held: this
computer's microphone keys the radio in SSB, AM or FM (not in a data mode), with
transmission on in the core (`[rig] tx_enabled`, as for FT8). One client talks at a time;
the radio's DATA OFF MOD input is set to WLAN only while keyed, and put back on release
(the radio's own microphone works again). It is released when the button is let go, the
client leaves or its audio stops for a second, and after three minutes in any case; FT8 and
packet do not key meanwhile. Set the level with the radio's LAN MOD level and its ALC meter.

**Phone** (F10, graphical client only; in the terminal it opens the rig view) gathers what
a phone operator needs: the digits, the meters and the scope; buttons for the bands (each at
its phone segment, or where it was left in the session), LSB, USB, AM and FM, and FIL1-3;
the controls phone needs (AF, SQL, RF, P.AMP, ATT, AGC, NB, NR, the filter, PWR, MIC, COMP,
VOX, MON); a big **PTT** with the computer's microphone (as the corner button) or the
radio's own (the PTT only keys it; DATA OFF MOD is set to MIC while keyed if it was not),
worked with the space bar held, the space bar pressed to talk and again to stop, or the
mouse only; the **memories**, the suite's (a table in its database: name, frequency, mode,
filter, and for FM repeaters the duplex, offset and tone; the current frequency saved as
one; hamrlog's repeaters, FM and D-STAR up to 70 cm, imported) and the radio's (a group of
the IC-705's 100 x 100 channels read, one written from the current frequency or a suite's
memory, one cleared), a double click tuning to any of them; and the quick log with today's
contacts. The radio's memory layout is the one wfview reads (CI-V 1A 00).

**D-STAR** (F4, terminal and window alike) follows the radio in DV through the core, so
it goes on with no client open when the core runs on a server: every 700 ms the last DV
call signs, message and status received are read over CI-V, every 5 s the mode and MY /
UR / R1 / R2. Each over goes to the history (table `dstar_history`) with the operator's
name and place from radioid.net (cached), the message when it comes, and the D-PRS
position read after it (distance and direction from `[station] locator`); the same header
again within 8 s is the same over resumed, and replies of our own gateway are no
conversation. In Terminal Mode with the radio's internal gateway (INT, R1 `<call> Z`) the
reflector is changed by writing the TO (UR, R1 / R2 kept, read back): `[` `]` choose it, `C`
writes it, then a short PTT registers on the reflector; `[dstar] server` is the G3 server
set on the radio (it cannot be read over CI-V), and a reflector reached through another one
is warned about. `N` writes the MY note, `M` the TX message, `H` hides our own overs and
Enter (a click in the window) shows an over of the history. The reflector list is the one
shipped (`apps/dstar/default_reflectors.json`) and the operator's own (table
`dstar_reflectors`). This is QDStar, the same author's D-STAR controller, brought into the
suite: its D-PRS views, the reflectors' status and its own gateway over USB (EXT) follow.

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

**Packet (F7).** A BayCom-style terminal on a Direwolf TNC, in one of two ways
(`[packet] tnc`, also asked by `--configure`):

- `tnc = "agwpe"`: a Direwolf already running, on this machine or another one (such as a
  Raspberry Pi with a Digirig and another radio), reached over its AGWPE port (`host`,
  `tcp_port`). Until `[packet]` is written, hamrpkt's own settings (its node and callsign)
  are used.
- `tnc = "direwolf"`: the core starts its own Direwolf here, writes its configuration from
  `[packet]` (`audio`: the sound card, `ptt`, `txdelay`, `mycall`; AGWPE on `tcp_port`), keeps
  what it says and starts it again if it stops. `--configure` finds a Digirig plugged in (its
  CM108 sound card and the CP210x serial port next to it, PTT on RTS) and offers it. Direwolf
  must be installed (`apt install direwolf`; on Windows, its installer): it is a program of
  its own (GPLv2), which the suite only starts and talks to. `/tnc` shows its configuration
  and its last words, `/tnc restart` starts it again.
- `tnc = "ic705"`: the IC-705 over WiFi is the radio, with Direwolf started here (Linux). The
  core passes the radio's audio to Direwolf (UDP), records what Direwolf transmits from the
  `snd-aloop` virtual sound card and sends it to the radio, and answers Direwolf's PTT as a
  rigctld, keying the radio over CI-V. Load the virtual card once:
  `sudo modprobe snd-aloop; echo snd-aloop | sudo tee /etc/modules-load.d/snd-aloop.conf`.
  The radio transmits packet in FM-D with its DATA1 MOD input on WLAN: `/qrg 144.800` in the
  packet screen tunes it so (the core sets the input the first time it keys anyway, but leaves
  the mode to the operator: without a data mode, or with `tx_enabled` off, it does not key and
  says why). A higher `[rig] sample_rate` (16000 or 48000) helps Direwolf decode.

The monitor (channel 0) stays on top; below it, the chosen channel: **F1-F8** choose one of the 8 channels, one
session each. What is typed goes to that channel's station; `/c CALL [v DIGI …]` calls from
this channel, `/d` hangs up, `/ui DEST text` sends an unconnected frame, `/help` lists the
rest. A station calling us takes the first free channel. **F9** hides the monitor, **F10**
lists the stations heard (Enter calls one). The bar marks the channels with unread text. The
sessions live in the core: leaving the screen, or closing a remote client, does not drop
them. With `agwpe` or `direwolf` it needs no radio of the suite's own.

**FT8 (F8).** The core cuts the radio's audio into the 15-second cycles of the UTC clock
and decodes each one with WSJT-X's `jt9` (a program of its own, GPL, which the suite only
runs: `apt install wsjtx`; on Windows, WSJT-X's installer). The list is WSJT-X's Band
Activity: each cycle's time, dB, DT, audio frequency and message, a CQ in green and what is
sent to you in red. **←→** go through the bands to the FT8 frequencies (USB-D), **C** shows
only the CQs (and yours), **S** stops and starts the decoding; opening the screen starts it
and leaving it stops it. As WSJT-X, a quick pass comes first (on a Raspberry Pi 4, depth 1
in about 3-6 s after the cycle) and, when the machine has time before the next cycle, a
deeper one adds the weaker signals; the depths follow what each one takes there
(`[ft8] depth` fixes one). It needs the clock on time (NTP).

Transmitting, as WSJT-X does: **Enter** (or a double click) answers the chosen station,
**Q** calls CQ, **H** halts at once, **F** puts the transmit frequency on the chosen
line's (`[ft8] tx_freq`, 1500 Hz by default). The QSO then runs by itself with the six
standard messages (grid, report, R-report, RR73, 73), in our turn: the cycles of the other
parity from the station answered. In a QSO a narrow pass near the other station at second
13 of its turn (under a second, even on a Raspberry Pi) decides the next message in time;
the same message goes again while the other station repeats theirs, and after six tries
without an answer, or twenty CQs, it stops. The messages' tones come from WSJT-X's
`ft8code` (installed with jt9); the suite makes the audio and sends it to the radio over its
own link, keying it by CI-V, only with `tx_enabled` and the radio in a data mode (its DATA1
MOD input set to WLAN). What we send shows in yellow. `[station] locator` is the grid sent
(asked by `--configure`).

With the core on a server (the Raspberry Pi), FT8 goes on there when the clients close:
decoding, a QSO under way, CQ, the super-automatic mode; a client opened later finds it as
it was, with the cycles it decoded meanwhile. With no client connected for `[ft8]
unattended` minutes (10 by default, 0 without a limit) it stops transmitting and goes on
decoding; the **Alone** box changes that until the core restarts. The messages between
other stations show in a quieter grey; **Only the CQs** (C) hides them.

A right click on a station (its line in the band activity or the QSO, its dot on the map,
its call on the waterfall) offers: ignore it (I) or its country (Shift+I), so the automatic
modes leave it, and **CQ auto** (w), the stations wanted. With **Auto CQ** on (W), the
wanted stations heard in the last three minutes calling CQ (or CQ DX, or calling us), with
nothing under way, are called in turn, three times each (the one called longest ago first;
one that answers another station meanwhile is left at once for the next), and after every
two of them three CQs of ours give others their chance to call us; it goes before the
super-automatic mode, and a station worked on a band is not called there again. The lists live in the
suite's database (table `ft8_lists`; the ignored ones of older versions are taken in from
FT8's settings file) and the **Lists** tab, beside the waterfall and the map, shows them in a
table: filtered by list, the name and the note edited in place, rows added and removed.

In the graphical client the **waterfall** sits above the lists, as WSJT-X's Wide Graph:
0-3200 Hz, a row every quarter of a second, a dashed line and the time at each cycle, our
transmit frequency in red and the station we work in green. The call of each station
decoded is written on its trace: a click on it chooses that station in the list, a double
click answers it, and a click anywhere else puts our transmit frequency there. Gain and Zero
set its colours. The core computes it only while a screen shows it.

Each station heard is placed: its country (DXCC, from the `cty.dat` WSJT-X installs), and
the distance and bearing from `[station] locator` (from its grid, or from the one it sent
last, else from its country's middle: marked ~). With the shared logbook the list also says
what it would be for the log, as GridTracker and JTAlert do: **NEW DXCC** (magenta), a
new band for that entity, **NEW GRID**, **B4** (worked before on this band, dim) or ✓
(the entity confirmed in LoTW). Beside the waterfall, the **Map** tab draws them on the
world (Natural Earth's land, public domain) with the grey line, Maidenhead's fields and
squares, the squares worked (orange) and confirmed (green), our station, and the great
circle to the station we call and from each station calling us, with arrows running the way
the call goes. A key in the bottom right corner says what each mark is, and its last line
what is under the mouse (a station, or the locator, distance and bearing there). The wheel
zooms, a drag moves it, a click on a station chooses it and a double click answers it.

**I** ignores the chosen station, and **Shift+I** its whole country (again: no longer); in
the graphical client a right click on a line does the same and lists what is ignored. Their
lines are marked IGN. The lists are kept by the core.

Beside the band's activity (under it in the terminal), the **QSO** area shows the
conversation alone: what we send, what is sent to us and what the station we work says.
Under them, what we transmit with: the audio frequency (**[ ]** move it 25 Hz, or type it),
the audio level (**- +**, `[ft8] level`, 50 % by default), the radio's power (**, .** a watt
down or up) and its **ALC**, live while sending and the last message's peak afterwards. As
Icom asks for data modes, the ALC goes within its zone (1.0 here is its end): green up to
0.5, yellow up to 1.0, red past it, and the suite says to lower the level. With the ALC
still at 0 the radio may give less than the power set: the suite says so, and the level can
go up. The watts come from the power; the audio only has to be enough. **T** (the Tone button) sends a steady
tone on the transmit frequency and level for 10 seconds, as WSJT-X's Tune, to set the level
with the ALC moving and nobody to answer (T or H stops it sooner; never over 15 s).

Each QSO completed (reports both ways) is added to `ft8/ft8_log.adi` in the core's data
folder (`~/.local/share/ic705suite` on Linux), as WSJT-X keeps `wsjtx_log.adi`: call, grid,
mode, both reports, the start and end times, band, frequency (dial plus audio), our call,
grid and power. That file is the one to upload to LoTW, eQSL or QRZ. The FT8 screen also
puts the QSO into the logbook (hamrlog) of the machine it runs on, once.

**The BBS.** With `[packet] bbs = true` the core also runs a BBS on its own callsign
(`bbs_call`, by default your callsign `-1`): personal messages and bulletins, with the
commands of the BBSes on the air: `L` (new since your last `L`), `LM`, `LB`, `LL n`,
`L< CALL`, `L> CALL`, `R n`, `RM`, `SP CALL[@BBS]`, `SB AREA[@DIST]` (the text ends with
`/EX` or Ctrl+Z), `K n`, `KM`, `N name`, `I`, `H`, `B`. It keeps them in the suite's database
(tables `bbs_messages`, `bbs_users`). Up to 10 stations at once; each is welcomed with the
messages waiting for them. The terminal (`[packet] client`, `mycall`) is optional too: the
BBS alone, the terminal alone, or both. From the terminal, `/c` to the BBS's callsign enters
it with no radio between (as its sysop); **F12** is the sysop's page: the messages (Enter
reads, K kills), U the users, and who is connected. A line starting with `/` that is not a
command of the terminal goes to the station as typed while connected (a BBS's `/EX`).

**Packet around the clock on another machine.** A Raspberry Pi with a Digirig can keep the
BBS (and the terminal's sessions) on the air while the computer is off: there the core runs
with no radio (`[rig] connection = "none"`) as a server (`ic705suite --serve`, e.g. a
systemd service), with `[packet] tnc = "direwolf"`, `bbs = true` and `[server] token`. On
the computer, `[packet] core = "raspberrypi.local:7373"` and `core_token` (that token) make
F7 open packet there, terminal and graphical client alike, while the rest of the suite stays
with the radio on the computer's own core (whose packet then stays off). On a Raspberry Pi
OS of 32 bits, numpy and the like come best from its packages
(`apt install python3-numpy python3-sqlalchemy python3-websockets python3-soundfile`) and
the suite in a `python3 -m venv --system-site-packages` environment. That core can have the
radio too (`connection = "lan"`): a Raspberry Pi 4 runs it all with room to spare. Then, on
the computer, `[rig] connection = "server"` with `[client] connect` and `token` makes
`ic705suite` (and `--gui`) always use that core, with no `--connect` to type; `--sim` still
opens the simulator here. The status line then shows both machines by name: this one's
CPU, RAM and CPU temperature, and the core's (temperatures from Linux's sensors). If the
core goes away (restarted, the network down) the radio shows as disconnected, the screens
keep working (Esc leaves), and the client connects again by itself when the core is back.

**One logbook for every client.** With a `[logbook]` section on the core's machine (a
PostgreSQL database there: `apt install postgresql python3-psycopg`), the core opens that
logbook and tells each client how to reach it when it connects: the logbook (F6, or
`ic705suite log` on its own) is then the same from any client, and FT8 logs each QSO from
the core, once. `ic705suite --copy-logbook FROM TO` moves an existing logbook (the SQLite
file of a computer, say) into it, ids and all, into an empty database only. The clients
need the `postgres` extra (psycopg); nothing to set on them.

**The log by band and mode.** `/filter 20m FT8` in the logbook's entry line (`/filtro`)
shows only the QSOs of that band and mode, or of either alone (`/filter cw`, `/filter 2m`);
`/filter` alone shows them all again. The log's title says what is filtered. In the
graphical client two boxes over the log do the same with the mouse.

**The logbook in the KDE Plasma panel.** `packaging/plasma/install.sh` installs an applet
(Plasma 6) showing today's QSOs on its icon and, open, the logbook's counters, the latest
QSOs (a click looks the call up on QRZ.com) and a band and a mode to narrow them by. It
reads the logbook the suite's clients open (the core's, when shared) through
`ic705suite --logbook-json [BAND] [MODE]`, which prints it as JSON; the applet's settings
say how ic705suite is run and how often to refresh.

**LoTW from the core.** With the shared logbook and a `[lotw]` section (TQSL's station
location; `apt install trustedqsl xvfb` and the certificate in `~/.tqsl` of the core's
machine) the core signs and uploads every few minutes the logbook's QSOs not sent yet,
whichever client logged them: HF only unless `[lotw] bands` says otherwise (not those
through a repeater, which LoTW does not count, nor other operators'), and every few hours brings back the confirmations with the LoTW
website's account (`username`, `password`). Each QSO keeps ADIF's LOTW_QSL_SENT and
LOTW_QSL_RCVD with their dates.

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
suite brings together cwic705, hamrlog, hamrpkt and QDStar, all by the same author.
