"""The suite's own memory channels (table phone_memories), for phone: a name, the frequency,
mode and filter, and for FM repeaters the duplex, its offset and the tones; a note. Kept in
the suite's database, so every client sees the same ones; the radio's own memories are
another thing (Rig.read_memory).
"""

from __future__ import annotations

import time

import sqlalchemy as sa

from ic705suite import db

FIELDS = ("name", "frequency", "mode", "filter", "data", "duplex", "offset", "tone_mode",
          "tone", "tsql", "note")

metadata = sa.MetaData()
memories_table = sa.Table(
    "phone_memories", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("name", sa.String(80), nullable=False, default=""),
    sa.Column("frequency", sa.BigInteger, nullable=False),
    sa.Column("mode", sa.String(10), nullable=False, default="USB"),
    sa.Column("filter", sa.Integer, nullable=False, default=1),
    sa.Column("data", sa.Integer, nullable=False, default=0),
    sa.Column("duplex", sa.String(1), nullable=False, default=""),
    sa.Column("offset", sa.Integer, nullable=False, default=0),
    sa.Column("tone_mode", sa.String(8), nullable=False, default=""),
    sa.Column("tone", sa.Float, nullable=False, default=88.5),
    sa.Column("tsql", sa.Float, nullable=False, default=88.5),
    sa.Column("note", sa.Text, nullable=False, default=""),
    sa.Column("added", sa.Float, nullable=False),
)


class MemoryError_(ValueError):
    pass


def clean(entry: dict) -> dict:
    """The fields of a memory, checked and with their types."""
    try:
        out = {"frequency": int(entry["frequency"])}
    except (KeyError, TypeError, ValueError):
        raise MemoryError_("a memory needs its frequency") from None
    if out["frequency"] <= 0:
        raise MemoryError_("a memory needs its frequency")
    out["name"] = str(entry.get("name") or "").strip()[:80]
    out["mode"] = str(entry.get("mode") or "USB").upper()[:10]
    out["filter"] = max(1, min(3, int(entry.get("filter") or 1)))
    out["data"] = int(bool(entry.get("data")))
    out["duplex"] = entry.get("duplex") if entry.get("duplex") in ("-", "+") else ""
    out["offset"] = max(0, int(entry.get("offset") or 0))
    out["tone_mode"] = entry.get("tone_mode") if entry.get("tone_mode") in ("tone", "tsql") \
        else ""
    out["tone"] = float(entry.get("tone") or 88.5)
    out["tsql"] = float(entry.get("tsql") or out["tone"])
    out["note"] = str(entry.get("note") or "")
    return out


class Memories:
    def __init__(self, url: str | None) -> None:
        self.engine = db.make_engine(url)
        metadata.create_all(self.engine)

    def entries(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(sa.select(memories_table).order_by(
                memories_table.c.frequency, memories_table.c.name)).mappings()
            return [dict(row) for row in rows]

    def save(self, entry: dict) -> dict:
        """A new memory, or (with its id) one changed."""
        values = clean(entry)
        with self.engine.begin() as conn:
            if entry.get("id"):
                done = conn.execute(memories_table.update().where(
                    memories_table.c.id == int(entry["id"])).values(**values))
                if not done.rowcount:
                    raise MemoryError_(f"no memory {entry['id']}")
                return {"id": int(entry["id"]), **values}
            values["added"] = time.time()
            new_id = conn.execute(memories_table.insert().values(**values)).inserted_primary_key
            return {"id": new_id[0], **values}

    def delete(self, entry_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(memories_table.delete().where(memories_table.c.id == entry_id))
