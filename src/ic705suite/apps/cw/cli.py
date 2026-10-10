"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from pathlib import Path

from ic705suite.apps.cw import __version__
from ic705suite.apps.cw.config import DEFAULT_PATH, load_config, write_template
from ic705suite.apps.cw.core import DEFAULT_PORT
from ic705suite.apps.cw.i18n import render, set_language
from ic705suite.apps.cw.links import Link, LinkError, LocalLink, RemoteLink
from ic705suite.apps.cw.radio import CivError
from ic705suite.apps.cw.transport import Transport, TransportError

LOG_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "cwic705"
TRACE = 5


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="cwic705",
        description="CW decoder/encoder for the Icom IC-705 over WiFi.",
        epilog=f"Settings are read from {DEFAULT_PATH} (create it with --init-config). "
               "The password can also be given in the CWIC705_PASSWORD environment variable.")
    conn = p.add_argument_group("connection")
    conn.add_argument("--host", help="IP address or hostname of the IC-705")
    conn.add_argument("--user", help="network username (Remote Settings > Network User1)")
    conn.add_argument("--password", help="network password")
    conn.add_argument("--port", type=int, help="control port (default 50001)")
    conn.add_argument("--sample-rate", type=int, choices=(8000, 16000, 24000, 48000),
                      help="receive audio sample rate (default 8000)")
    conn.add_argument("--usb", metavar="DEVICE", help="connect over USB (next version)")
    conn.add_argument("--sim", action="store_true", help="use the built-in radio simulator")
    conn.add_argument("--wav", metavar="FILE", type=Path,
                      help="decode a recording as if it came from the radio (no radio needed)")
    conn.add_argument("--wav-freq", metavar="MHZ",
                      help="dial frequency of the recording, if it has no .json sidecar")
    p.add_argument("--call", help="your callsign (used in macros)")
    p.add_argument("--wpm", type=int, help="keyer speed set at start-up")
    p.add_argument("--tx", action="store_true",
                   help="enable transmission (default: receive only; check your antenna SWR first)")
    p.add_argument("--record", metavar="WAV", type=Path,
                   help="save received audio to a WAV file (analyse it with cwic705-replay)")
    p.add_argument("--plain", action="store_true", help="line mode instead of full screen")
    mode = p.add_argument_group("client/server")
    mode.add_argument("--serve", action="store_true",
                      help="run the core as a server for remote clients (e.g. on a Raspberry Pi)")
    mode.add_argument("--listen", metavar="HOST:PORT",
                      help=f"server address (default from the config, port {DEFAULT_PORT})")
    mode.add_argument("--connect", metavar="HOST[:PORT]", nargs="?", const="",
                      help="connect to a cwic705 server instead of the radio")
    mode.add_argument("--token", help="token for --serve/--connect (default from the config)")
    p.add_argument("--lang", help="interface language (en, es)")
    p.add_argument("--config", type=Path, help="configuration file")
    p.add_argument("--init-config", action="store_true", help="write a configuration template and exit")
    p.add_argument("--update-scp", action="store_true",
                   help="download MASTER.SCP (callsign database used by the skimmer) and exit")
    p.add_argument("--debug", action="store_true", help="debug logging (add -v for packet dumps)")
    p.add_argument("-v", "--verbose", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--version", action="version", version=f"cwic705 {__version__}")
    return p.parse_args(argv)


def setup_logging(debug: bool, trace: bool) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "cwic705.log"
    logging.addLevelName(TRACE, "TRACE")
    logging.basicConfig(
        filename=log_file, level=TRACE if trace else logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return log_file


def build_transport(args: argparse.Namespace, config) -> Transport:
    if args.wav:
        from ic705suite.apps.cw.bands import parse_frequency
        from ic705suite.apps.cw.playback import load_sidecar
        from ic705suite.apps.cw.transport.sim import SimTransport

        timeline = load_sidecar(args.wav)
        freq = parse_frequency(args.wav_freq) if args.wav_freq else \
            (timeline[0][1] if timeline else 7_030_000)
        return SimTransport(config.sample_rate, stations=[], frequency=freq)
    if args.sim:
        from ic705suite.apps.cw.transport.sim import SimTransport
        return SimTransport(config.sample_rate)
    if args.usb:
        from ic705suite.apps.cw.transport.usb import UsbTransport
        return UsbTransport(args.usb, sample_rate=config.sample_rate)
    from ic705suite.apps.cw.transport.lan import LanTransport
    if not config.host:
        raise SystemExit("error: no radio address; use --host, the config file "
                         "(--init-config) or --sim")
    if not config.username:
        raise SystemExit("error: no network username; use --user or the config file")
    return LanTransport(config.host, config.username, config.password, config.sample_rate,
                        config.control_port)


def _signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)


def client_scp(config) -> set[str]:
    """MASTER.SCP on the client machine, used to tidy callsigns in the translation."""
    from ic705suite.apps.cw.skimmer import load_scp

    return load_scp(config.scp_path) if config.scp_path.exists() else set()


async def run_client(link: Link, plain: bool, startup: list[str] | None = None,
                     scp: set[str] | None = None) -> int:
    """A user interface on a link (local core or remote server)."""
    if not plain:
        from ic705suite.apps.cw.tui import CwApp

        return await CwApp(link, startup=startup, scp=scp).run_async() or 0
    from ic705suite.apps.cw.plain import PlainClient

    stop = asyncio.Event()
    _signals(stop)
    client = PlainClient(link, stop, scp=scp)
    try:
        await link.connect()
    except (LinkError, TransportError, CivError, OSError) as exc:
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


async def run_server(transport: Transport, config, record: Path | None) -> int:
    """Headless core plus WebSocket server; messages go to the console and the log."""
    from ic705suite.apps.cw.core import Core
    from ic705suite.apps.cw.server import CoreServer

    host, _, port = config.listen.rpartition(":")
    core = Core(transport, config)
    try:
        server = CoreServer(core, host or "0.0.0.0", int(port or DEFAULT_PORT),
                            config.server_token)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if record is not None:
        core.session.record_to(str(record))

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

    from ic705suite.apps.cw.config import SCP_URL
    from ic705suite.apps.cw.skimmer import load_scp

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
        print(f"configuration template written to {path}; edit it and run cwic705")
        return 0
    config = load_config(args.config)
    if args.update_scp:
        return update_scp(config.scp_path)
    for attr, value in (("host", args.host), ("username", args.user), ("password", args.password),
                        ("control_port", args.port), ("sample_rate", args.sample_rate),
                        ("wpm", args.wpm)):
        if value is not None:
            setattr(config, attr, value)
    if args.tx:
        config.tx_enabled = True
    if args.call:
        config.callsign = args.call.upper()
    if args.listen:
        config.listen = args.listen
    set_language(args.lang, config.language)
    log_file = setup_logging(args.debug or args.verbose, args.verbose)
    logging.getLogger(__name__).info("cwic705 %s starting", __version__)

    if args.connect is not None:
        address = args.connect or config.connect
        if not address:
            print("error: no server address; use --connect HOST[:PORT] or [client] connect "
                  "in the config", file=sys.stderr)
            return 1
        coro = run_client(RemoteLink(address, args.token or config.client_token), args.plain,
                          scp=client_scp(config))
    else:
        transport = build_transport(args, config)
        if args.serve:
            if args.token:
                config.server_token = args.token
            coro = run_server(transport, config, args.record)
        else:
            link = LocalLink(transport, config)
            if args.record is not None:
                link.core.session.record_to(str(args.record))
            startup = []
            if args.wav:
                startup.append(f"/play {args.wav}" + (f" {args.wav_freq}" if args.wav_freq else ""))
            coro = run_client(link, args.plain, startup, scp=client_scp(config))
    with contextlib.suppress(KeyboardInterrupt):
        code = asyncio.run(coro)
        if code:
            print(f"(details in {log_file})", file=sys.stderr)
        return code
    return 130
