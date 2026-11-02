import pytest

from allin.ui.chat import Row
from allin.ui.selection import Point, Selection, autoscroll_rows, char_index

ROWS = [
    Row(None, "first line wraps"),
    Row(None, " here", joined=True),
    Row(None, "second"),
    Row(None, "third"),
]


def dragged(start: Point, end: Point) -> Selection:
    selection = Selection()
    selection.start(start)
    selection.extend(end)
    return selection


def test_a_drag_selects_from_where_it_started_to_where_it_is_both_included():
    assert dragged(Point(0, 6), Point(0, 9)).text(ROWS) == "line"


def test_a_drag_backwards_selects_the_same_text():
    assert dragged(Point(0, 9), Point(0, 6)).text(ROWS) == "line"


def test_wrapped_rows_join_without_a_break_and_lines_with_one():
    assert dragged(Point(0, 11), Point(2, 2)).text(ROWS) == "wraps here\nsec"


def test_a_point_past_the_end_of_its_row_takes_the_whole_row():
    assert dragged(Point(2, 0), Point(3, 40)).text(ROWS) == "second\nthird"


def test_a_click_without_a_drag_selects_nothing():
    selection = Selection()
    selection.start(Point(0, 3))
    assert not selection.active
    assert selection.text(ROWS) == ""
    assert selection.span(0, len(ROWS[0].text)) is None


def test_each_row_says_which_part_is_highlighted():
    selection = dragged(Point(0, 11), Point(2, 2))
    assert selection.span(0, 16) == (11, 16)
    assert selection.span(1, 5) == (0, 5)
    assert selection.span(2, 6) == (0, 3)
    assert selection.span(3, 5) is None


def test_clearing_drops_it():
    selection = dragged(Point(0, 1), Point(0, 4))
    selection.clear()
    assert not selection.active
    assert selection.text(ROWS) == ""


@pytest.mark.parametrize(
    ("text", "cell", "index"),
    [("hola", 2, 2), ("hola", 9, 4), ("日本語", 2, 1), ("日本語", 3, 1), ("", 0, 0)],
)
def test_a_cell_is_the_character_drawn_on_it(text: str, cell: int, index: int):
    assert char_index(text, cell) == index


@pytest.mark.parametrize(
    ("y", "rows"),
    [(5, 0), (2, 0), (11, 0), (1, -1), (-3, -5), (12, 1), (40, 10), (-40, -10)],
)
def test_autoscroll_goes_faster_the_further_out_the_pointer_is(y: int, rows: int):
    assert autoscroll_rows(y, top=2, height=10) == rows
