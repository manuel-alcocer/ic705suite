"""Translations: the .po parser, language choice, and complete catalogs."""

from __future__ import annotations

import ast
import pathlib

import pytest

from ic705suite.apps.log import i18n

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "src" / "ic705suite" / "apps" / "log"


def marked_strings() -> dict[str, str]:
    """Every literal passed to _() or N_() in the code, with where it is."""
    found: dict[str, str] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("_", "N_")
                and node.args
            ):
                arg = node.args[0]
                # _(LABELS[key]) translates text marked elsewhere with N_();
                # only built strings are wrong, they can never be in a catalog.
                assert not isinstance(arg, (ast.JoinedStr, ast.BinOp)), (
                    f"{path}:{node.lineno}: _() needs a literal string, not a built one"
                )
                if not isinstance(arg, ast.Constant):
                    continue
                assert isinstance(arg.value, str), f"{path}:{node.lineno}: not a string"
                found.setdefault(arg.value, f"{path.relative_to(SOURCE)}:{node.lineno}")
    return found


def test_every_marked_string_has_a_spanish_translation():
    catalog = i18n.load_catalog("es")
    missing = [f"{where}: {text!r}" for text, where in marked_strings().items()
               if text not in catalog]
    assert not missing, "Sin traducir:\n" + "\n".join(missing)


def test_translations_keep_the_placeholders():
    import string

    def fields(text: str) -> set[str]:
        return {name for _, name, _, _ in string.Formatter().parse(text) if name}

    catalog = i18n.load_catalog("es")
    wrong = [msgid for msgid, msgstr in catalog.items() if fields(msgid) != fields(msgstr)]
    assert not wrong, wrong


def test_po_parser_reads_continued_and_escaped_strings():
    text = '''
# comment
msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgid "Band {band}"
msgstr "Banda {band}"

msgid ""
"Two "
"lines"
msgstr ""
"Dos "
"líneas \\"citadas\\""

msgid "Untranslated"
msgstr ""
'''
    assert i18n.parse_po(text) == {
        "Band {band}": "Banda {band}",
        "Two lines": 'Dos líneas "citadas"',
    }


@pytest.mark.parametrize(
    ("env", "expected"),
    [({"HAMRLOG_LANG": "en", "LANG": "es_ES.UTF-8"}, "en"),
     ({"LANG": "es_ES.UTF-8"}, "es"),
     ({"LANG": "de_DE.UTF-8"}, "en")],
)
def test_language_comes_from_the_override_then_the_locale(monkeypatch, env, expected):
    for name in ("HAMRLOG_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: (None, None))
    monkeypatch.setattr(i18n, "_windows_ui_language", lambda: "")
    assert i18n.detect_language() == expected


@pytest.mark.parametrize(
    ("value", "code"),
    [("es_ES.UTF-8", "es"), ("es-ES", "es"), ("Spanish_Spain.1252", "es"),
     ("English_United States.1252", "en"), ("C.UTF-8", "c"), ("", "")],
)
def test_language_codes_from_unix_and_windows_names(value, code):
    assert i18n.language_code(value) == code


def test_windows_takes_the_language_of_its_interface(monkeypatch):
    """Windows seldom sets LANG: the user interface language decides."""
    for name in ("HAMRLOG_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(i18n, "_windows_ui_language", lambda: "es_ES")
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: ("English_United States", "1252"))
    assert i18n.detect_language() == "es"


def test_english_is_the_source_and_spanish_translates():
    try:
        i18n.set_language("en")
        assert i18n._("Band {band}") == "Band {band}"
        i18n.set_language("es")
        assert i18n._("No such text") == "No such text"
    finally:
        i18n.set_language("es")
