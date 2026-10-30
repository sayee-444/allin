from allin.ui.draft import DraftReporter


def reporter() -> tuple[DraftReporter, list[tuple[str, ...]]]:
    sent: list[tuple[str, ...]] = []
    return DraftReporter(sent.append, delay=0.5), sent


def test_the_input_is_reported_once_it_stays_unchanged_for_the_delay():
    draft, sent = reporter()
    draft.update(("h",), now=0.0)
    draft.update(("ho",), now=0.3)
    draft.update(("ho",), now=0.7)
    assert sent == []
    draft.update(("ho",), now=0.8)
    draft.update(("ho",), now=5.0)
    assert sent == [("ho",)]


def test_an_input_emptied_after_a_report_is_reported_empty():
    draft, sent = reporter()
    draft.update(("hola",), now=0.0)
    draft.update(("hola",), now=1.0)
    draft.update((), now=2.0)
    draft.update((), now=3.0)
    assert sent == [("hola",), ()]


def test_a_new_input_is_reported_once_even_if_empty():
    draft, sent = reporter()
    draft.update((), now=0.0)
    draft.update((), now=10.0)
    assert sent == [()]


def test_text_main_put_back_and_cleared_at_once_is_reported_cleared():
    draft, sent = reporter()
    draft.update((), now=0.0)
    draft.update(("restored",), now=0.1)  # Main's InputRestore
    draft.update((), now=0.2)  # deleted before it was ever reported
    draft.update((), now=1.0)
    assert sent == [()]


def test_going_back_to_what_was_reported_reports_nothing():
    draft, sent = reporter()
    draft.update(("hola",), now=0.0)
    draft.update(("hola",), now=1.0)
    draft.update(("hol",), now=2.0)
    draft.update(("hola",), now=2.1)
    draft.update(("hola",), now=3.0)
    assert sent == [("hola",)]


def test_waiting_blocks_are_part_of_the_draft():
    draft, sent = reporter()
    draft.update(("a1", "typing"), now=0.0)
    draft.update(("a1", "typing"), now=1.0)
    assert sent == [("a1", "typing")]


def test_a_draft_can_be_reported_at_once():
    draft, sent = reporter()
    draft.update(("a1", "typing"), now=0.0)
    draft.report_now(("typing",), now=0.1)
    draft.update(("typing",), now=5.0)
    assert sent == [("typing",)]
