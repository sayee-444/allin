"""``/profile switch <name> [--now]``: changing Profile without restarting Main (#31, ADR-0010)."""

from pathlib import Path

import pytest

from allin.core.errors import TurnCancelled
from allin.core.settings import MainSettings, SessionSettings
from allin.ipc.messages import Close, InputRestore
from allin.main.events import (
    ConfirmationRequested,
    DraftReceived,
    InputReceived,
    MainCommandEntered,
    TurnEnded,
    WindowClosed,
)
from allin.main.profiles import Profile
from allin.main.settings_files import MainSettingsFiles
from allin.main.state import MainStateStore
from tests.pieces import approve_sysprompt
from tests.test_dispatcher import SETTINGS, MainHarness, ended, restored

GPU_TOML = 'model = "m.gguf"\ncontext = 8192\nport = 9101\n'


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.slot_locks_dir.mkdir(parents=True, exist_ok=True)
    (harness.slot_locks_dir / "gpu.toml").write_text(GPU_TOML)
    return harness


def other_profile(main: MainHarness, name: str = "tesis") -> Profile:
    return main.profiles.create(name, main.profile)


def switch(name: str, now: bool = False) -> MainCommandEntered:
    return MainCommandEntered(f"/profile switch {name}" + (" --now" if now else ""))


def switched_out(main: MainHarness, session_id: str) -> int:
    """How many times the switch closed the Session's Window."""
    entries = main.session_log(main.sessions.load(session_id))
    return sum(
        entry.event == "window_closed" and entry.fields.get("reason") == "profile switched"
        for entry in entries
    )


def prompts(main: MainHarness) -> list[str]:
    return [job.request.input[-1]["content"] for job in main.held]


def test_a_to_b_to_a_leaves_a_as_it_was(main: MainHarness):
    a1, a2 = main.create_session(), main.create_session()
    other_profile(main)
    main.server_up = False  # the queue waits, so it is there to be kept
    main.run(
        MainCommandEntered(f"/open {a1.id}"),
        MainCommandEntered(f"/open {a2.id}"),
        DraftReceived(a1.id, ("typing", "later")),
        InputReceived(a2.id, "queued"),
        switch("tesis"),
        settle=True,
    )
    [first_a1] = main.windows(a1.id)
    assert switched_out(main, a1.id) == switched_out(main, a2.id) == 1
    assert Close() in first_a1.sent
    assert main.profiles.open(None).name == "tesis"
    saved = MainStateStore(main.profile.state).load()
    assert list(saved.windows) == [a1.id, a2.id]
    assert saved.drafts == {a1.id: ["typing", "later"]}
    assert [prompt.text for prompt in saved.queue] == ["queued"]

    main.server_up = True
    main.hold_turns = True
    main.run(switch("default"), settle=True)
    [_, again_a1], [_, _] = main.windows(a1.id), main.windows(a2.id)
    assert restored(again_a1) == ["typing", "later"]
    assert switched_out(main, a2.id) == 1
    assert prompts(main) == ["queued"]  # the queue goes on where it was
    assert main.profiles.open(None).name == "default"


