"""Command line entry point: the dashboard by default, or one application directly."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path

from ic705suite import __version__, paths
from ic705suite.config import DEFAULT_PATH, load_config, migrate_legacy, write_template
from ic705suite.i18n import render, set_language
from ic705suite.rig import MODELS
from ic705suite.rig.base import RigError
from ic705suite.rig.transport import Transport, TransportError
from ic705suite.station.core import DEFAULT_PORT
from ic705suite.station.links import Link, LinkError, LocalLink, RemoteLink

TRACE = 5
APP_CHOICES = ("view", "cw", "swr", "log", "packet", "dstar")
OFFLINE_APPS = ("log", "packet")   # they never use the radio


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="ic705suite",
        description="Station suite for the Icom IC-705: dashboard, rig view, CW, antenna "
                    "tuning, logbook and packet, as client/server.",
        epilog=f"Settings are read from {DEFAULT_PATH} (create it with --init-config). "
               "The password can also be given in the IC705SUITE_PASSWORD environment variable.")
    p.add_argument("app", nargs="?", choices=APP_CHOICES,
                   help="open this application directly instead of the dashboard")
    conn = p.add_argument_group("rig")
    conn.add_argument("--model", choices=sorted(MODELS), help="rig model (default from the config)")
    conn.add_argument("--host", help="IP address or hostname of the radio")
    conn.add_argument("--user", help="network username (Remote Settings > Network User1)")
    conn.add_argument("--password", help="network password")
    conn.add_argument("--port", type=int, help="control port (default 50001)")
    conn.add_argument("--sample-rate", type=int, choices=(8000, 16000, 24000, 48000),
                      help="receive audio sample rate (default 8000)")
    conn.add_argument("--usb", metavar="DEVICE", help="connect over USB (next version)")
    conn.add_argument("--sim", action="store_true", help="use the built-in radio simulator")
    conn.add_argument("--tx", action="store_true",
                      help="enable transmission (default: receive only; check your antenna SWR first)")
    cw = p.add_argument_group("CW")
    cw.add_argument("--wav", metavar="FILE", type=Path,
                    help="decode a recording as if it came from the radio (no radio needed)")
    cw.add_argument("--wav-freq", metavar="MHZ",
                    help="dial frequency of the recording, if it has no .json sidecar")
    cw.add_argument("--call", help="your callsign (used in macros)")
    cw.add_argument("--wpm", type=int, help="keyer speed set at start-up")
    cw.add_argument("--record", metavar="WAV", type=Path,
                    help="save received audio to a WAV file (analyse it with ic705suite-replay)")
    cw.add_argument("--plain", action="store_true", help="CW in line mode instead of full screen")
    mode = p.add_argument_group("client/server")
    mode.add_argument("--serve", action="store_true",
                      help="run the core as a server for remote clients (e.g. on a Raspberry Pi)")
    mode.add_argument("--listen", metavar="HOST:PORT",
                      help=f"server address (default from the config, port {DEFAULT_PORT})")
    mode.add_argument("--connect", metavar="HOST[:PORT]", nargs="?", const="",
                      help="connect to an ic705suite server instead of the radio")
    mode.add_argument("--token", help="token for --serve/--connect (default from the config)")
    p.add_argument("--lang", help="interface language (en, es)")
    p.add_argument("--config", type=Path, help="configuration file")
    p.add_argument("--init-config", action="store_true", help="write a configuration template and exit")
    p.add_argument("--update-scp", action="store_true",
                   help="download MASTER.SCP (callsign database used by the CW skimmer) and exit")
    p.add_argument("--debug", action="store_true", help="debug logging (add -v for packet dumps)")
    p.add_argument("-v", "--verbose", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--version", action="version", version=f"ic705suite {__version__}")
    return p.parse_args(argv)


def setup_logging(debug: bool, trace: bool) -> Path:
    log_dir = paths.state_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ic705suite.log"
    logging.addLevelName(TRACE, "TRACE")
    logging.basicConfig(
        filename=log_file, level=TRACE if trace else logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return log_file


def apply_overrides(args: argparse.Namespace, config) -> None:
    for attr, value in (("host", args.host), ("username", args.user), ("password", args.password),
                        ("control_port", args.port), ("sample_rate", args.sample_rate),
                        ("wpm", args.wpm), ("rig_model", args.model)):
        if value is not None:
            setattr(config, attr, value)
    if args.sim:
        config.connection = "sim"
    if args.usb:
        config.connection, config.serial_device = "usb", args.usb
    if args.tx:
        config.tx_enabled = True
    if args.call:
        config.callsign = args.call.upper()
    if args.listen:
        config.listen = args.listen


def recording_transport(args: argparse.Namespace, config) -> Transport | None:
    """--wav: a quiet simulated radio on the recording's frequency; the file is the audio."""
    if not args.wav:
        return None
    from ic705suite.apps.cw.playback import load_sidecar
    from ic705suite.rig.bands import parse_frequency
    from ic705suite.rig.icom.sim import SimTransport

    timeline = load_sidecar(args.wav)
    freq = parse_frequency(args.wav_freq) if args.wav_freq else \
        (timeline[0][1] if timeline else 7_030_000)
    return SimTransport(config.sample_rate, stations=[], frequency=freq)


