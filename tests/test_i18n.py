"""The suite's Spanish catalog covers every marked string (hamrlog and hamrpkt keep their own)."""

import ast
import re
from pathlib import Path

from ic705suite import i18n

SRC = Path(__file__).parents[1] / "src" / "ic705suite"
OWN_CATALOGS = ("apps/log", "apps/packet")


def _marked_strings() -> set[str]:
    found = set()
    for path in SRC.rglob("*.py"):
        if any(part in path.relative_to(SRC).as_posix() for part in OWN_CATALOGS):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in ("_", "N_") and node.args \
                    and isinstance(node.args[0], ast.Constant):
                found.add(node.args[0].value)
    return found - {"{text}", "{error}"}  # pass-through templates


def test_spanish_catalog_is_complete_and_consistent():
    catalog = i18n.load_catalog("es")
    missing = sorted(_marked_strings() - set(catalog))
    assert not missing, "untranslated strings:\n" + "\n".join(missing)
    for msgid, msgstr in catalog.items():
        assert sorted(re.findall(r"{\w+}", msgid)) == sorted(re.findall(r"{\w+}", msgstr)), msgid
