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
| F7 | **Packet** | *hamrpkt*: AX.25 terminal over AGWPE or Telnet (LinBPQ, Direwolf) |

Everything runs in a console (Textual) with the HAMRLOG look, in English or Spanish.

```
╭─ Applications ──────────────────────────────────────────╮╭─ Rig ─────────────────────────────╮
│ Key  Application   What it does                         ││ Icom IC-705                       │
│ F2   IC-705 View   Rig control: frequency, band, mode…  ││ Link: lan · connected             │
│ F3   CW            Decoder, skimmer and keyer; channels ││   7.030.00 MHz   40m   CW         │
│ F4   D-STAR        coming soon · Terminal mode, gateway ││   S6     ▮▮▮▮▮▮··············     │
│ F5   Antenna       SWR and noise sweeps, coil; phone    ││   Receive only (--tx to transmit) │
│ F6   Logbook       hamrlog: contacts, ADIF, repeaters   │╰───────────────────────────────────╯
│ F7   Packet        hamrpkt: AX.25 over AGWPE or Telnet  │╭─ Station ─────────────────────────╮
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

```sh
uv sync                                # add --extra postgres for PostgreSQL
uv run ic705suite --init-config        # writes ~/.config/ic705suite/config.toml (edit it)
uv run ic705suite --update-scp         # MASTER.SCP for the CW skimmer
uv run ic705suite                      # dashboard, core in this process
uv run ic705suite --sim                # try it without a radio
uv run ic705suite cw                   # open one application directly (view cw swr log packet)
uv run ic705suite --lang es            # Spanish (or [ui] language / IC705SUITE_LANG)
uv run ic705suite --tx                 # enable transmission (check your SWR first!)
```

On the first run the settings of **cwic705** (`~/.config/cwic705/config.toml` and its
MASTER.SCP) are imported; the antenna history of **swr-remote**
(`~/.local/share/swr-remote/history.json`) goes into the database the first time the Antenna
application needs it. The old files are left untouched.

### Core on a Raspberry Pi, client here

```sh
uv run ic705suite --serve              # on the Pi, next to the radio ([server] listen/token)
uv run ic705suite --connect rpi.local  # on the desktop ([client] token or --token)
```

The antenna page is served by the core: `http://<core address>:8705` on the phone.

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
port = 8705
max_power = 30                # percent, for SWR sweeps
```

`ic705suite --init-config` writes the full template with every section (CW decoder, skimmer,
channels, macros, server, client, UI).

## Applications

**View (F2).** Frequency in large digits with VFO, mode, filter and RIT; meters (S, Po, SWR,
ALC, COMP, Vd, Id); the band scope as a spectrum over a colour waterfall (two waterfall rows
per character cell; click to tune, `[` `]` contrast); and every front-panel control the rig
offers, grouped as Receive (AF, RF, SQL, P.AMP, ATT, AGC, filter and width), DSP (NB, NR,
notches, Twin PBT, filter shape), Transmit (power, mic, compressor, VOX, monitor), CW (speed,
pitch, break-in), VFO (split, RIT, ΔTX, tuning step, data mode, lock) and Scope (mode, span,
reference, speed, hold, VBW).

Keys: PgUp/PgDn tune by the step, F2 step, F3 mode, F4/F5 band, F6 filter; Tab/↑↓ select a
control, ←/→ (Shift ×10) turn it, Enter toggles, Ctrl+←/→ jump between groups; `/` opens the
command line (`/f 7.030`, `/b 40`, `/mode USB`, `/set agc slow`, `/set rit_offset -120`).
The scope streams only while a view is open. In receive-only mode VOX and break-in cannot be
switched on (they would let the radio transmit by itself) and the tuner's TUNE is refused.

Command codes come from Icom's IC-705 CI-V reference guide, cross-checked with wfview and
hamlib (`rig/icom/controls.py`).

**CW (F3).** As cwic705: F10 skimmer, F11 channels, `/help` for every command. While it is
open the radio is kept in CW. Leaving it returns to the dashboard; the channels stay in the
core and leaving the suite offers to archive them.

**Antenna (F5).** Choose a band; **N** noise sweep (receive only), **S** SWR sweep (needs
`--tx`; RTTY carrier at `max_power`, 2 s PTT watchdog, settings restored afterwards), **F**
records the coil position. The verdict says whether to raise or lower the coil and, with the
history, roughly how much.

**Logbook (F6) and Packet (F7).** hamrlog and hamrpkt as they were, with their own settings
(F9). They do not need the radio, so they open even when it is unreachable.

## Development

```sh
uv run pytest                          # ~570 tests: simulator, fake Icom server, Textual pilot
IC705SUITE_TEST_POSTGRES=postgresql+psycopg://user@host/db uv run pytest tests/test_postgres.py tests/log
python -m ic705suite.apps.cw.bench     # CW decoder benchmark (character error rate)
```

Code, comments and base strings are in English; translations are gettext `.po` files read
directly (`src/ic705suite/locales/es/`; hamrlog and hamrpkt keep theirs). A test fails when a
marked string has no Spanish translation.

Layout:

```
src/ic705suite/
  cli.py launcher.py      command line; dashboard and applications in turn
  config.py db.py paths.py i18n.py locales/
  rig/                    base.py (Rig, RigModel), bands.py, icom/ (civ, rig, lan, usb, sim, models)
  station/                station.py, core.py (events), server.py (WebSocket), links.py
  dashboard/  ui/         first screen; shared styles, dialogs, status line
  apps/view/              rig view
  apps/cw/                decoder, bayes, skimmer, channels, interpret, tui...
  apps/swr/               service (sweeps, history), analysis, web (phone page), app (TUI)
  apps/log/               hamrlog          apps/packet/   hamrpkt
```

## Licence

GPL-3.0-or-later (see `LICENSE`). It brings together cwic705, hamrlog and hamrpkt (MIT) and,
soon, QDStar (GPL-3.0), all by the same author.
