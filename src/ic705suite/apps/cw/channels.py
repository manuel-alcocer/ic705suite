"""Channels: the conversations heard in a session, their statistics and their archive.

A channel is created when a tuned frequency produces decoded text, and for every skimmer
station whose callsign is validated. It keeps the transcript (received/sent text and the
plain-language summary of each transmission), basic statistics and a pointer into the
session's audio spool. On request it is archived in a database (SQLite by default,
PostgreSQL or any SQLAlchemy URL) next to the core, with the audio as lossless FLAC whose
Vorbis comments carry the metadata (frequency and its timeline, callsign, start time...).

Archive identity: a channel with a callsign is identified by (callsign, frequency within
KEY_TOLERANCE_HZ); saving it again appends to the same archived channel. Channels without a
callsign are identified by a name the operator gives (or "Channel N"); an existing name is
only reused (appended to) when the operator agrees.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import sqlalchemy as sa

from ic705suite import db, paths

from ic705suite.rig import bands
from ic705suite.apps.cw.hamdata import QCODE_BY_NAME
from ic705suite.i18n import N_, _
from ic705suite.apps.cw.interpret import ABBREVIATIONS, CALLSIGN_RE, RST_RE, Interpretation, Interpreter

log = logging.getLogger(__name__)

DATA_DIR = paths.data_dir() / "cw"
KEY_TOLERANCE_HZ = 500      # same callsign within this distance: the same archived channel
RETUNE_HZ = 150             # the tuned channel ends when the dial moves further than this
LINE_MAX = 300              # long receive lines are cut into entries of this size
AUTO_NAME = N_("Channel {n}")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@dataclass
class ChannelStats:
    """Quality indicators. Without the true text, lost characters cannot be counted
    exactly: garbled letters and one-letter noise fragments are the measurable part."""

    chars: int = 0          # decoded characters, spaces excluded
    garbled: int = 0        # undecodable patterns ('*')
    words: int = 0
    known_words: int = 0    # CW abbreviations, Q codes, reports, numbers
    callsigns: int = 0      # callsign-shaped words
    scp_callsigns: int = 0  # ... of which in MASTER.SCP
    fragments: int = 0      # one-letter noise (K and R are words)
    transmissions: int = 0
    wpm_sum: float = 0.0
    snr_sum: float = 0.0
    samples: int = 0        # wpm/snr samples taken

    def add_word(self, word: str, scp: set[str]) -> None:
        self.words += 1
        self.chars += len(word)
        self.garbled += word.count("*")
        bare = word.rstrip("?")
        if len(word) == 1 and word not in ("K", "R", "?"):
            self.fragments += 1
        elif bare in ABBREVIATIONS or bare in QCODE_BY_NAME or bare.isdigit() \
                or (RST_RE.fullmatch(bare) and any(c.isdigit() for c in bare)):
            self.known_words += 1
        elif CALLSIGN_RE.fullmatch(bare):
            self.callsigns += 1
            self.scp_callsigns += bare in scp

    def sample(self, wpm: float, snr_db: float) -> None:
        self.wpm_sum += wpm
        self.snr_sum += snr_db
        self.samples += 1

    COUNTS = ("chars", "garbled", "words", "known_words", "callsigns", "scp_callsigns",
              "fragments", "transmissions", "wpm_sum", "snr_sum", "samples")

    def counts(self) -> dict:
        return {key: getattr(self, key) for key in self.COUNTS}

    def merge(self, other: dict) -> None:
        for key in self.COUNTS:
            setattr(self, key, getattr(self, key) + other.get(key, 0))

    def as_dict(self) -> dict:
        words = max(1, self.words)
        return {
            "chars": self.chars, "garbled": self.garbled, "words": self.words,
            "known_words": self.known_words, "callsigns": self.callsigns,
            "scp_callsigns": self.scp_callsigns, "fragments": self.fragments,
            "transmissions": self.transmissions,
            "recognized_pct": round(100 * (self.known_words + self.callsigns) / words, 1),
            "noise_pct": round(100 * (self.fragments + self.garbled) / words, 1),
            "wpm": round(self.wpm_sum / self.samples, 1) if self.samples else None,
            "snr_db": round(self.snr_sum / self.samples, 1) if self.samples else None,
            "wpm_sum": self.wpm_sum, "snr_sum": self.snr_sum, "samples": self.samples,
        }


@dataclass
class ChannelRecord:
    id: int
    source: str                     # "tuned" or "skimmer"
    frequency: int | None
    scp: set[str]
    callsign: str | None = None
    started: str = field(default_factory=utc_now)
    last: str = field(default_factory=utc_now)
    entries: list[dict] = field(default_factory=list)   # {utc, kind: rx|tx|summary, text}
    stats: ChannelStats = field(default_factory=ChannelStats)
    senders: Counter = field(default_factory=Counter)
    audio_start: int | None = None  # spool sample positions
    audio_end: int | None = None
    archive_id: int | None = None
    archive_name: str | None = None
    saved_entries: int = 0          # entries already archived
    saved_audio: int | None = None  # spool position already archived
    saved_counts: dict = field(default_factory=dict)  # statistics already archived
    _line: str = ""
    _word: str = ""
    _interpreter: Interpreter | None = None

    def __post_init__(self) -> None:
        self._interpreter = Interpreter(scp=self.scp)

    @property
    def label(self) -> str:
        if self.archive_name:
            return self.archive_name
        if self.callsign:
            return self.callsign
        return bands.format_frequency(self.frequency) if self.frequency else f"#{self.id}"

    @property
    def unsaved(self) -> bool:
        pending = len(self.entries) > self.saved_entries or bool(self._line.strip())
        return pending and self.stats.words > 0

    @property
    def text(self) -> str:
        """Received text, oldest first (for seeding a view)."""
        return " ".join(e["text"] for e in self.entries if e["kind"] == "rx") + \
            (" " + self._line if self._line else "")

    def add_rx(self, text: str, wpm: float | None = None, snr_db: float | None = None,
               spool_pos: int | None = None, lookback: int = 0) -> None:
        if spool_pos is not None:
            if self.audio_start is None:
                # Text comes out after the audio it was decoded from: start earlier.
                self.audio_start = max(0, spool_pos - lookback)
            self.audio_end = spool_pos
        self.last = utc_now()
        for ch in text:
            if ch == " ":
                if self._word:
                    self.stats.add_word(self._word, self.scp)
                    self._word = ""
            else:
                self._word += ch
        self._line += text
        if len(self._line) >= LINE_MAX:
            self._close_line()
        for result in self._interpreter.feed(text):
            self._transmission(result)
        if wpm is not None and snr_db is not None and text.strip():
            self.stats.sample(wpm, snr_db)

    def add_tx(self, text: str) -> None:
        self._close_line()
        self.entries.append({"utc": utc_now(), "kind": "tx", "text": text})

    def tick(self) -> None:
        result = self._interpreter.tick()
        if result is not None:
            self._transmission(result)

    def seed(self, text: str) -> None:
        """Text decoded before the channel existed (e.g. by the skimmer before validation)."""
        if text.strip():
            self.add_rx(text.strip() + " ")

    def _close_line(self) -> None:
        if self._line.strip():
            self.entries.append({"utc": utc_now(), "kind": "rx", "text": " ".join(self._line.split())})
        self._line = ""

    def _transmission(self, result: Interpretation) -> None:
        self._close_line()
        self.stats.transmissions += 1
        if result.summary:
            self.entries.append({"utc": utc_now(), "kind": "summary",
                                 "text": ". ".join(result.summary) + "."})
        if result.sender:
            # The station calling CQ holds the frequency: that is the channel's callsign.
            cq = "CQ" in result.words or "TEST" in result.words
            self.senders[result.sender] += 2 if cq else 1
            best, count = self.senders.most_common(1)[0]
            if self.source == "tuned" and (count >= 2 or best in self.scp):
                self.callsign = best

    def flush(self) -> None:
        self._close_line()

    def as_dict(self) -> dict:
        return {"id": self.id, "source": self.source, "label": self.label,
                "frequency": self.frequency, "callsign": self.callsign,
                "started": self.started, "last": self.last, "unsaved": self.unsaved,
                "archive_name": self.archive_name, "audio": self.audio_start is not None,
                "stats": self.stats.as_dict()}


class ChannelBook:
    """The channels of the running session."""

    def __init__(self, scp: set[str] | None = None,
                 on_change: Callable[[], None] | None = None, sample_rate: int = 8000) -> None:
        self.scp = scp or set()
        self.lookback_tuned = 10 * sample_rate   # audio kept before a tuned channel's text
        self.lookback_skimmer = 30 * sample_rate  # skimmer stations validate after a while
        self.on_change = on_change or (lambda: None)
        self.records: list[ChannelRecord] = []
        self.tuned: ChannelRecord | None = None
        self._skimmer: dict[int, ChannelRecord] = {}  # id(skimmer channel) -> record
        self._next = 1

    def _new(self, source: str, frequency: int | None) -> ChannelRecord:
        record = ChannelRecord(self._next, source, frequency, self.scp)
        self._next += 1
        self.records.append(record)
        self.on_change()
        return record

    def get(self, record_id: int) -> ChannelRecord | None:
        return next((r for r in self.records if r.id == record_id), None)

    # tuned decoding ----------------------------------------------------------------------

    def tuned_rx(self, frequency: int | None, text: str, wpm: float, snr_db: float,
                 spool_pos: int | None) -> None:
        if self.tuned is not None and frequency is not None and self.tuned.frequency is not None \
                and abs(frequency - self.tuned.frequency) > RETUNE_HZ:
            self.end_tuned()
        if self.tuned is None:
            if not text.strip():
                return
            self.tuned = self._new("tuned", frequency)
        self.tuned.add_rx(text, wpm, snr_db, spool_pos, self.lookback_tuned)

    def tuned_tx(self, frequency: int | None, text: str) -> None:
        if self.tuned is None:
            self.tuned = self._new("tuned", frequency)
        self.tuned.add_tx(text)

    def end_tuned(self) -> None:
        if self.tuned is not None:
            self.tuned.flush()
            self.tuned = None
            self.on_change()

    def follow(self, record: ChannelRecord, frequency: int | None) -> None:
        """Keep decoding a skimmer station's channel in single-channel mode (/go)."""
        self.end_tuned()
        record.source = "tuned"
        record.frequency = frequency or record.frequency
        self.tuned = record
        self.on_change()

    # skimmer stations --------------------------------------------------------------------

    def skimmer_validated(self, channel, frequency: int | None) -> ChannelRecord:
        record = self._skimmer.get(id(channel))
        if record is not None and record.callsign is None:
            record.callsign = channel.callsign  # picked (/go) before it was validated
            self.on_change()
        elif record is None or record.callsign != channel.callsign:
            record = self._new("skimmer", frequency)
            record.callsign = channel.callsign
            record.seed(channel.text)
            self._skimmer[id(channel)] = record
        return record

    def attach(self, channel, record: ChannelRecord) -> None:
        """Bind a skimmer station to a channel created for it before validation."""
        self._skimmer[id(channel)] = record

    def skimmer_rx(self, channel, text: str, frequency: int | None,
                   spool_pos: int | None) -> None:
        record = self._skimmer.get(id(channel))
        if record is not None:
            record.frequency = frequency or record.frequency
            record.add_rx(text, channel.wpm, channel.snr_db, spool_pos, self.lookback_skimmer)

    def record_for(self, channel) -> ChannelRecord | None:
        return self._skimmer.get(id(channel))

    def tick(self) -> None:
        for record in self.records:
            record.tick()

    def summaries(self) -> list[dict]:
        return [r.as_dict() for r in self.records if r.stats.words or r.entries]


