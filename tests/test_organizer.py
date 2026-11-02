"""The Organizer (#53, ADR-0020): its rows, its keys and clicks, and Main's side with a fake
Organizer."""

import os
from pathlib import Path

import pytest

from allin.core.settings import MainSettings, WindowsSettings
from allin.ipc.messages import Close, OrganizerRow, OrganizerRows
from allin.main.events import (
    ConfirmationRequested,
    DraftReceived,
    InputReceived,
    MainCommandEntered,
    OrganizerActed,
    OrganizerClosed,
    OrganizerConnected,
)
from allin.main.organizer import command_for, row_title
from allin.ui.organizer_view import QUIT, Act, OrganizerView, fit, row_text, windows_list
from tests.test_dispatcher import FakeWindowLink, MainHarness, ended

WIDTH = 100


def row(sid: str, place: str = "view", **fields: object) -> OrganizerRow:
    return OrganizerRow.model_validate({"session_id": sid, "title": sid, "place": place, **fields})


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def command(text: str) -> MainCommandEntered:
    return MainCommandEntered(text)


def last_rows(link: FakeWindowLink) -> list[OrganizerRow]:
    return [m for m in link.sent if isinstance(m, OrganizerRows)][-1].rows


def all_rows(link: FakeWindowLink) -> list[list[OrganizerRow]]:
    return [m.rows for m in link.sent if isinstance(m, OrganizerRows)]


# The row model.


def test_a_title_left_empty_is_the_id_and_the_start_of_its_first_prompt():
    assert row_title("uno1", "Mi informe", "hola") == "Mi informe"
    assert row_title("uno1", "", "") == "uno1"
    assert row_title("uno1", "", "Cuenta  del\n1 al 3") == "uno1 · Cuenta del 1 al 3"
    long = row_title("uno1", "", "x" * 80)
    assert long.endswith("…") and len(long) == len("uno1 · ") + 40


def test_a_row_says_where_it_is_its_turn_its_boxes_its_context_and_its_supervisor():
    busy = row(
        "uno1",
        running=True,
        queued=2,
        asking=True,
        notice=True,
        context=400,
        limit=32000,
        supervisor="gpu",
    )
    assert row_text(busy) == (
        "uno1 — in view · turn running · 2 queued · asks a confirmation · notice"
        " · context 400 / 32k · server gpu"
    )
    assert row_text(row("dos2", "background")) == "dos2 — in the background · server base_url"


def test_a_title_with_control_characters_is_drawn_with_visible_marks():
    shown = row_text(OrganizerRow(session_id="uno1", title="a\x1b[2Jb\x07\x9bc", place="view"))
    assert "\x1b" not in shown and "\x07" not in shown and "\x9b" not in shown
    assert shown.startswith("uno1 a␛[2Jb␇�9Bc")


def test_the_list_has_the_open_sessions_and_below_the_closed_ones():
    rows = [row("uno1"), row("dos2", "background"), row("tres3", "closed")]
    assert windows_list(rows) == [
        "open sessions: 2",
        "  uno1 — in view · server base_url",
        "  dos2 — in the background · server base_url",
        "closed sessions: 1, most recent first",
        "  tres3 — closed · server base_url",
    ]


def test_each_action_is_the_command_main_of_its_name_or_nothing():
    assert command_for("open", "a", "background") == "/open a"
    assert command_for("open", "a", "closed") == "/open a"
    assert command_for("open", "a", "view") is None
    assert command_for("bg", "a", "view") == "/bg a"
    assert command_for("bg", "a", "closed") == "/open a --bg"
    assert command_for("bg", "a", "background") is None
    assert command_for("close", "a", "background") == "/close a"
    assert command_for("close", "a", "closed") is None


def test_fit_cuts_to_terminal_cells_and_pads():
    assert fit("abc", 5) == "abc  "
    assert fit("abcdef", 4) == "abc…"
    assert fit("日本語", 4) == "日… "  # a wide character takes two cells


# Keys and clicks, with a fake Main that only gathers what is sent.