def test_the_incoming_profile_opens_its_own_windows_and_drafts(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    other_profile(main)
    main.run(switch("tesis"), settle=True)
    main.run(
        MainCommandEntered(f"/open {b.id}"),  # b joins tesis only
        DraftReceived(b.id, ("in tesis",)),
        switch("default"),
        settle=True,
    )
    assert switched_out(main, b.id) == 1
    main.run(MainCommandEntered(f"/open {a.id}"), switch("tesis"), settle=True)
    assert restored(main.windows(b.id)[-1]) == ["in tesis"]
    assert switched_out(main, b.id) == 1
    assert switched_out(main, a.id) == 1


def test_a_shared_session_keeps_its_window_open_through_the_switch(main: MainHarness):
    shared, mine = main.create_session(), main.create_session()
    tesis = other_profile(main)
    main.profiles.add_session(tesis, shared.id)
    main.run(
        MainCommandEntered(f"/open {shared.id}"),
        MainCommandEntered(f"/open {mine.id}"),
        DraftReceived(shared.id, ("half",)),
        switch("tesis"),
        settle=True,
    )
    [_], [_] = main.windows(shared.id), main.windows(mine.id)
    assert switched_out(main, shared.id) == 0
    assert switched_out(main, mine.id) == 1
    saved_tesis = MainStateStore(tesis.state).load()
    assert list(saved_tesis.windows) == [shared.id]
    assert saved_tesis.drafts == {shared.id: ["half"]}


def test_a_shared_windows_draft_keeps_what_it_holds_and_gets_back_what_the_profile_kept(
    main: MainHarness,
):
    shared = main.create_session()
    tesis = other_profile(main)
    main.run(
        MainCommandEntered(f"/open {shared.id}"),
        DraftReceived(shared.id, ("from default",)),
        switch("tesis"),  # not shared yet: its Window closes with default's Draft kept
        MainCommandEntered(f"/open {shared.id}"),  # now it is
        DraftReceived(shared.id, ("from tesis",)),
        switch("default"),
        settle=True,
    )
    assert main.profiles.sessions(tesis) == [shared.id]
    # It holds "from tesis" as typed; default's block is sent back ahead of it.
    assert restored(main.windows(shared.id)[-1]) == ["from default"]
    saved = MainStateStore(main.profile.state).load()
    assert saved.drafts == {shared.id: ["from default", "from tesis"]}


def test_a_shared_sessions_queued_prompts_stay_in_the_queue(main: MainHarness):
    shared, mine = main.create_session(), main.create_session()
    tesis = other_profile(main)
    main.profiles.add_session(tesis, shared.id)
    main.server_up = False
    main.run(
        MainCommandEntered(f"/open {shared.id}"),
        MainCommandEntered(f"/open {mine.id}"),
        InputReceived(shared.id, "shared one"),
        InputReceived(mine.id, "mine"),
        switch("tesis"),
        settle=True,
    )
    left = MainStateStore(main.profile.state).load()
    assert [prompt.text for prompt in left.queue] == ["mine"]
    assert [prompt.text for prompt in MainStateStore(tesis.state).load().queue] == ["shared one"]


def test_without_now_a_running_turn_ends_before_the_switch(main: MainHarness):
    a = main.create_session()
    other_profile(main)
    main.hold_turns = True
    before_it_ends: list[tuple[int, str]] = []
    chat = main.run(
        MainCommandEntered(f"/open {a.id}"),
        InputReceived(a.id, "a1"),
        switch("tesis"),
        lambda: before_it_ends.append((switched_out(main, a.id), main.profiles.open(None).name)),
        ended(a),
        settle=True,
    )
    assert before_it_ends == [(0, "default")], "it waits for the Turn"
    assert any("waiting for 1 turn to end" in line for line in chat)
    assert main.held[0].cancellation.reason is None
    conversation = main.sessions.load_conversation(a)
    assert conversation is not None and len(conversation.turns) == 1
    assert switched_out(main, a.id) == 1
    assert main.profiles.open(None).name == "tesis"


def test_while_waiting_no_new_turn_starts_and_its_prompt_is_kept_with_the_outgoing_profile(
    main: MainHarness,
):
    a, b = main.create_session(), main.create_session()
    other_profile(main)
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered(f"/open {b.id}"),
        InputReceived(a.id, "a1"),
        switch("tesis"),
        InputReceived(b.id, "b1"),
        ended(a),
        settle=True,
    )
    assert prompts(main) == ["a1"]
    assert main.profiles.open(None).name == "tesis"
    saved = MainStateStore(main.profile.state).load()
    assert [prompt.text for prompt in saved.queue] == ["b1"]


def test_with_now_a_running_turn_is_cancelled_and_its_prompt_goes_back_to_the_input(
    main: MainHarness,
):
    a = main.create_session()
    other_profile(main)
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        InputReceived(a.id, "a1"),
        DraftReceived(a.id, ("typing",)),  # typed while its Turn runs
        switch("tesis", now=True),
        TurnEnded(a.id, error=TurnCancelled(reason="profile switched")),  # as its executor ends it
        settle=True,
    )
    assert main.held[0].cancellation.reason == "profile switched"
    assert switched_out(main, a.id) == 1
    assert main.profiles.open(None).name == "tesis"
    saved = MainStateStore(main.profile.state).load()
    assert saved.drafts == {a.id: ["a1", "typing"]}
    assert saved.running == []
    main.hold_turns = False
    main.run(switch("default"), settle=True)
    assert restored(main.windows(a.id)[-1]) == ["a1", "typing"]


def test_with_now_a_shared_sessions_cancelled_prompt_goes_back_to_its_open_window(
    main: MainHarness,
):
    shared = main.create_session()
    tesis = other_profile(main)
    main.profiles.add_session(tesis, shared.id)
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {shared.id}"),
        InputReceived(shared.id, "s1"),
        switch("tesis", now=True),
        TurnEnded(shared.id, error=TurnCancelled(reason="profile switched")),
        settle=True,
    )
    [window] = main.windows(shared.id)
    assert switched_out(main, shared.id) == 0
    assert InputRestore(text="s1") in window.sent
    assert main.profiles.open(None).name == "tesis"


def test_a_turn_waiting_for_its_confirmation_counts_as_running(main: MainHarness):
    a = main.create_session()
    other_profile(main)
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        InputReceived(a.id, "a1"),
        ConfirmationRequested(a.id, "Delete?", "Delete the file?"),
        switch("tesis"),
        settle=True,
    )
    assert main.profiles.open(None).name == "default"
    assert switched_out(main, a.id) == 0


