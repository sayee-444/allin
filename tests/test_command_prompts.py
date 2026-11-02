"""Command prompts end to end (#14): a human's @ directives, from the Window to the LLM."""

from dataclasses import replace
from pathlib import Path

import pytest

from allin.ipc.messages import ChatAppend
from allin.main.events import InputReceived, MainCommandEntered
from allin.main.slots import ServerProps, parse_props
from allin.main.state import MainState
from tests.llm_fakes import FakeLlm, full_reply
from tests.test_dispatcher import MainHarness, restored, server

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MainHarness:
    cwd = tmp_path / "proyecto"
    cwd.mkdir()
    (cwd / "notas.txt").write_text("tres puntos\n")
    (cwd / "foto.png").write_bytes(PNG)
    monkeypatch.chdir(cwd)
    return MainHarness(tmp_path)


def errors_in(main: MainHarness, session_id: str) -> list[str]:
    [window] = main.windows(session_id)
    return [m.text for m in window.sent if isinstance(m, ChatAppend) and m.role == "error"]


def test_an_override_applies_to_its_turn_only_and_leaves_the_text(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(full_reply("", "uno"), full_reply("", "dos"))
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "explica @effort=medium esto @top_k=5"),
        InputReceived(session.id, "y ahora"),
        settle=True,
    )
    first, second = main.llm.requests
    assert first["input"][-1] == {"role": "user", "content": "explica esto"}
    assert first["reasoning"] == {"effort": "medium"}
    assert first["extra_body"] == {"top_k": 5}
    assert "reasoning" not in second and "extra_body" not in second
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None
    assert [turn.prompt for turn in conversation.turns] == ["explica esto", "y ahora"]
    [logged, _] = [e for e in main.session_log(session) if e.event == "prompt"]
    assert logged.fields["overrides"] == {"top_k": 5, "effort": "medium"}


def test_an_override_of_max_output_tokens_caps_its_turn(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(full_reply("", "uno"))
    main.run(InputReceived(session.id, "breve @max_output_tokens=50"), settle=True)
    [request] = main.llm.requests
    assert request["max_output_tokens"] == 50


@pytest.mark.parametrize("text", ["hola @tools=all", "hola @temperature=hot", "@clone"])
def test_a_refused_directive_is_an_error_and_the_prompt_goes_back(main: MainHarness, text: str):
    session = main.create_session()
    main.llm = FakeLlm()
    main.run(
        MainCommandEntered(f"/open {session.id}"), InputReceived(session.id, text), settle=True
    )
    assert main.llm.requests == []
    assert len(errors_in(main, session.id)) == 1
    assert restored(main.windows(session.id)[0]) == [text]
    assert not any(e.event == "prompt" for e in main.session_log(session))


def test_an_address_goes_literal(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(full_reply("", "ok"))
    main.run(InputReceived(session.id, "escribe a correo@dominio.com"), settle=True)
    [request] = main.llm.requests
    assert request["input"][-1]["content"] == "escribe a correo@dominio.com"


def test_a_file_is_copied_to_the_workspace_and_named_in_relative(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(full_reply("", "ok"))
    main.run(InputReceived(session.id, "resume @./notas.txt"), settle=True)
    [request] = main.llm.requests
    text, attached = request["input"][-1]["content"]
    workspace = main.sessions.workspace(session.id)
    [copy] = (workspace / ".allin" / "adjuntos").glob("*/notas.txt")
    relative = copy.relative_to(workspace).as_posix()
    assert text == {"type": "input_text", "text": f"resume {relative}"}
    assert attached == {"type": "input_text", "text": f"\n[file {relative}]\ntres puntos\n"}
    conversation = main.sessions.load_conversation(session)
    assert conversation is not None
    assert conversation.turns[0].prompt == f"resume {relative}"
    assert [a.path for a in conversation.turns[0].attachments] == [relative]


def test_a_file_already_in_the_workspace_is_not_copied(main: MainHarness):
    session = main.create_session()
    workspace = main.sessions.workspace(session.id)
    workspace.mkdir(parents=True)
    (workspace / "datos.txt").write_text("dentro")
    main.llm = FakeLlm(full_reply("", "ok"))
    main.run(InputReceived(session.id, f"lee @{workspace / 'datos.txt'}"), settle=True)
    [request] = main.llm.requests
    assert request["input"][-1]["content"][0]["text"] == "lee datos.txt"
    assert not (workspace / ".allin").exists()


def test_a_missing_file_is_an_error_and_nothing_is_sent(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm()
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "lee @./no-existe.txt"),
        settle=True,
    )
    assert main.llm.requests == []
    assert any("file_reference_invalid" in error for error in errors_in(main, session.id))
    assert restored(main.windows(session.id)[0]) == ["lee @./no-existe.txt"]


@pytest.mark.parametrize(("vision", "kind"), [(False, "input_text"), (True, "input_image")])
def test_an_image_goes_as_an_image_only_to_a_server_that_sees_them(
    main: MainHarness, vision: bool, kind: str
):
    main.server = replace(server(), vision=vision)
    session = main.create_session()
    main.llm = FakeLlm(full_reply("", "ok"))
    main.run(InputReceived(session.id, "describe @./foto.png"), settle=True)
    [request] = main.llm.requests
    _, image = request["input"][-1]["content"]
    assert image["type"] == kind
    if not vision:
        assert "the current model does not see images" in image["text"]


def test_a_queued_prompt_keeps_its_directives_in_mains_state(main: MainHarness):
    session = main.create_session()
    main.hold_turns = True
    main.run(
        InputReceived(session.id, "primero"),
        InputReceived(session.id, "luego @./notas.txt @seed=3"),
    )
    saved = main.state.saved[-1]
    reloaded = MainState.model_validate_json(saved.model_dump_json())  # as state.json has it
    [queued] = reloaded.queue
    assert queued.overrides.seed == 3
    assert [a.kind for a in queued.attachments] == ["text"]


def test_props_say_whether_the_server_sees_images():
    props = {"total_slots": 1, "default_generation_settings": {"n_ctx": 8}}
    assert parse_props(props) == ServerProps(1, 8, "")
    seeing = parse_props({**props, "modalities": {"vision": True, "audio": False}})
    assert seeing is not None and seeing.vision
