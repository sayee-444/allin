from allin.ui.viewport import Viewport


def test_follows_the_last_lines_while_content_grows():
    view = Viewport()
    assert view.top(total=5, rows=10) == 0
    assert view.top(total=100, rows=10) == 90
    assert view.top(total=105, rows=10) == 95


def test_scrolling_up_stops_following_new_lines():
    view = Viewport()
    view.top(total=100, rows=10)
    view.scroll(-5, total=100, rows=10)
    assert view.top(total=100, rows=10) == 85
    assert view.top(total=110, rows=10) == 85


def test_scrolling_back_to_the_bottom_resumes_following():
    view = Viewport()
    view.scroll(-5, total=100, rows=10)
    view.scroll(+100, total=110, rows=10)
    assert view.top(total=110, rows=10) == 100
    assert view.top(total=120, rows=10) == 110


def test_scroll_is_clamped_to_the_content():
    view = Viewport()
    view.scroll(-1000, total=100, rows=10)
    assert view.top(total=100, rows=10) == 0


def test_growing_the_pad_never_shows_past_the_end():
    view = Viewport()
    view.scroll(-5, total=100, rows=10)
    assert view.top(total=100, rows=30) == 70