def test_now_given_while_waiting_cancels_what_runs(main: MainHarness):
    a = main.create_session()
    other_profile(main)
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        InputReceived(a.id, "a1"),
        switch("tesis"),
        switch("tesis", now=True),
        TurnEnded(a.id, error=TurnCancelled(reason="profile switched")),
        settle=True,
    )
    assert main.held[0].cancellation.reason == "profile switched"
    assert main.profiles.open(None).name == "tesis"


def test_asking_for_the_active_profile_while_waiting_drops_the_switch(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    other_profile(main)
    main.hold_turns = True
    chat = main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered(f"/open {b.id}"),
        InputReceived(a.id, "a1"),
        switch("tesis"),
        InputReceived(b.id, "b1"),  # waits: no Turn starts while the switch is asked
        switch("default"),
        ended(a),
        settle=True,
    )
    assert any("profile switch to tesis dropped" in line for line in chat)
    assert prompts(main) == ["a1", "b1"]
    assert switched_out(main, a.id) == 0
    assert main.profiles.open(None).name == "default"
    [dropped] = [entry for entry in main.main_log() if entry.event == "profile_switch_dropped"]
    assert dropped.fields["profile"] == "tesis"


def test_entering_a_profile_with_another_default_enlace_moves_no_session(main: MainHarness):
    shared, mine = main.create_session(), main.create_session()
    tesis = other_profile(main)
    main.profiles.add_session(tesis, shared.id)
    gpu = SETTINGS.model_copy(
        update={"session": SETTINGS.session.model_copy(update={"server": "gpu"})}
    )
    MainSettingsFiles(tesis.settings).save_current(gpu)
    main.run(
        MainCommandEntered(f"/open {shared.id}"),
        switch("tesis"),
        MainCommandEntered("/new"),
        settle=True,
    )
    assert main.sessions.load(shared.id).settings.server is None
    assert main.sessions.load(mine.id).settings.server is None
    [(new_id, _)] = [(sid, link) for sid, link in main.opened if sid not in (shared.id, mine.id)]
    assert main.sessions.load(new_id).settings.server == "gpu"
    assert main.profiles.sessions(tesis) == [shared.id, new_id]
    assert main.profiles.sessions(main.profile) == [shared.id]  # /open shared it with default


def test_the_incoming_profiles_settings_and_sysprompts_take_over(main: MainHarness):
    tesis = other_profile(main)
    (tesis.sysprompts / "default.txt").write_text("Tesis: $assistant_name.")
    approve_sysprompt(tesis.sysprompts, "default.txt")
    MainSettingsFiles(tesis.settings).save_current(
        MainSettings(session=SessionSettings(username="eva", assistant_name="Tesis"))
    )
    chat = main.run(switch("tesis"), MainCommandEntered("/set"), MainCommandEntered("/new"))
    assert any("session.username=eva" in line for line in chat)
    [(new_id, _)] = main.opened
    conversation = main.sessions.load_conversation(main.sessions.load(new_id))
    assert conversation is not None and conversation.sysprompt == "Tesis: Tesis."


def test_a_window_closed_by_the_switch_reporting_its_exit_later_changes_nothing(
    main: MainHarness,
):
    a = main.create_session()
    other_profile(main)
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        switch("tesis"),
        switch("default"),
        WindowClosed(a.id, returncode=0),  # the first xterm going, after a new one opened
        InputReceived(a.id, "a1"),
        settle=True,
    )
    assert prompts(main) == ["a1"]
    assert main.held[0].cancellation.reason != "window closed"


@pytest.mark.parametrize("command", ["/profile switch nadie", "/profile switch X!"])
def test_a_profile_that_does_not_exist_is_an_error_and_changes_nothing(
    main: MainHarness, command: str
):
    a = main.create_session()
    chat = main.run(MainCommandEntered(f"/open {a.id}"), MainCommandEntered(command), settle=True)
    assert any("profile_not_found" in line for line in chat)
    assert switched_out(main, a.id) == 0
    assert main.profiles.names() == ["default"]
    assert main.profiles.open(None).name == "default"


def test_switching_to_the_active_profile_changes_nothing(main: MainHarness):
    a = main.create_session()
    chat = main.run(MainCommandEntered(f"/open {a.id}"), switch("default"), settle=True)
    assert any("already" in line for line in chat)
    assert switched_out(main, a.id) == 0


def test_the_switch_is_told_in_mains_chat(main: MainHarness):
    other_profile(main)
    chat = main.run(switch("tesis"), settle=True)
    assert any("profile tesis active" in line for line in chat)
    assert any("tesis (active)" in line for line in main.run(MainCommandEntered("/profile")))
