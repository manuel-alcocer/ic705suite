# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for a self-contained ic705suite executable.

Produces one file that runs without Python installed. Textual loads its widgets
and stylesheets by name at runtime, so it is collected whole rather than left
to the import analyser; the suite's own stylesheets, translations, the phone
page and hamrlog's data are read through importlib.resources and travel as
package data.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH)
PROJECT_DIR = SPEC_DIR.parent.parent
SRC_DIR = PROJECT_DIR / "src"

textual_datas, textual_binaries, textual_hidden = collect_all("textual")

datas = textual_datas + collect_data_files(
    # .tcss, .po, the phone page and hamrlog's preseed and demo data.
    "ic705suite", excludes=["**/*.py", "**/__pycache__"]
)

hiddenimports = (
    textual_hidden
    # The launcher imports each application only when it is opened.
    + collect_submodules("ic705suite")
    # SQLAlchemy resolves its dialects by name.
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + collect_submodules("sqlalchemy.dialects.postgresql")
    # PostgreSQL, the recommended database: bundled when the build environment
    # has the postgres extra (uv sync --extra postgres), with its own libpq.
    # soundfile and psycopg bring their libraries through PyInstaller's hooks, and so does
    # sounddevice (PortAudio) on Windows; on Linux it uses the system's libportaudio.
    + ["psycopg", "psycopg_binary"]
)

a = Analysis(
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(SRC_DIR)],
    binaries=textual_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The API and metrics extras of hamrlog are not part of the suite's bundle.
    excludes=["tkinter", "matplotlib", "pytest", "fastapi", "uvicorn", "prometheus_client"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ic705suite",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # A terminal application: it must keep the console it was started from.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
