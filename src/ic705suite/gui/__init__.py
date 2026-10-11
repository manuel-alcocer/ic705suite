"""The graphical client (ic705suite --gui): the terminal suite's screens with Qt, the same
menus, options and keys, plus the mouse. Needs the "gui" extra (PySide6, qasync).

One page per terminal screen (dashboard, view, cw, swr, log, packet), each mirroring its
Textual App (``Page.TUI``) and reading its key bindings (keys.py).
"""