def _signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)


def client_scp(config) -> set[str]:
    """MASTER.SCP on the client machine, used to tidy callsigns in the CW translation."""
    from ic705suite.apps.cw.skimmer import load_scp

    return load_scp(config.scp_path) if config.scp_path.exists() else set()


async def run_plain(link: Link, startup: list[str] | None = None,
                    scp: set[str] | None = None) -> int:
    """The CW application in line mode (stdin commands, stdout text)."""
    from ic705suite.apps.cw.plain import PlainClient

    stop = asyncio.Event()
    _signals(stop)
    client = PlainClient(link, stop, scp=scp)
    try:
        await link.connect()
        await link.call("cw.activate")
    except (LinkError, TransportError, RigError, OSError) as exc:
        await link.close()
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for line in startup or []:
        await link.command(line)
    try:
        await client.run()
    finally:
        await link.close()
    return 0


async def run_server(transport: Transport | None, config, record: Path | None) -> int:
    """Headless core plus WebSocket server; messages go to the console and the log."""
    from ic705suite.station.core import Core
    from ic705suite.station.server import CoreServer

    host, _, port = config.listen.rpartition(":")
    try:
        core = Core(transport, config)
        server = CoreServer(core, host or "0.0.0.0", int(port or DEFAULT_PORT),
                            config.server_token)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if record is not None:
        core.station.record_to(str(record))

    def show(event: dict) -> None:
        if event.get("type") in ("info", "error", "tx"):
            mark = {"info": "--", "error": "!!", "tx": "TX>"}[event["type"]]
            print(f"{mark} {render(event)}", flush=True)

    core.hub.subscribe(show)
    stop = asyncio.Event()
    _signals(stop)
    serving = asyncio.create_task(server.run(stop))
    starting = asyncio.create_task(core.start(retry_forever=True))
    await stop.wait()
    starting.cancel()
    await serving
    await core.stop(autosave=config.autosave_on_exit)
    return 0


def update_scp(path: Path) -> int:
    import urllib.request

    from ic705suite.apps.cw.skimmer import load_scp
    from ic705suite.config import SCP_URL

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with urllib.request.urlopen(SCP_URL, timeout=30) as response:
            tmp.write_bytes(response.read())
        count = len(load_scp(tmp))
        if count < 1000:
            raise ValueError(f"only {count} callsigns, refusing to replace the current file")
        tmp.replace(path)
    except (OSError, ValueError) as exc:
        tmp.unlink(missing_ok=True)
        print(f"error: cannot update MASTER.SCP: {exc}", file=sys.stderr)
        return 1
    print(f"{count} callsigns saved to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.init_config:
        try:
            path = write_template(args.config)
        except FileExistsError as exc:
            print(f"{exc} already exists", file=sys.stderr)
            return 1
        print(f"configuration template written to {path}; edit it and run ic705suite")
        return 0
    if args.config is None:
        migrated = migrate_legacy()
        if migrated is not None:
            print(f"settings imported from ~/.config/cwic705 into {migrated}")
    config = load_config(args.config)
    if args.update_scp:
        return update_scp(config.scp_path)
    apply_overrides(args, config)
    set_language(args.lang, config.language)
    log_file = setup_logging(args.debug or args.verbose, args.verbose)
    logging.getLogger(__name__).info("ic705suite %s starting", __version__)

    app = "cw" if (args.wav or args.plain) and args.app is None else args.app
    if app == "dstar":
        print("D-STAR is coming soon (it will be QDStar in the terminal)", file=sys.stderr)
        return 1
    transport = recording_transport(args, config)
    scp = client_scp(config)
    if args.serve:
        if args.token:
            config.server_token = args.token
        coro = run_server(transport, config, args.record)
    else:
        link: Link | None = None
        problem = ""
        if args.connect is not None:
            address = args.connect or config.connect
            if not address:
                print("error: no server address; use --connect HOST[:PORT] or [client] connect "
                      "in the config", file=sys.stderr)
                return 1
            link = RemoteLink(address, args.token or config.client_token)
        elif app not in OFFLINE_APPS:
            try:
                link = LocalLink(transport, config)
            except ValueError as exc:   # no radio configured, unknown model...
                problem = str(exc)
                if app is not None:
                    print(f"error: {exc}", file=sys.stderr)
                    return 1
            if link is not None and args.record is not None:
                link.core.station.record_to(str(args.record))
        startup = []
        if args.wav:
            startup.append(f"/play {args.wav}" + (f" {args.wav_freq}" if args.wav_freq else ""))
        if args.plain:
            coro = run_plain(link, startup, scp)
        elif app == "cw" and startup:
            coro = _run_cw(link, startup, scp)
        else:
            from ic705suite.launcher import run_suite

            coro = run_suite(link, config, scp, start_app=app, problem=problem)
    with contextlib.suppress(KeyboardInterrupt):
        code = asyncio.run(coro)
        if code:
            print(f"(details in {log_file})", file=sys.stderr)
        return code
    return 130


async def _run_cw(link: Link, startup: list[str], scp: set[str]) -> int:
    from ic705suite.apps.cw.tui import CwApp

    return await CwApp(link, startup=startup, scp=scp).run_async() or 0
