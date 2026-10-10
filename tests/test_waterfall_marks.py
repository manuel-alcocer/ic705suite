"""The skimmer's stations on the waterfall: a row of their own, never over the scale."""

import asyncio

import numpy as np
from textual.app import App

from ic705suite.ui.waterfall import Waterfall

START, END = 7_003_400, 7_006_900
# Sixteen stations as crowded as on 40 m in a contest (the screenshot that asked for this).
CROWD = [7_003_710, 7_003_830, 7_004_230, 7_004_960, 7_005_180, 7_005_490, 7_005_660,
         7_005_730, 7_005_830, 7_005_900, 7_006_210, 7_006_370, 7_006_550, 7_006_650,
         7_006_700, 7_006_800]


def _render(setup) -> list[str]:
    lines: list[str] = []

    class Demo(App):
        def compose(self):
            yield Waterfall()

    async def run():
        app = Demo()
        async with app.run_test(size=(100, 12)) as pilot:
            scope = app.query_one(Waterfall)
            scope.spectrum_rows = 2
            scope.add_frame(np.zeros(400), START, END)
            setup(scope)
            await pilot.pause()
            lines.extend(scope.render_line(y).text for y in range(4))

    asyncio.run(run())
    return lines


def test_marks_have_their_own_row_and_leave_the_scale_alone():
    plain = _render(lambda scope: None)

    def crowded(scope):
        scope.mark_row = True
        scope.view.marks = [(hz, str(n)) for n, hz in enumerate(CROWD, start=1)]

    marks, spectrum, _, scale = _render(crowded)
    assert scale == plain[2]                        # the scale reads as without stations
    assert spectrum.strip() == ""
    assert marks.count("▼") == len(CROWD)          # every station has its tick


def test_labels_never_run_together_and_the_chosen_one_always_shows():
    def crowded(scope):
        scope.mark_row = True
        scope.view.marks = [(hz, str(n)) for n, hz in enumerate(CROWD, start=1)]
        scope.view.chosen = CROWD[14]               # station 15, in the thick of it
        scope.view.calling = {CROWD[3]: "cq"}

    marks = _render(crowded)[0]
    labels = marks.replace("▼", " ").split()
    assert "15" in labels and "4" in labels         # chosen, then calling CQ, come first
    # Every label is a whole station number: none glued to another ("78", "11516").
    assert all(label.isdigit() and 1 <= int(label) <= len(CROWD) for label in labels)
    assert len(labels) == len(set(labels))