def view_of(*rows: OrganizerRow) -> OrganizerView:
    view = OrganizerView()
    view.set_rows(list(rows))
    return view


def test_arrows_move_enter_brings_b_backgrounds_x_closes_and_esc_quits():
    view = view_of(row("uno1"), row("dos2", "background"))
    assert view.key("KEY_DOWN") is None
    assert view.key("\n") == Act("open", "dos2")
    assert view.key("b") == Act("bg", "dos2")
    assert view.key("x") == Act("close", "dos2")
    assert view.key("KEY_UP") is None and view.key("KEY_ENTER") == Act("open", "uno1")
    assert view.key("\x1b") == QUIT


def test_the_closed_sessions_are_folded_until_asked_for():
    view = view_of(row("uno1"), row("tres3", "closed"))
    assert not any("tres3" in line.text for line in view.lines(WIDTH))
    view.key("c")
    assert any("tres3" in line.text for line in view.lines(WIDTH))
    view.key("KEY_DOWN")
    view.key("KEY_DOWN")
    assert view.key("\n") == Act("open", "tres3")


def test_a_click_on_a_row_brings_it_and_on_its_buttons_backgrounds_or_closes_it():
    view = view_of(row("uno1"), row("dos2", "background"))
    lines = view.lines(WIDTH)
    y = next(n for n, line in enumerate(lines) if "dos2" in line.text)
    assert view.click(y, 5, WIDTH) == Act("open", "dos2")
    bg_x = lines[y].text.index("[bg]")
    close_x = lines[y].text.index("[x]")
    assert view.click(y, bg_x + 1, WIDTH) == Act("bg", "dos2")
    assert view.click(y, close_x, WIDTH) == Act("close", "dos2")
    assert view.click(0, 5, WIDTH) is None  # the header


def test_a_click_on_the_fold_unfolds_it():
    view = view_of(row("uno1"), row("tres3", "closed"))
    y = next(n for n, line in enumerate(view.lines(WIDTH)) if line.kind == "fold")
    assert view.click(y, 3, WIDTH) is None and view.unfolded


def test_only_the_line_under_the_mouse_is_highlighted():
    view = view_of(row("uno1"), row("dos2"))
    view.move(2)
    assert [line.hovered for line in view.lines(WIDTH)][:4] == [False, False, True, False]


def test_new_rows_keep_the_same_session_selected():
    view = view_of(row("uno1"), row("dos2"))
    view.key("KEY_DOWN")
    view.set_rows([row("cero"), row("uno1"), row("dos2")])
    assert view.key("\n") == Act("open", "dos2")


# Main's side, with a fake Organizer.


def test_windows_opens_the_organizer_with_a_row_per_open_session_and_the_closed_below(
    main: MainHarness,
):
    seen, hidden, closed = (main.create_session() for _ in range(3))
    main.profiles.add_session(main.profile, closed.id)  # as /new does: it is the Profile's
    main.run(
        command(f"/open {seen.id}"),
        command(f"/open {hidden.id} --bg"),
        command("/windows"),
        OrganizerConnected(),
    )
    [organizer] = main.organizers
    rows = last_rows(organizer)
    assert [(r.session_id, r.place) for r in rows] == [
        (seen.id, "view"),
        (hidden.id, "background"),
        (closed.id, "closed"),
    ]
    assert organizer.themes  # it is born with Main's Theme
    assert len(main.state.saved[-1].windows) == 1  # it is no Window and is never saved


def test_a_click_brings_a_session_and_without_room_sends_away_the_least_used(main: MainHarness):
    main.settings = MainSettings(session=main.settings.session, windows=WindowsSettings(max=1))
    first, second = main.create_session(), main.create_session()
    main.run(
        command(f"/open {first.id}"),
        command(f"/open {second.id}"),  # first goes to the background
        command("/windows"),
        OrganizerConnected(),
        OrganizerActed("open", first.id),
    )
    assert main.state.saved[-1].background == [second.id]
    assert [(r.session_id, r.place) for r in last_rows(main.organizers[0])][:2] == [
        (first.id, "view"),
        (second.id, "background"),
    ]


