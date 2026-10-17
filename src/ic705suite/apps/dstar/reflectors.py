"""D-STAR reflectors: the list shipped with the suite (QDStar's) and the operator's own (the
database, table dstar_reflectors), merged: the operator's copy wins, but a field it leaves
empty is taken from the shipped one (a copy renamed must not lose its dashboard).

A reflector is reached in one of two ways ("via"): INT, Terminal Mode with the radio's
internal gateway, by writing its TO (UR, e.g. /XLX214D) through a G3 server; EXT, the
suite's own gateway over USB (a later phase), linking "REF001 C".
"""

from __future__ import annotations

import json
import re
from importlib import resources

import sqlalchemy as sa

from ic705suite import db
from ic705suite.i18n import _

FIELDS = ("via", "to", "server", "name", "description", "notes", "dashboard", "api")
INHERITED = ("server", "description", "notes", "dashboard", "api")

metadata = sa.MetaData()
reflectors = sa.Table(
    "dstar_reflectors", metadata,
    sa.Column("to", sa.String(16), primary_key=True),
    *(sa.Column(field, sa.Text, nullable=False, default="") for field in FIELDS if field != "to"),
)


def normalize_to(text: str, via: str = "int") -> str:
    """INT: '/xlx214d' -> '/XLX214D' (the TO written to the radio); EXT: 'ref001c' ->
    'REF001 C' (the reflector the gateway links)."""
    text = re.sub(r"\s+", "", str(text).upper())
    if via == "ext":
        text = text.lstrip("/")
        if not re.fullmatch(r"[A-Z0-9]{4,7}[A-Z]", text):
            raise ValueError(_("Invalid reflector format (e.g. REF001 C)"))
        return text[:-1].ljust(7) + text[-1]
    if not text.startswith("/"):
        text = "/" + text
    if not re.fullmatch(r"/[A-Z0-9]{3,6}[A-Z]", text):
        raise ValueError(_("Invalid TO format (e.g. /XLX214D)"))
    return text


def split_to(to: str) -> tuple[str, str]:
    """'/XLX214D' or 'XLX214 D' -> ('XLX214', 'D')."""
    return to.lstrip("/")[:-1].strip(), to[-1]


def clean(item: dict) -> dict:
    out = {f: str(item.get(f, "") or "").strip() for f in FIELDS}
    if out["via"] not in ("int", "ext"):
        out["via"] = "int" if out["to"].startswith("/") else "ext"
    out["to"] = normalize_to(out["to"], out["via"])
    if out["via"] == "ext":
        out["server"] = ""
    return out


def shipped() -> list[dict]:
    text = resources.files("ic705suite.apps.dstar").joinpath("default_reflectors.json") \
        .read_text(encoding="utf-8")
    return [clean(i) for i in json.loads(text) if isinstance(i, dict) and i.get("to")]


class Registry:
    def __init__(self, url: str | None) -> None:
        self.engine = db.make_engine(url)
        metadata.create_all(self.engine)

    def own(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [clean(dict(r)) for r in conn.execute(sa.select(reflectors)).mappings()]

    def list(self, via: str | None = None) -> list[dict]:
        """Every reflector once, "own" telling the operator's from the shipped ones."""
        merged = [dict(i, own=True) for i in self.own()]
        by_to = {i["to"]: i for i in merged}
        for item in shipped():
            mine = by_to.get(item["to"])
            if mine is None:
                merged.append(dict(item, own=False))
            else:
                for field in INHERITED:
                    if not mine[field] and item[field]:
                        mine[field] = item[field]
        return [i for i in merged if via is None or i["via"] == via]

    def get(self, to: str) -> dict | None:
        return next((i for i in self.list() if i["to"] == to), None)

    def save(self, item: dict, old_to: str | None = None) -> dict:
        item = clean(item)
        with self.engine.begin() as conn:
            conn.execute(reflectors.delete().where(reflectors.c.to.in_(
                {item["to"], old_to or item["to"]})))
            conn.execute(reflectors.insert().values(**item))
        return item

    def delete(self, to: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(reflectors.delete().where(reflectors.c.to == to))
