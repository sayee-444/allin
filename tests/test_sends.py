"""``/send`` between Sessions (#110, ADR-0013, ADR-0010): a Prompt goes with its chain of
Origin, its Command prompts in the structured form ahead of its text, which Main never reads.
An attachment comes from the sender's Carpeta de trabajo, and one from outside waits for a
human's Approval; a Session only reaches the Sessions of the active Profile."""

from pathlib import Path

import pytest
from pydantic import JsonValue

from allin.core.ids import BoxId
from allin.core.origin import HUMAN, Link, Origin
from allin.ipc.messages import ChatAppend
from allin.main.events import BoxAnswered, InputReceived, MainCommandEntered, WindowClosed
from allin.main.sessions import Session
from allin.ui.screen import ShowBox
from tests.llm_fakes import FakeLlm, full_reply
from tests.test_dispatcher import FakeWindowLink, MainHarness

FIRST_BOX = BoxId("b1")


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.hold_turns = True
    return harness


def from_session(session: Session) -> Origin:
    return Origin(chain=(*HUMAN.chain, Link("session", session.id)))


def received(main: MainHarness, session: Session) -> list[dict[str, JsonValue]]:
    return [entry.fields for entry in main.session_log(session) if entry.event == "prompt"]


def results(main: MainHarness, sender: Session) -> list[JsonValue]:
    """How each Command main its LLM sent went, as its Log tells it."""
    return [
        entry.fields.get("result") for entry in main.session_log(sender) if entry.event == "command"
    ]


def attachments(sent: dict[str, JsonValue]) -> list[str]:
    """The paths a received Prompt's attachments have in its Carpeta de trabajo."""
    paths = sent["attachments"]
    assert isinstance(paths, list)
    return [str(path) for path in paths]


def boxes(main: MainHarness) -> list[ShowBox]:
    return [update for update in main.main_screen if isinstance(update, ShowBox)]


