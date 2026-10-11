"""Running a Textual application's own code in a Qt page.

hamrlog and hamrpkt keep their behaviour in their App classes, which ask for their widgets by
class (``self.query_one(EntryPanel)``) and check them with ``isinstance``. A page borrows those
methods with the widget classes in their globals replaced by Qt classes offering the same
methods, so the graphical client runs the very same logic, not a copy of it.
"""

from __future__ import annotations

import types


def rebound(function: types.FunctionType, replacements: dict[str, type],
            owner: str) -> types.FunctionType:
    """`function` with `replacements` in its globals (the module's are left alone)."""
    if function.__closure__:                   # super() and the like: not borrowable
        raise TypeError(f"{function.__qualname__} cannot be borrowed")
    namespace = dict(function.__globals__)
    namespace.update(replacements)
    copy = types.FunctionType(function.__code__, namespace, function.__name__,
                              function.__defaults__)
    copy.__kwdefaults__ = function.__kwdefaults__
    copy.__doc__ = function.__doc__
    copy.__qualname__ = f"{owner}.{function.__name__}"
    return copy


def borrow(source: type, target: type, replacements: dict[str, type],
           own: set[str] = frozenset()) -> None:
    """Give `target` every method and property of `source` (its own class body, not its
    bases') that `target` does not define itself and that is not in `own`."""
    for name, value in vars(source).items():
        if name.startswith("__") or name in own or name in vars(target):
            continue
        if isinstance(value, types.FunctionType):
            setattr(target, name, rebound(value, replacements, target.__name__))
        elif isinstance(value, property):
            setattr(target, name, property(rebound(value.fget, replacements, target.__name__)))
