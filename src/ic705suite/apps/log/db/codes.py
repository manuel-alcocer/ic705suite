"""The operator's identifiers for radios, antennas and power supplies.

Each one gets a code made of a letter and a number: E0001 for a radio
(«emisora»), A0001 for an antenna, S0001 for a power supply. The database
keeps its own autoincrement id; the code is what the operator reads and types,
and it is unique, enforced by a unique index.

Codes are handed out in order and never reused while the highest one exists.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, select, text

from .models import Antenna, PowerSupply, Station

#: Letter of each kind's codes.
PREFIXES: dict[type, str] = {Station: "E", Antenna: "A", PowerSupply: "S"}

#: Digits after the letter; more appear once the count outgrows them.
DIGITS = 4


def format_code(prefix: str, number: int) -> str:
    return f"{prefix}{number:0{DIGITS}d}"


def _highest(codes: Any, prefix: str) -> int:
    highest = 0
    for code in codes:
        if code and code.startswith(prefix) and code[len(prefix) :].isdigit():
            highest = max(highest, int(code[len(prefix) :]))
    return highest


def next_code(executor: Any, model: type) -> str:
    """The next free code of a kind; ``executor`` is a session or connection."""
    prefix = PREFIXES[model]
    codes = executor.execute(select(model.code).where(model.code.is_not(None))).scalars()
    return format_code(prefix, _highest(codes, prefix) + 1)


def assign_missing_codes(engine: Engine) -> int:
    """Give a code to every row without one, oldest first, and index them.

    Runs on every start, so rows written by an older version, by an upgrade
    or by the catalog loader all end up with a code. The unique index is
    created here too because adding the column to an existing table does not
    create it.

    Returns:
        How many codes were assigned.
    """
    assigned = 0
    with engine.begin() as connection:
        for model, prefix in PREFIXES.items():
            table = model.__table__
            highest = _highest(
                connection.execute(select(table.c.code)).scalars(), prefix
            )
            missing = connection.execute(
                select(table.c.id).where(table.c.code.is_(None)).order_by(table.c.id)
            ).scalars().all()
            for row_id in missing:
                highest += 1
                connection.execute(
                    table.update()
                    .where(table.c.id == row_id)
                    .values(code=format_code(prefix, highest))
                )
            assigned += len(missing)
            connection.execute(
                text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS ix_{table.name}_code "
                    f"ON {table.name} (code)"
                )
            )
    return assigned
