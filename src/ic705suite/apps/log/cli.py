"""Command line entry point.

Beyond launching the TUI, the subcommands make the log usable from a script or
a cron job, which is the same surface a future API would expose.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__, i18n
from .core import contacts as contact_files
from .db.session import default_database_url, init_engine
from .i18n import _
from .paths import config_dir, data_dir, export_dir


def _configure_console() -> None:
    """Make the Windows console speak UTF-8.

    PowerShell and cmd.exe still start in a legacy code page on many systems,
    which mangles accented text and the box drawing characters the interface
    relies on. Switching the console to UTF-8 and reconfiguring the Python
    streams fixes both. On Linux and macOS this is a no-op.
    """
    if sys.platform != "win32":
        return

    try:
        import ctypes

        # 65001 is the UTF-8 code page.
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001 - best effort, never fatal
        pass

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - redirected streams may refuse
                pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hamrlog",
        description=_("Amateur radio logbook for the console (Linux, Windows and macOS)."),
    )
    parser.add_argument("--version", action="version", version=f"hamrlog {__version__}")
    parser.add_argument(
        "--database",
        metavar="URL",
        help=_("SQLAlchemy URL of the database (local SQLite by default)."),
    )
    parser.add_argument(
        "-d",
        "--demo",
        action="store_true",
        help=_(
            "Open a demo log with invented data. Nothing is saved: every demo "
            "starts with the same data."
        ),
    )
    parser.add_argument(
        "-l",
        "--lang",
        choices=i18n.AVAILABLE,
        help=_("Language of the interface (English in the demo unless given)."),
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help=_("Open the text interface (default)."))
    sub.add_parser("info", help=_("Show paths, database and version."))
    sub.add_parser(
        "build-demo", help=_("Build the demo database again (the installer does it).")
    )

    export = sub.add_parser("export", help=_("Export the log without opening the interface."))
    export.add_argument("path", nargs="?", help=_("Destination file."))
    export.add_argument(
        "--format", choices=("adif", "csv"), default="adif", help=_("Output format.")
    )

    importer = sub.add_parser("import", help=_("Import an ADIF file."))
    importer.add_argument("path", help=_(".adi file to import."))
    importer.add_argument(
        "--operator", required=True, help=_("Callsign of the target operator.")
    )
    importer.add_argument(
        "--force-operator",
        action="store_true",
        help=_("Assign every contact to the given operator, ignoring the one in the file."),
    )

    metrics = sub.add_parser("metrics", help=_("Start only the Prometheus exporter."))
    metrics.add_argument("--port", type=int, default=9119, help=_("HTTP port."))

    contacts = sub.add_parser("contacts", help=_("Contact address book."))
    contacts_sub = contacts.add_subparsers(dest="contacts_command", required=True)

    contacts_import = contacts_sub.add_parser(
        "import", help=_("Import a contact list (CSV or JSON, format detected).")
    )
    contacts_import.add_argument("path", help=_("File to import."))
    contacts_import.add_argument(
        "--country", default="", help=_("Import only the given country, e.g. Spain.")
    )
    contacts_import.add_argument(
        "--no-update",
        action="store_true",
        help=_("Leave alone the contacts already in the address book."),
    )

    contacts_export = contacts_sub.add_parser("export", help=_("Export the address book."))
    contacts_export.add_argument("path", nargs="?", help=_("Destination file."))
    contacts_export.add_argument(
        "--format",
        choices=tuple(contact_files.EXPORT_FORMATS),
        default="hamrlog",
        help=_("Output format."),
    )

    contacts_list = contacts_sub.add_parser("list", help=_("Search the address book."))
    contacts_list.add_argument("query", nargs="?", default="", help=_("Text to search for."))
    contacts_list.add_argument("--limit", type=int, default=50, help=_("Maximum number of rows."))

    return parser


def _run_demo(language: str | None) -> int:
    from . import demo
    from .tui.app import HamrlogApp

    with demo.session(language):
        i18n.set_language(language or demo.DEFAULT_LANGUAGE)
        HamrlogApp(demo=True).run(mouse=False)
    return 0


def _cmd_build_demo() -> int:
    from . import demo

    path = demo.build_template()
    print(_("Demo database ready: {path}").format(path=path))
    return 0


def _cmd_info() -> int:
    print(f"hamrlog {__version__}")
    print(_("Database      : {value}").format(value=default_database_url()))
    print(_("Data          : {value}").format(value=data_dir()))
    print(_("Configuration : {value}").format(value=config_dir()))
    print(_("Exports       : {value}").format(value=export_dir()))
    return 0


def _cmd_export(path: str | None, output_format: str) -> int:
    from .core import transfer

    if output_format == "adif":
        target, count = transfer.export_adif(path)
    else:
        target, count = transfer.export_csv(path)
    print(_("Exported {count} contacts to {path}").format(count=count, path=target))
    return 0


def _cmd_import(path: str, operator: str, force_operator: bool) -> int:
    from .core import transfer
    from .core.services import OperatorService

    found = OperatorService.get_by_callsign(operator)
    if found is None:
        print(
            _("There is no operator {callsign}.").format(callsign=operator.upper()),
            file=sys.stderr,
        )
        return 1
    report = transfer.import_adif(
        path, operator_id=found.id, respect_file_operator=not force_operator
    )
    print(report.summary)
    for error in report.errors[:10]:
        print(f"  {error}", file=sys.stderr)
    return 0


def _cmd_contacts(args: argparse.Namespace) -> int:
    """Address book subcommands: import, export and search."""
    from .core import transfer
    from .core.services import ContactService

    if args.contacts_command == "import":
        summary = transfer.import_contacts(
            args.path,
            update_existing=not args.no_update,
            country_filter=args.country,
        )
        print(_("Detected format: {name}").format(name=summary.format_name))
        print(summary.text)
        for warning in summary.warnings:
            print("  " + _("warning: {warning}").format(warning=warning), file=sys.stderr)
        return 0 if (summary.created or summary.updated) else 1

    if args.contacts_command == "export":
        target, count = transfer.export_contacts(
            args.path, export_format=args.format
        )
        print(_("Exported {count} contacts to {path}").format(count=count, path=target))
        if args.format == "anytone":
            print(_("Contacts without a DMR ID are not exported: the radio ignores them."))
        return 0

    rows = ContactService.search(args.query, limit=args.limit)
    if not rows:
        print(_("No results.") if args.query else _("The address book is empty."))
        return 0
    print(
        f"{_('CALLSIGN'):<12} {_('NAME'):<24} {_('CITY'):<16} "
        f"{'DMR ID':<10} {'QSO':>4}"
    )
    for row in rows:
        print(
            f"{row.callsign:<12} {row.full_name[:24]:<24} {row.city[:16]:<16} "
            f"{row.dmr_id or '':<10} {row.qso_count or '':>4}"
        )
    summary = _("{shown} of {total} contacts.").format(
        shown=len(rows), total=ContactService.count()
    )
    print(f"\n{summary}")
    return 0


def _cmd_metrics(port: int) -> int:
    from .api.metrics import serve_forever

    return serve_forever(port)


def _offer_update(argv: list[str] | None) -> int | None:
    from . import updater

    if not updater.should_check():
        return None
    return updater.offer_update(list(sys.argv[1:] if argv is None else argv))


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns the process exit code."""
    _configure_console()
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"

    if args.lang:
        # Through the environment, which wins over the settings, for this run.
        os.environ[i18n.LANG_ENV] = args.lang
        i18n.set_language(args.lang)
    if args.demo and (command != "run" or args.database):
        parser.error(_("--demo only opens the interface, on its own database."))
    if command == "run":
        # In the terminal, before the interface takes it over.
        exit_code = _offer_update(argv)
        if exit_code is not None:
            return exit_code
    if args.demo:
        return _run_demo(args.lang)
    if command == "build-demo":
        return _cmd_build_demo()

    init_engine(args.database)
    if command == "info":
        return _cmd_info()
    if command == "export":
        return _cmd_export(args.path, args.format)
    if command == "import":
        return _cmd_import(args.path, args.operator, args.force_operator)
    if command == "metrics":
        return _cmd_metrics(args.port)
    if command == "contacts":
        return _cmd_contacts(args)

    from .tui.app import HamrlogApp

    # Keyboard only: the mouse is not captured, so the terminal keeps it for
    # selecting and copying text.
    HamrlogApp(database_url=args.database).run(mouse=False)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
