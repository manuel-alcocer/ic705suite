"""Line-oriented client: decoded text on stdout, commands/text read from stdin.

Useful over SSH without a full terminal, for logging, or for scripting.
"""

from __future__ import annotations

import asyncio
import sys

from ic705suite.i18n import _, render
from ic705suite.apps.cw.interpret import Interpretation, Interpreter
from ic705suite.station.links import Link


class PlainClient:
    def __init__(self, link: Link, quit_event: asyncio.Event,
                 scp: set[str] | None = None) -> None:
        self.link = link
        self.quit_event = quit_event
        self._at_line_start = True
        self._transmitting = False
        self.interpreter = Interpreter(scp=scp)
        link.attach(self._on_event)

    def _line(self, text: str) -> None:
        if not self._at_line_start:
            sys.stdout.write("\n")
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        self._at_line_start = True

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "state":
            self._transmitting = event.get("transmitting", False)
        elif kind == "rx":
            sys.stdout.write(event["text"])
            sys.stdout.flush()
            self._at_line_start = False
            for result in self.interpreter.feed(event["text"]):
                self._meaning(result)
        elif kind == "tx":
            self._line(f"TX> {event['text']}")
        elif kind == "history":
            self._line("── " + _("earlier on {title}").format(title=event["title"]) + " ──")
            self._line(event["text"][-600:])
        elif kind == "info":
            self._line(f"-- {render(event)}")
        elif kind == "error":
            self._line(f"!! {render(event)}")

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue[str | None] = asyncio.Queue()

        def on_stdin() -> None:
            line = sys.stdin.readline()
            lines.put_nowait(line if line else None)

        loop.add_reader(sys.stdin.fileno(), on_stdin)
        stop = asyncio.create_task(self._stopped())
        ticker = asyncio.create_task(self._tick())
        try:
            while True:
                get = asyncio.create_task(lines.get())
                done, _ = await asyncio.wait({get, stop}, return_when=asyncio.FIRST_COMPLETED)
                if get not in done:
                    get.cancel()
                    break
                line = get.result()
                if line is None:  # EOF: keep receiving until Ctrl-C
                    loop.remove_reader(sys.stdin.fileno())
                    await stop
                    break
                if line.strip().lower() in ("/quit", "/q", "/exit"):
                    break
                if line.strip().lower().startswith("/wait"):
                    await self._wait(line.split()[1:])  # scripting aid, also works remotely
                    continue
                await self.link.command(line.strip())
        finally:
            stop.cancel()
            ticker.cancel()
            loop.remove_reader(sys.stdin.fileno())
        if sys.stdin.isatty() and not self.link.closed.is_set():
            await self._offer_save()

    async def _ask(self, prompt: str) -> str:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        line = await asyncio.get_running_loop().run_in_executor(None, sys.stdin.readline)
        return line.strip()

    async def _offer_save(self) -> None:
        """Same questions as the full-screen client: save?, name, reuse an existing name."""
        listing = await self.link.call("channels")
        pending = [c for c in listing.get("session", []) if c.get("unsaved")]
        if not pending:
            return
        self._line(_("{n} channels of this session are not saved: {names}").format(
            n=len(pending), names=", ".join(c["label"] for c in pending)))
        answer = (await self._ask(_("Save them? [a]udio and text / [t]ext only / [n]o: "))).lower()
        if answer[:1] not in ("a", "t"):
            return
        for channel in pending:
            params: dict = {"id": channel["id"], "audio": answer.startswith("a")}
            while True:
                result = await self.link.call("save_channel", **params)
                status = result.get("status")
                if status == "needs_name":
                    name = await self._ask(_("Name for channel {label} (empty: {suggestion}): ")
                                           .format(label=channel["label"],
                                                   suggestion=result.get("suggestion", "")))
                    params.update(name=name, auto=not name, append=False)
                elif status == "name_exists":
                    reuse = await self._ask(_("«{name}» exists. Append to it? [y/n]: ")
                                            .format(name=result["name"]))
                    if reuse.lower().startswith(("y", "s")):
                        params["append"] = True
                    else:
                        params.pop("name", None)
                        params.pop("auto", None)
                else:
                    if status in ("saved", "appended"):
                        self._line(_("channel saved as «{name}»").format(name=result["name"]))
                    break

    def _meaning(self, result: Interpretation) -> None:
        if result.summary:
            self._line("≈ " + ". ".join(result.summary) + ".")

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            result = self.interpreter.tick()
            if result is not None:
                self._meaning(result)

    async def _wait(self, args: list[str]) -> None:
        """/wait N pauses N seconds; /wait alone waits until the transmission ends."""
        if args:
            await asyncio.sleep(float(args[0]))
            return
        await asyncio.sleep(1.0)  # let the server start the transmission
        while self._transmitting:
            await asyncio.sleep(0.2)

    async def _stopped(self) -> None:
        quit_wait = asyncio.create_task(self.quit_event.wait())
        closed_wait = asyncio.create_task(self.link.closed.wait())
        await asyncio.wait({quit_wait, closed_wait}, return_when=asyncio.FIRST_COMPLETED)
        quit_wait.cancel()
        closed_wait.cancel()
