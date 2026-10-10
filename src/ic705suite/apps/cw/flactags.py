"""Vorbis comments (the tags) of a FLAC file, read and written without other libraries.

A FLAC file is "fLaC", then metadata blocks (a 4-byte header: last-block flag and type in the
first byte, length in the next three, big endian), then the audio frames. The tags are the
VORBIS_COMMENT block (type 4): a vendor string and "KEY=value" entries, each preceded by its
length (32-bit little endian). Keys are case insensitive; they are read back lower case.
"""

from __future__ import annotations

import struct
from pathlib import Path

MAGIC = b"fLaC"
VORBIS_COMMENT = 4
PADDING = 1
VENDOR = b"ic705suite"


def _blocks(data: bytes) -> tuple[list[tuple[int, bytes]], int]:
    """[(type, body), ...] of the metadata, and where the audio frames start."""
    if data[:4] != MAGIC:
        raise ValueError("not a FLAC file")
    blocks, pos, last = [], 4, False
    while not last:
        if pos + 4 > len(data):
            raise ValueError("truncated FLAC metadata")
        header = data[pos]
        last, kind = bool(header & 0x80), header & 0x7F
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        blocks.append((kind, data[pos + 4:pos + 4 + length]))
        pos += 4 + length
    return blocks, pos


def _parse_comments(body: bytes) -> dict[str, list[str]]:
    tags: dict[str, list[str]] = {}
    (vendor_len,) = struct.unpack_from("<I", body, 0)
    pos = 4 + vendor_len
    (count,) = struct.unpack_from("<I", body, pos)
    pos += 4
    for _ in range(count):
        (length,) = struct.unpack_from("<I", body, pos)
        entry = body[pos + 4:pos + 4 + length].decode("utf-8", "replace")
        pos += 4 + length
        key, sep, value = entry.partition("=")
        if sep:
            tags.setdefault(key.lower(), []).append(value)
    return tags


def read_tags(path: str | Path) -> dict[str, list[str]]:
    """{key (lower case): [values]} of a FLAC file ({} without tags)."""
    blocks, _audio = _blocks(Path(path).read_bytes())
    for kind, body in blocks:
        if kind == VORBIS_COMMENT:
            return _parse_comments(body)
    return {}


def write_tags(path: str | Path, tags: dict[str, object]) -> None:
    """Replace the tags of a FLAC file with `tags` (None values are left out)."""
    path = Path(path)
    data = path.read_bytes()
    blocks, audio = _blocks(data)
    entries = [f"{key}={value}".encode() for key, value in tags.items() if value is not None]
    body = struct.pack("<I", len(VENDOR)) + VENDOR + struct.pack("<I", len(entries))
    body += b"".join(struct.pack("<I", len(e)) + e for e in entries)
    # STREAMINFO stays first; the old comments and padding go.
    kept = [(k, b) for k, b in blocks if k not in (VORBIS_COMMENT, PADDING)]
    kept.append((VORBIS_COMMENT, body))
    out = bytearray(MAGIC)
    for i, (kind, block) in enumerate(kept):
        if len(block) >= 1 << 24:
            raise ValueError("FLAC metadata block too large")
        out.append((0x80 if i == len(kept) - 1 else 0) | kind)
        out += len(block).to_bytes(3, "big") + block
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(bytes(out) + data[audio:])
    tmp.replace(path)
