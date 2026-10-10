"""Entry point for the PyInstaller bundle.

A module of its own because PyInstaller needs a script file to analyse, and
``python -m ic705suite`` is not one.
"""

from ic705suite.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
