"""The tools view (F8): references and helpers, one per tab.

For now it holds the Q code. Its lists are read-only: the entry line is a
filter box, narrowed as it is typed, and the arrows move the cursor over the
list while the box keeps the keyboard.
"""

from __future__ import annotations

from ..core import qcodes
from ..i18n import N_, _
from .inventory import Item, Kind


class QCodeKind(Kind):
    key = "qcodes"
    title = N_("Q code")
    guide = (
        N_("Q CODE   a statement; followed by «?», the question"),
        N_("Type in the box to narrow the list: the letters or any word of the meaning"),
        N_("↑↓ move through the list"),
    )
    fields = ("filter",)
    columns = (
        (N_("CODE"), 6),
        (N_("MEANING"), 58),
        (N_("QUESTION"), None),
    )
    can_add = False
    can_delete = False
    browses = False
    count_text = N_("{total} codes")

    def items(self, query: str = "") -> list[Item]:
        # Ids follow the whole list, so a code keeps its id however narrowed.
        return [
            Item(
                qcodes.QCODES.index(code) + 1,
                code.code,
                (code.code, _(code.statement), _(code.question)),
                (
                    code.code,
                    _(code.statement),
                    f"{code.code}?  {_(code.question)}",
                ),
                locked=False,
            )
            for code in qcodes.search(query)
        ]

    def total(self, query: str = "") -> int:
        return len(qcodes.QCODES)


#: The tabs of the tools view, in order.
TOOL_KINDS: tuple[Kind, ...] = (QCodeKind(),)
