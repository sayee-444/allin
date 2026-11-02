"""File references copied to the Session's Carpeta de trabajo, and what the LLM gets of them."""

from pathlib import Path

import pytest

from allin.core.errors import FileReferenceInvalid
from allin.main.attachments import AttachmentReader, attach, copy_in
from allin.main.conversation import Attachment

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def places(tmp_path: Path) -> tuple[Path, Path]:
    cwd, workspace = tmp_path / "proyecto", tmp_path / "workspaces" / "abc123"
    cwd.mkdir()
    (cwd / "notas.txt").write_text("hola\n")
    (cwd / "foto.png").write_bytes(PNG)
    return cwd, workspace


def test_a_file_outside_is_copied_as_a_snapshot_and_named_in_relative(places: tuple[Path, Path]):
    cwd, workspace = places
    names, attachments = attach(["./notas.txt"], workspace, "p1", cwd)
    copy = workspace / ".allin" / "adjuntos" / "p1" / "notas.txt"
    assert copy.read_text() == "hola\n"
    assert names == {"./notas.txt": ".allin/adjuntos/p1/notas.txt"}
    assert attachments == [Attachment(path=".allin/adjuntos/p1/notas.txt", kind="text")]
    (cwd / "notas.txt").write_text("changed")
    assert copy.read_text() == "hola\n"


def test_a_file_already_in_the_workspace_is_not_copied(places: tuple[Path, Path]):
    cwd, workspace = places
    (workspace / "datos").mkdir(parents=True)
    (workspace / "datos" / "tabla.csv").write_text("a,b\n")
    names, attachments = attach([str(workspace / "datos" / "tabla.csv")], workspace, "p1", cwd)
    assert names == {str(workspace / "datos" / "tabla.csv"): "datos/tabla.csv"}
    assert attachments == [Attachment(path="datos/tabla.csv", kind="text")]
    assert not (workspace / ".allin").exists()


def test_home_paths_are_expanded(places: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch):
    cwd, workspace = places
    monkeypatch.setenv("HOME", str(cwd))
    _, attachments = attach(["~/foto.png"], workspace, "p1", cwd)
    assert attachments == [Attachment(path=".allin/adjuntos/p1/foto.png", kind="image")]


@pytest.mark.parametrize("path", ["./no-existe.txt", "./", "/no/such/file"])
def test_a_path_that_is_no_file_is_refused_and_nothing_is_copied(
    places: tuple[Path, Path], path: str
):
    cwd, workspace = places
    with pytest.raises(FileReferenceInvalid):
        attach(["./notas.txt", path], workspace, "p1", cwd)
    assert not workspace.exists()


def test_two_files_with_the_same_name_keep_both(places: tuple[Path, Path]):
    cwd, workspace = places
    (cwd / "otra").mkdir()
    (cwd / "otra" / "notas.txt").write_text("otra")
    names, _ = attach(["./notas.txt", "./otra/notas.txt"], workspace, "p1", cwd)
    assert sorted(names.values()) == [
        ".allin/adjuntos/p1/notas-2.txt",
        ".allin/adjuntos/p1/notas.txt",
    ]


def test_a_binary_file_is_attached_by_name(places: tuple[Path, Path]):
    cwd, workspace = places
    (cwd / "datos.bin").write_bytes(b"\x00\x01\x02")
    _, [attachment] = attach(["./datos.bin"], workspace, "p1", cwd)
    assert attachment.kind == "binary"


def test_what_the_llm_gets_depends_on_the_kind_and_whether_it_sees_images(
    places: tuple[Path, Path],
):
    cwd, workspace = places
    _, attachments = attach(["./notas.txt", "./foto.png"], workspace, "p1", cwd)
    text_part, image_part = AttachmentReader(workspace, vision=True).parts(attachments)
    assert text_part == {
        "type": "input_text",
        "text": "\n[file .allin/adjuntos/p1/notas.txt]\nhola\n",
    }
    assert image_part["type"] == "input_image"
    assert str(image_part["image_url"]).startswith("data:image/png;base64,")
    _, blind = AttachmentReader(workspace, vision=False).parts(attachments)
    assert blind == {
        "type": "input_text",
        "text": "\n[image .allin/adjuntos/p1/foto.png: the current model does not see images]",
    }


def test_a_file_gone_from_the_workspace_is_said_so(places: tuple[Path, Path]):
    cwd, workspace = places
    _, attachments = attach(["./notas.txt"], workspace, "p1", cwd)
    (workspace / attachments[0].path).unlink()
    [part] = AttachmentReader(workspace, vision=True).parts(attachments)
    assert "no longer in the workspace" in str(part["text"])


def test_a_file_named_twice_is_attached_once(places: tuple[Path, Path]):
    cwd, workspace = places
    names, attachments = attach(["./notas.txt", "./notas.txt"], workspace, "p1", cwd)
    assert names == {"./notas.txt": ".allin/adjuntos/p1/notas.txt"}
    assert len(attachments) == 1
    assert [p.name for p in (workspace / ".allin" / "adjuntos" / "p1").iterdir()] == ["notas.txt"]


def test_a_file_swapped_for_a_link_once_resolved_is_not_copied(places: tuple[Path, Path]):
    # A tool turns the note it sends into a link to a secret between check and copy.
    cwd, workspace = places
    note = (cwd / "notas.txt").resolve()
    secret = cwd / "secreto"
    secret.write_text("clave")
    note.unlink()
    note.symlink_to(secret)
    with pytest.raises(FileReferenceInvalid, match="changed while it was copied"):
        copy_in([("notas.txt", note)], workspace, "p1")
    assert not list(workspace.rglob("notas.txt"))
