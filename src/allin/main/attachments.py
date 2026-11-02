"""Referencias a archivo: files a human attaches to a Prompt with ``@<path>`` (#14).

Each one is copied, as it is now, to ``.allin/adjuntos/<prompt>/`` in the Session's Carpeta de
trabajo, unless it is in there already, and the Conversation names it relative to that folder:
its tools will read it there once they run in their enclosure, with the Carpeta de trabajo as
their current directory, and a Clone or an exported Profile drags no absolute path (ADR-0016).
"""

import base64
import mimetypes
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from allin.core.errors import FileReferenceInvalid
from allin.main.conversation import Attachment, Item

ATTACHMENTS = Path(".allin") / "adjuntos"
_IMAGES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


def attach(
    references: Sequence[str], workspace: Path, prompt_id: str, cwd: Path
) -> tuple[dict[str, str], list[Attachment]]:
    """Copy the files ``references`` name (as typed after the ``@``; ``./`` is relative to
    ``cwd``) for the Prompt ``prompt_id``: what the text calls each now, and the attachments.
    Every one is checked before any is copied, so a bad one leaves nothing behind."""
    sources = [_source(reference, cwd) for reference in references]
    root = workspace.resolve()
    names: dict[str, str] = {}
    attachments: list[Attachment] = []
    taken: set[Path] = set()
    for reference, source in zip(references, sources, strict=True):
        if source.is_relative_to(root):
            relative = source.relative_to(root)
        else:
            relative = _free(ATTACHMENTS / prompt_id / source.name, taken)
            target = workspace / relative
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            except OSError as error:
                raise FileReferenceInvalid(path=reference, reason=str(error)) from error
        taken.add(relative)
        names[reference] = relative.as_posix()
        attachments.append(Attachment(path=relative.as_posix(), kind=_kind(source)))
    return names, attachments


def _source(reference: str, cwd: Path) -> Path:
    path = Path(reference).expanduser()
    path = (cwd / path if not path.is_absolute() else path).resolve()
    if not path.exists():
        raise FileReferenceInvalid(path=reference, reason="no such file")
    if not path.is_file():
        raise FileReferenceInvalid(path=reference, reason="not a file")
    return path


def _free(relative: Path, taken: set[Path]) -> Path:
    """``relative``, or ``<stem>-2<suffix>``… if another attachment of the Prompt has it."""
    candidate, number = relative, 2
    while candidate in taken:
        candidate = relative.with_name(f"{relative.stem}-{number}{relative.suffix}")
        number += 1
    return candidate


def _kind(path: Path) -> Literal["image", "text", "binary"]:
    media, _ = mimetypes.guess_type(path.name)
    if media in _IMAGES:
        return "image"
    try:
        data = path.read_bytes()
    except OSError:
        return "binary"
    if b"\0" in data:
        return "binary"
    try:
        data.decode()
    except UnicodeDecodeError:
        return "binary"
    return "text"


class AttachmentReader:
    """What the LLM gets of each attachment, read from the Carpeta de trabajo at each Step:
    a text file's content, an image as an image if its Server sees them (``vision``) or else a
    line saying it does not (ADR-0022), and a binary file by its name."""

    def __init__(self, workspace: Path, vision: bool) -> None:
        self._workspace = workspace
        self._vision = vision

    def parts(self, attachments: Sequence[Attachment]) -> list[Item]:
        return [self._part(attachment) for attachment in attachments]

    def _part(self, attachment: Attachment) -> Item:
        path = attachment.path
        if attachment.kind == "binary":
            return _text(f"[file {path}: binary, its content is not attached]")
        if attachment.kind == "image" and not self._vision:
            return _text(f"[image {path}: the current model does not see images]")
        try:
            data = (self._workspace / path).read_bytes()
        except OSError:
            return _text(f"[file {path}: no longer in the workspace]")
        if attachment.kind == "image":
            media, _ = mimetypes.guess_type(path)
            encoded = base64.b64encode(data).decode()
            return {"type": "input_image", "image_url": f"data:{media};base64,{encoded}"}
        return _text(f"[file {path}]\n{data.decode(errors='replace')}")


def _text(text: str) -> Item:
    # The template joins a message's parts as they are: each attachment starts its own line.
    return {"type": "input_text", "text": f"\n{text}"}