def test_its_actions_are_command_mains_of_a_human_in_mains_log(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.run(
        command(f"/open {a.id}"),
        command(f"/open {b.id}"),
        command("/windows"),
        OrganizerConnected(),
        OrganizerActed("bg", a.id),
        OrganizerActed("close", b.id),
        OrganizerActed("close", b.id),  # closed already: nothing to do
    )
    commands = [e.fields for e in main.main_log() if e.event == "command"]
    from_organizer = [f for f in commands if f["command"] in (f"/bg {a.id}", f"/close {b.id}")]
    assert [(f["command"], f["origin"], f["result"]) for f in from_organizer] == [
        (f"/bg {a.id}", "human", "ok"),
        (f"/close {b.id}", "human", "ok"),
    ]
    assert main.state.saved[-1].background == [a.id] and not main.state.saved[-1].windows


def test_a_turn_and_a_confirmation_show_in_the_organizer_as_they_happen(main: MainHarness):
    session = main.create_session()
    main.hold_turns = True
    main.run(
        command(f"/open {session.id}"),
        command("/windows"),
        OrganizerConnected(),
        InputReceived(session.id, "hola"),
        ConfirmationRequested(session.id, "Run it?", "really?"),
        ended(session),
        settle=True,
    )
    states = [(r[0].running, r[0].asking) for r in all_rows(main.organizers[0])]
    assert states[0] == (False, False)
    assert (True, False) in states and (True, True) in states
    assert states[-1] == (False, False)


def test_windows_again_replaces_it_and_closing_it_touches_no_session(main: MainHarness):
    session = main.create_session()
    main.run(
        command(f"/open {session.id}"),
        DraftReceived(session.id, ("borrador",)),
        command("/windows"),
        OrganizerConnected(),
        command("/windows"),
        OrganizerClosed(9000),  # the first one's xterm, gone: the second stays
        OrganizerConnected(),
        OrganizerClosed(9001),  # Esc on the second
    )
    first, second = main.organizers
    assert first.closed and Close() in first.sent
    assert not second.closed and last_rows(second)[0].session_id == session.id
    assert main.state.saved[-1].windows.keys() == {session.id}
    assert main.state.saved[-1].drafts == {session.id: ["borrador"]}
    chat_log = [e.event for e in main.main_log()]
    assert chat_log.count("organizer_opened") == 2 and chat_log.count("organizer_closed") == 2


def test_windows_list_writes_the_same_list_in_mains_chat(main: MainHarness):
    seen, closed = main.create_session(), main.create_session()
    main.profiles.add_session(main.profile, closed.id)
    chat = main.run(command(f"/open {seen.id}"), command("/windows list"))
    at = chat.index("open sessions: 1")
    assert chat[at + 1] == f"  {seen.id} — in view · server base_url"
    assert chat[at + 2] == "closed sessions: 1, most recent first"
    assert chat[at + 3] == f"  {closed.id} — closed · server base_url"


def test_only_a_human_opens_the_organizer(main: MainHarness):
    chat = main.run(MainCommandEntered("/windows", "session"))
    assert main.organizers == [] and any("human_only" in line for line in chat)


def test_the_title_setting_names_the_row(main: MainHarness):
    session = main.create_session()
    main.run(
        command(f"/session set {session.id} title=Informe"),
        command(f"/open {session.id}"),
        command("/windows"),
        OrganizerConnected(),
    )
    assert last_rows(main.organizers[0])[0].title == "Informe"


def test_the_closed_sessions_go_most_recent_first(main: MainHarness):
    old, recent = main.create_session(), main.create_session()
    for when, session in ((1_000, old), (2_000, recent)):
        main.profiles.add_session(main.profile, session.id)
        for path in (main.root / session.id).iterdir():
            os.utime(path, (when, when))
    main.run(command("/windows"), OrganizerConnected())
    assert [r.session_id for r in last_rows(main.organizers[0])] == [recent.id, old.id]
