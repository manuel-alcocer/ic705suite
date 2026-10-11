"""Photos of the antenna kept with the history's entries, over the link: the clients (terminal
and graphical) may be on another machine than the core, so a photo travels in pieces (the
link's messages are small) and is opened from a local copy."""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ic705suite.apps.swr.service import MAX_PHOTO, PHOTO_CHUNK, photo_type
from ic705suite.station.links import Link

EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


class PhotoError(Exception):
    pass


def read_photo(path: str | Path) -> bytes:
    """The file at `path`, if it is a photo the history accepts."""
    path = Path(str(path).strip().strip("'\"")).expanduser()
    if not path.is_file():
        raise PhotoError(f"no such file: {path}")
    if path.stat().st_size > MAX_PHOTO:
        raise PhotoError("the photo is too large (16 MB at most)")
    data = path.read_bytes()
    if photo_type(data) is None:
        raise PhotoError("that is not a photo (JPEG, PNG, WebP or GIF)")
    return data


async def upload(link: Link, entry_id: int, data: bytes) -> dict:
    """Send `data` as the entry's photo; returns the entry as it is now."""
    offset = 0
    while True:
        piece = data[offset:offset + PHOTO_CHUNK]
        done = offset + len(piece) >= len(data)
        reply = await link.call("swr.photo.put", id=entry_id, offset=offset, done=done,
                                data=base64.b64encode(piece).decode())
        if reply.get("error"):
            raise PhotoError(reply["error"])
        if done:
            return reply["entry"]
        offset += len(piece)


async def download(link: Link, entry_id: int) -> tuple[bytes, str] | None:
    """(the entry's photo, its media type), or None when it has none."""
    data, size, media = bytearray(), None, ""
    while size is None or len(data) < size:
        reply = await link.call("swr.photo.get", id=entry_id, offset=len(data))
        if reply.get("error"):
            if reply["error"] == "no photo":
                return None
            raise PhotoError(reply["error"])
        size, media = reply["size"], reply["type"]
        piece = base64.b64decode(reply["data"])
        if not piece and len(data) < size:
            raise PhotoError("the photo arrived incomplete")
        data += piece
    return bytes(data), media


def save_copy(entry_id: int, data: bytes, media: str) -> Path:
    """A local copy to open with the system's viewer."""
    folder = Path(tempfile.gettempdir()) / "ic705suite-photos"
    folder.mkdir(exist_ok=True)
    path = folder / f"antenna-{entry_id}.{EXTENSIONS.get(media, 'img')}"
    path.write_bytes(data)
    return path


def open_with_viewer(path: Path) -> None:
    """The desktop's image viewer (whatever opens images here)."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - a file this program wrote
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL,  # noqa: S603, S607
                         stderr=subprocess.DEVNULL)
