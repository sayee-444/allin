"""A kernel that cannot confine tools in full: a Notice when Main starts and a permanent mark on
every border, like --devastation's (ADR-0016, #100)."""

from pathlib import Path

import pytest

from allin.confinement.support import ConfinementSupport
from allin.ipc.messages import ConfinementGap, Coverage
from allin.main.events import MainCommandEntered, WindowConnected
from allin.ui.marks import DEVASTATION_MARK
from allin.ui.screen import ShowBox, TitleMark
from allin.window.__main__ import MainListener, WindowRecord
from tests.test_dispatcher import MainHarness


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def marks(main: MainHarness) -> list[str]:
    return [update.text for update in main.main_screen if isinstance(update, TitleMark)]


def notices(main: MainHarness) -> list[ShowBox]:
    return [update for update in main.main_screen if isinstance(update, ShowBox)]


@pytest.mark.parametrize(
    ("abi", "missing"),
    [
        (0, ["files", "network", "signals and abstract sockets"]),
        (3, ["network", "signals and abstract sockets"]),
        (5, ["signals and abstract sockets"]),
    ],
)
def test_what_each_abi_leaves_out(abi: int, missing: list[str]):
    assert ConfinementSupport(abi=abi).missing == tuple(missing)


def test_a_full_abi_misses_nothing():
    assert ConfinementSupport(abi=6).missing == ()
    assert ConfinementSupport(abi=8).missing == ()


def test_without_network_rules_main_tells_on_start_and_marks_its_chat(main: MainHarness):
    main.confinement = ConfinementSupport(abi=3)
    main.run()
    [notice] = notices(main)
    assert notice.box.kind == "notice"
    assert "network" in notice.box.text and "ABI 3" in notice.box.text
    [mark] = marks(main)
    assert "network" in mark


def test_each_window_gets_the_mark_when_it_connects(main: MainHarness):
    main.confinement = ConfinementSupport(abi=5)
    session = main.create_session()
    main.run(MainCommandEntered(f"/open {session.id}"), WindowConnected(session.id), settle=True)
    [window] = main.windows(session.id)
    gaps = [m for m in window.sent if isinstance(m, ConfinementGap)]
    assert gaps == [ConfinementGap(missing=["signals and abstract sockets"])]


def test_a_full_kernel_shows_no_notice_and_no_mark(main: MainHarness):
    main.confinement = ConfinementSupport(abi=8)
    session = main.create_session()
    main.run(MainCommandEntered(f"/open {session.id}"), WindowConnected(session.id), settle=True)
    assert notices(main) == []
    assert all(text == "" for text in marks(main))
    [window] = main.windows(session.id)
    assert not any(isinstance(m, ConfinementGap) for m in window.sent)


def test_the_mark_stays_beside_devastations_and_after_it_goes_off(main: MainHarness):
    main.confinement = ConfinementSupport(abi=3)
    main.run(
        MainCommandEntered("/devastation on"),
        MainCommandEntered("/devastation off"),
        settle=True,
    )
    lit, off = marks(main)[-2:]
    assert DEVASTATION_MARK in lit and "network" in lit
    assert DEVASTATION_MARK not in off and "network" in off


def test_a_window_shows_both_marks_on_its_border(tmp_path: Path):
    shown: list[object] = []
    listener = MainListener(shown.append, WindowRecord(tmp_path / "window.jsonl"))
    listener.on_message(ConfinementGap(missing=["network"]))
    listener.on_message(Coverage(covered=True))
    [*_, last] = [update for update in shown if isinstance(update, TitleMark)]
    assert DEVASTATION_MARK in last.text and "network" in last.text
    listener.on_message(Coverage(covered=False))
    [*_, last] = [update for update in shown if isinstance(update, TitleMark)]
    assert DEVASTATION_MARK not in last.text and "network" in last.text