def workspace(main: MainHarness, session: Session) -> Path:
    folder = main.sessions.workspace(session.id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_it_arrives_with_its_chain_of_origin_and_its_parameters(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    text = f"/send {b.id} --temperature=0.2 -- --attach=x /reset @./y @send hola"
    main.run(MainCommandEntered(text, origin=from_session(a)), settle=True)
    [sent] = received(main, b)
    assert sent["origin"] == ["human", f"session:{a.id}"]
    assert sent["text"] == f"[from Session {a.id}]\n--attach=x /reset @./y @send hola"
    assert sent["overrides"] == {"temperature": 0.2}
    assert "attachments" not in sent, "Main never reads its text"
    assert results(main, a) == ["ok"]


def test_one_from_its_carpeta_de_trabajo_is_copied_to_the_receivers(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    (workspace(main, a) / "notas.txt").write_text("hola")
    text = f"/send {b.id} --attach=notas.txt mira"
    main.run(MainCommandEntered(text, origin=from_session(a)), settle=True)
    [sent] = received(main, b)
    [path] = attachments(sent)
    assert path.startswith(".allin/adjuntos/")
    assert path.endswith("/notas.txt")
    assert (main.sessions.workspace(b.id) / path).read_text() == "hola"
    assert sent["text"] == f"[from Session {a.id}]\nmira"
    assert boxes(main) == [], "nothing to approve"


def test_a_path_with_spaces_goes_as_a_json_string(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    (workspace(main, a) / "mis notas.txt").write_text("hola")
    text = f'/send {b.id} --attach="mis notas.txt" mira'
    main.run(MainCommandEntered(text, origin=from_session(a)), settle=True)
    [sent] = received(main, b)
    [path] = attachments(sent)
    assert path.endswith("/mis notas.txt")


def test_one_from_outside_waits_for_approval_and_without_it_is_not_copied(
    main: MainHarness, tmp_path: Path
):
    a, b = main.create_session(), main.create_session()
    secret = tmp_path / "secreto.txt"
    secret.write_text("clave")
    text = f"/send {b.id} --attach={secret} mira"
    main.run(MainCommandEntered(text, origin=from_session(a)), BoxAnswered(FIRST_BOX, False))
    [box] = boxes(main)
    assert box.box.kind == "confirm" and str(secret) in box.box.text
    assert received(main, b) == []
    assert not (main.sessions.workspace(b.id) / ".allin").exists()
    [result] = results(main, a)
    assert str(result).startswith("error attachment_not_approved")


def test_one_from_outside_approved_is_copied(main: MainHarness, tmp_path: Path):
    a, b = main.create_session(), main.create_session()
    shared = tmp_path / "datos.csv"
    shared.write_text("1,2")
    text = f"/send {b.id} --attach={shared} mira"
    main.run(MainCommandEntered(text, origin=from_session(a)), BoxAnswered(FIRST_BOX, True))
    [sent] = received(main, b)
    [path] = attachments(sent)
    assert (main.sessions.workspace(b.id) / path).read_text() == "1,2"
    assert results(main, a) == ["ok"]


def test_a_link_out_of_the_carpeta_de_trabajo_counts_as_outside(main: MainHarness, tmp_path: Path):
    a, b = main.create_session(), main.create_session()
    secret = tmp_path / "id_rsa"
    secret.write_text("clave")
    (workspace(main, a) / "llave").symlink_to(secret)
    main.run(MainCommandEntered(f"/send {b.id} --attach=llave x", origin=from_session(a)))
    [box] = boxes(main)
    assert str(secret) in box.box.text, "the human sees where it leads"
    assert received(main, b) == []


def test_devastation_does_not_approve_it(main: MainHarness, tmp_path: Path):
    a, b = main.create_session(), main.create_session()
    main.devastation = True
    (tmp_path / "f.txt").write_text("x")
    main.run(
        MainCommandEntered(f"/send {b.id} --attach={tmp_path / 'f.txt'} x", origin=from_session(a))
    )
    assert [box.box.title for box in boxes(main) if box.box.kind == "confirm"] == ["Approve files"]
    assert received(main, b) == []


def test_the_file_copied_is_the_one_the_human_approved(main: MainHarness, tmp_path: Path):
    # The link leads elsewhere by the time the human says yes: what was shown is copied.
    a, b = main.create_session(), main.create_session()
    (tmp_path / "publico.txt").write_text("hola")
    (tmp_path / "secreto.txt").write_text("clave")
    link = tmp_path / "enlace"
    link.symlink_to(tmp_path / "publico.txt")

    def retarget() -> None:
        link.unlink()
        link.symlink_to(tmp_path / "secreto.txt")

    main.run(
        MainCommandEntered(f"/send {b.id} --attach={link} x", origin=from_session(a)),
        retarget,
        BoxAnswered(FIRST_BOX, True),
        settle=True,
    )
    [box] = boxes(main)
    assert str(tmp_path / "publico.txt") in box.box.text
    [sent] = received(main, b)
    [path] = attachments(sent)
    assert (main.sessions.workspace(b.id) / path).read_text() == "hola"


def test_a_session_of_another_profile_is_out_of_reach(main: MainHarness):
    a = main.create_session()
    main.sessions_join_profile = False
    other = main.create_session()
    main.run(MainCommandEntered(f"/send {other.id} hola", origin=from_session(a)), settle=True)
    assert received(main, other) == []
    [result] = results(main, a)
    assert str(result).startswith("error outside_profile")


def test_a_session_out_of_the_active_profile_cannot_send(main: MainHarness):
    main.sessions_join_profile = False
    outsider = main.create_session()
    main.sessions_join_profile = True
    b = main.create_session()
    main.run(MainCommandEntered(f"/send {b.id} hola", origin=from_session(outsider)), settle=True)
    assert received(main, b) == []
    [result] = results(main, outsider)
    assert str(result).startswith("error outside_profile")


def test_an_approval_after_a_profile_switch_sends_nothing(main: MainHarness, tmp_path: Path):
    a, b = main.create_session(), main.create_session()
    main.profiles.create("tesis", main.profile)
    (tmp_path / "f.txt").write_text("x")
    main.run(
        MainCommandEntered(f"/send {b.id} --attach={tmp_path / 'f.txt'} x", origin=from_session(a)),
        MainCommandEntered("/profile switch tesis"),
        BoxAnswered(FIRST_BOX, True),
        settle=True,
    )
    assert received(main, b) == []
    [result] = results(main, a)
    assert str(result).startswith("error outside_profile")


def user_lines(window: FakeWindowLink) -> list[tuple[str, str]]:
    """Each Prompt its Window's Chat shows, as its label and text."""
    return [
        (message.label, message.text)
        for message in window.sent
        if isinstance(message, ChatAppend) and message.role == "user"
    ]


def test_its_window_labels_each_prompt_by_who_sent_it_and_so_does_its_chat_reopened(
    main: MainHarness,
):
    main.hold_turns = False
    main.llm = FakeLlm(*(full_reply("", "ok") for _ in range(3)))
    a, b = main.create_session(), main.create_session()
    bot = Origin(chain=(*HUMAN.chain, Link("process", "bot")))
    main.run(
        MainCommandEntered(f"/open {b.id}"),
        MainCommandEntered(f"/send {b.id} hola", origin=from_session(a)),
        MainCommandEntered(f"/send {b.id} soy juan", origin=bot),
        InputReceived(b.id, "y yo"),
        WindowClosed(b.id, returncode=0),
        MainCommandEntered(f"/open {b.id}"),
        settle=True,
    )
    before, after = main.windows(b.id)
    assert user_lines(before) == [
        (f"session:{a.id}", f"[from Session {a.id}]\nhola"),
        ("process:bot", "[from attached process bot]\nsoy juan"),
        ("ana", "y yo"),
    ]
    assert user_lines(after) == user_lines(before)


def test_a_human_text_goes_as_typed_even_if_it_starts_with_dashes(main: MainHarness):
    b = main.create_session()
    main.run(MainCommandEntered(f"/send {b.id} --attach=x --top_k=5 mira"), settle=True)
    [sent] = received(main, b)
    assert sent["text"] == "--attach=x --top_k=5 mira" and "overrides" not in sent


def test_an_unknown_parameter_or_no_text_is_refused(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    for text in (f"/send {b.id} --tempo=1 hola", f"/send {b.id} --top_k=5"):
        main.run(MainCommandEntered(text, origin=from_session(a)), settle=True)
    assert received(main, b) == []
    first, second = results(main, a)
    assert str(first).startswith("error param_override_invalid")
    assert str(second).startswith("error invalid_arguments")