class AudioSpool:
    """All received audio of the session in a temporary file, for archiving channels."""

    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        cache = paths.cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="spool-", suffix=".raw", dir=cache)
        self.path = Path(name)
        self._file = os.fdopen(fd, "w+b")
        self.position = 0  # samples written
        self.timeline: list[tuple[int, int]] = []  # (sample, dial Hz)

    def write(self, samples: np.ndarray, frequency: int | None) -> None:
        if frequency is not None and (not self.timeline or self.timeline[-1][1] != frequency):
            self.timeline.append((self.position, frequency))
        self._file.seek(0, os.SEEK_END)
        self._file.write((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        self.position += len(samples)

    def blocks(self, start: int, end: int, size: int = 1 << 18):
        """The samples from `start` to `end` as int16, `size` at a time: a channel open for
        hours is hundreds of MB, more than a Raspberry Pi has to read it whole."""
        self._file.flush()
        for at in range(start, end, size):
            self._file.seek(at * 2)
            yield np.frombuffer(self._file.read(min(size, end - at) * 2), "<i2")

    def frequencies(self, start: int, end: int) -> list[tuple[float, int]]:
        """Dial timeline relative to `start`, in seconds."""
        current = [f for s, f in self.timeline if s <= start][-1:] or []
        out = [(0.0, current[0])] if current else []
        out += [(round((s - start) / self.sample_rate, 2), f)
                for s, f in self.timeline if start < s < end]
        return out

    def close(self) -> None:
        self._file.close()
        self.path.unlink(missing_ok=True)


def write_flac(path: Path, blocks, sample_rate: int, tags: dict[str, str]) -> None:
    """Lossless FLAC with Vorbis comment metadata, from blocks of int16 samples."""
    import soundfile

    from ic705suite.apps.cw.flactags import write_tags

    path.parent.mkdir(parents=True, exist_ok=True)
    with soundfile.SoundFile(str(path), "w", sample_rate, 1, subtype="PCM_16",
                             format="FLAC") as out:
        for block in blocks:
            out.write(block)
    write_tags(path, tags)


metadata = sa.MetaData()

channels_table = sa.Table(
    "cw_channels", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(80), nullable=False, unique=True),
    sa.Column("callsign", sa.String(20), index=True),
    sa.Column("frequency", sa.BigInteger),
    sa.Column("created", sa.String(32), nullable=False),
    sa.Column("updated", sa.String(32), nullable=False),
    sa.Column("stats", sa.Text, nullable=False, default="{}"),
)
entries_table = sa.Table(
    "cw_entries", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("channel_id", sa.Integer, sa.ForeignKey("cw_channels.id", ondelete="CASCADE"),
              nullable=False, index=True),
    sa.Column("utc", sa.String(32), nullable=False),
    sa.Column("kind", sa.String(10), nullable=False),
    sa.Column("text", sa.Text, nullable=False),
)
audio_table = sa.Table(
    "cw_audio", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("channel_id", sa.Integer, sa.ForeignKey("cw_channels.id", ondelete="CASCADE"),
              nullable=False, index=True),
    sa.Column("path", sa.Text, nullable=False),
    sa.Column("started", sa.String(32), nullable=False),
    sa.Column("seconds", sa.Float, nullable=False),
)



class ChannelStore:
    """Archive of channels in any SQLAlchemy database (SQLite by default, PostgreSQL...)
    plus their FLAC files, kept next to the core."""

    def __init__(self, url: str | None = None, audio_dir: Path | None = None) -> None:
        self.url = db.database_url(url)
        self.engine = db.make_engine(self.url)
        metadata.create_all(self.engine)
        self.audio_dir = audio_dir or DATA_DIR / "audio"

    def close(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _row(row) -> dict | None:
        return dict(row._mapping) if row is not None else None

    def by_key(self, callsign: str, frequency: int | None) -> dict | None:
        with self.engine.connect() as conn:
            for row in conn.execute(sa.select(channels_table)
                                    .where(channels_table.c.callsign == callsign)):
                f = row.frequency
                if frequency is None or f is None or abs(f - frequency) <= KEY_TOLERANCE_HZ:
                    return self._row(row)
        return None

    def by_name(self, name: str) -> dict | None:
        with self.engine.connect() as conn:
            return self._row(conn.execute(
                sa.select(channels_table).where(channels_table.c.name == name)).first())

    def auto_name(self) -> str:
        n = 1
        while self.by_name(_(AUTO_NAME).format(n=n)) or self.by_name(AUTO_NAME.format(n=n)):
            n += 1
        return _(AUTO_NAME).format(n=n)

    def save(self, record: ChannelRecord, name: str | None, append_to: dict | None,
             spool: AudioSpool | None, with_audio: bool) -> tuple[int, str]:
        record.flush()
        now = utc_now()
        counts = record.stats.counts()
        delta = {k: v - record.saved_counts.get(k, 0) for k, v in counts.items()}
        with self.engine.begin() as conn:
            if append_to is not None:
                channel_id, name = append_to["id"], append_to["name"]
                stats = ChannelStats()
                stats.merge(json.loads(append_to["stats"] or "{}"))
                stats.merge(delta)  # only what this record added since it was last archived
                conn.execute(channels_table.update().where(channels_table.c.id == channel_id)
                             .values(updated=now, stats=json.dumps(stats.as_dict())))
            else:
                channel_id = conn.execute(channels_table.insert().values(
                    name=name, callsign=record.callsign, frequency=record.frequency,
                    created=record.started, updated=now,
                    stats=json.dumps(record.stats.as_dict()))).inserted_primary_key[0]
            new = record.entries[record.saved_entries:]
            if new:
                conn.execute(entries_table.insert(), [
                    {"channel_id": channel_id, "utc": e["utc"], "kind": e["kind"], "text": e["text"]}
                    for e in new])
            if with_audio and spool is not None and record.audio_start is not None:
                start = max(record.audio_start, record.saved_audio or 0)
                if spool.position > start:
                    self._save_audio(conn, channel_id, name, record, spool, start, spool.position)
                    record.saved_audio = spool.position
        record.saved_counts = counts
        record.saved_entries = len(record.entries)
        record.archive_id, record.archive_name = channel_id, name
        return channel_id, name

    def _save_audio(self, conn, channel_id: int, name: str, record: ChannelRecord,
                    spool: AudioSpool, start: int, end: int) -> None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe = "".join(c if c.isalnum() else "_" for c in name)[:40]
        path = self.audio_dir / f"{channel_id:05d}-{safe}-{stamp}.flac"
        seconds = (end - start) / spool.sample_rate
        started = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(
            seconds=(spool.position - start) / spool.sample_rate)).isoformat(timespec="seconds")
        write_flac(path, spool.blocks(start, end), spool.sample_rate, {
            "TITLE": name, "ARTIST": record.callsign or "", "DATE": started[:10],
            "COMMENT": f"ic705suite CW recording, {bands.format_frequency(record.frequency)} MHz",
            "IC705SUITE_CHANNEL": name, "IC705SUITE_CALLSIGN": record.callsign or "",
            "IC705SUITE_FREQUENCY": record.frequency, "IC705SUITE_START_UTC": started,
            "IC705SUITE_SAMPLE_RATE": spool.sample_rate,
            "IC705SUITE_FREQUENCY_TIMELINE": json.dumps(spool.frequencies(start, end)),
        })
        conn.execute(audio_table.insert().values(channel_id=channel_id, path=str(path),
                                                 started=started, seconds=seconds))
        log.info("channel %s: %.0f s of audio saved to %s", name, seconds, path)

    def list(self) -> list[dict]:
        entries = sa.select(sa.func.count()).select_from(entries_table)\
            .where(entries_table.c.channel_id == channels_table.c.id).scalar_subquery()
        audio = sa.select(sa.func.count()).select_from(audio_table)\
            .where(audio_table.c.channel_id == channels_table.c.id).scalar_subquery()
        query = sa.select(channels_table, entries.label("n_entries"), audio.label("n_audio"))\
            .order_by(channels_table.c.updated.desc())
        with self.engine.connect() as conn:
            return [{"id": r.id, "name": r.name, "callsign": r.callsign, "frequency": r.frequency,
                     "created": r.created, "updated": r.updated, "entries": r.n_entries,
                     "audio": r.n_audio, "stats": json.loads(r.stats or "{}")}
                    for r in conn.execute(query)]

    def transcript(self, channel_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(sa.select(channels_table)
                               .where(channels_table.c.id == channel_id)).first()
            if row is None:
                return None
            entries = [dict(e._mapping) for e in conn.execute(
                sa.select(entries_table.c.utc, entries_table.c.kind, entries_table.c.text)
                .where(entries_table.c.channel_id == channel_id).order_by(entries_table.c.id))]
            audio = [dict(a._mapping) for a in conn.execute(
                sa.select(audio_table.c.path, audio_table.c.started, audio_table.c.seconds)
                .where(audio_table.c.channel_id == channel_id).order_by(audio_table.c.id))]
        return {"id": row.id, "name": row.name, "callsign": row.callsign,
                "frequency": row.frequency, "stats": json.loads(row.stats or "{}"),
                "entries": entries, "audio": audio}
