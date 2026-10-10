"""Archiving the CW channels of the session, asked from whichever screen is open
(the CW application, or the dashboard when leaving the suite)."""

from __future__ import annotations

from ic705suite.i18n import _
from ic705suite.ui.widgets import ChoiceScreen, NameScreen


class ChannelSaving:
    """Mixin for a Textual App with a ``link`` and a ``_message(kind, text)`` method."""

    async def offer_save_channels(self) -> bool:
        """Ask about the unsaved channels. False when the operator cancels leaving."""
        try:
            listing = await self.link.call("channels")
        except Exception:
            listing = {}
        pending = [c for c in listing.get("session", []) if c.get("unsaved")]
        if not pending:
            return True
        names = ", ".join(c["label"] for c in pending[:8]) + ("..." if len(pending) > 8 else "")
        options = [("audio", _("Save with audio")), ("text", _("Save without audio")),
                   ("no", _("Don't save")), ("cancel", _("Cancel"))]
        if not listing.get("audio_available", True):
            options = [o for o in options if o[0] != "audio"]
        choice = await self.push_screen_wait(ChoiceScreen(
            _("Save channels"),
            _("{n} channels of this session are not saved: {names}").format(
                n=len(pending), names=names), options))
        if choice in ("", "cancel"):
            return False
        if choice in ("audio", "text"):
            for channel in pending:
                await self.save_channel_flow(channel, audio=choice == "audio")
        return True

    async def save_channel_flow(self, channel: dict, audio: bool = False,
                                ask_audio: bool = False) -> None:
        """Archive one channel, asking for a name when it has no callsign and before
        reusing an existing name (append mode)."""
        if ask_audio:
            choice = await self.push_screen_wait(ChoiceScreen(
                _("Save channel"), _("Save {label} with its audio?").format(label=channel["label"]),
                [("audio", _("With audio")), ("text", _("Text only")), ("cancel", _("Cancel"))]))
            if choice in ("", "cancel"):
                return
            audio = choice == "audio"
        params: dict = {"id": channel["id"], "audio": audio}
        while True:
            result = await self.link.call("save_channel", **params)
            status = result.get("status")
            if status == "needs_name":
                name = await self.push_screen_wait(NameScreen(
                    _("Name the channel"),
                    _("The channel {label} has no callsign. Name for it (empty: {suggestion}):")
                    .format(label=result.get("label", channel["label"]),
                            suggestion=result.get("suggestion", "")),
                    placeholder=result.get("suggestion", "")))
                if name is None:
                    return  # skipped
                params.update(name=name.strip(), auto=not name.strip(), append=False)
            elif status == "name_exists":
                answer = await self.push_screen_wait(ChoiceScreen(
                    _("Name in use"),
                    _("A channel named «{name}» already exists. Add this conversation to it?")
                    .format(name=result["name"]),
                    [("yes", _("Yes, append")), ("no", _("No, another name"))]))
                if answer == "yes":
                    params["append"] = True
                else:
                    params.pop("name", None)
                    params.pop("auto", None)
            elif status in ("saved", "appended"):
                msg = _("channel saved as «{name}»") if status == "saved" else \
                    _("conversation added to channel «{name}»")
                self._message("info", msg.format(name=result["name"]))
                return
            else:
                if status == "error":
                    self._message("error", _("cannot save {label}: {error}").format(
                        label=channel["label"], error=result.get("error")))
                return
