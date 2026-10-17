"""The FT8 operator's lists, in the suite's database (table ft8_lists): the stations and
countries ignored (never worked by the automatic modes) and the stations wanted (Auto CQ
calls them when they are heard).

Each entry is a kind ("call", "country" or "wanted"), a name (a callsign without its
portable suffix, or a country as cty.dat names it), a note and when it was added.
"""

from __future__ import annotations

import time

import sqlalchemy as sa

from ic705suite import db

KINDS = ("call", "country", "wanted")

metadata = sa.MetaData()
lists_table = sa.Table(
    "ft8_lists", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("kind", sa.String(10), nullable=False),
    sa.Column("name", sa.String(80), nullable=False),
    sa.Column("note", sa.Text, nullable=False, default=""),
    sa.Column("added", sa.Float, nullable=False),
    sa.UniqueConstraint("kind", "name", name="ft8_lists_kind_name"),
)


class ListError(ValueError):
    pass


def clean(kind: str, name: str) -> str:
    if kind not in KINDS:
        raise ListError(f"unknown list {kind!r}")
    name = name.strip()
    if kind != "country":
        name = name.upper().strip("<>").split("/")[0]
    if not name:
        raise ListError("an empty name")
    return name


class Lists:
    def __init__(self, url: str | None) -> None:
        self.engine = db.make_engine(url)
        metadata.create_all(self.engine)

    def entries(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(sa.select(lists_table).order_by(
                lists_table.c.kind, lists_table.c.name)).mappings()
            return [dict(row) for row in rows]

    def names(self, kind: str) -> set[str]:
        with self.engine.connect() as conn:
            return set(conn.execute(sa.select(lists_table.c.name).where(
                lists_table.c.kind == kind)).scalars())

    def add(self, kind: str, name: str, note: str = "") -> dict:
        """Into the list (as it is, when it is there already)."""
        name = clean(kind, name)
        with self.engine.begin() as conn:
            row = conn.execute(sa.select(lists_table).where(
                lists_table.c.kind == kind, lists_table.c.name == name)).mappings().first()
            if row is not None:
                return dict(row)
            entry = {"kind": kind, "name": name, "note": note, "added": time.time()}
            entry["id"] = conn.execute(lists_table.insert().values(**entry)).inserted_primary_key[0]
            return entry

    def remove(self, kind: str = "", name: str = "", entry_id: int | None = None) -> None:
        with self.engine.begin() as conn:
            if entry_id is not None:
                conn.execute(lists_table.delete().where(lists_table.c.id == entry_id))
            else:
                conn.execute(lists_table.delete().where(
                    lists_table.c.kind == kind, lists_table.c.name == clean(kind, name)))

    def edit(self, entry_id: int, name: str | None = None, note: str | None = None) -> dict:
        with self.engine.begin() as conn:
            row = conn.execute(sa.select(lists_table).where(
                lists_table.c.id == entry_id)).mappings().first()
            if row is None:
                raise ListError(f"no entry {entry_id}")
            values = {}
            if name is not None:
                values["name"] = clean(row["kind"], name)
            if note is not None:
                values["note"] = note
            if values:
                try:
                    conn.execute(lists_table.update().where(
                        lists_table.c.id == entry_id).values(**values))
                except sa.exc.IntegrityError:
                    raise ListError(f"{values.get('name')} is in that list already") from None
            return {**dict(row), **values}
