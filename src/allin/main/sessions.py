"""Sessions on disk: one folder per Session under ``<data-dir>/sessions/<sid>/``.

The folder outlives the Session's Window; ``session.json`` holds its metadata and Settings,
``conversation.json`` its Conversation and ``log.jsonl`` its Log. ``aliases.json`` keeps the ids
renamed Sessions had, which still reach them (#59): other Conversations keep them.
"""

import os
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from allin.core.errors import (
    SessionIdInvalid,
    SessionIdTaken,
    SessionNotFound,
    SessionNotOwned,
    SessionStorageFailed,
    SessionUnreadable,
    WorkspaceRefused,
)
from allin.core.files import write_atomically
from allin.core.ids import (
    AttachedName,
    CommandName,
    MainId,
    NotificationSource,
    PieceId,
    SessionId,
    new_session_id,
    parse_session_id,
)
from allin.core.settings import SessionSettings
from allin.core.themes import pick_border_color
from allin.main.conversation import (
    Conversation,
    Item,
    archive_conversation,
    load_archived,
    load_conversation,
    save_conversation,
)
from allin.main.log import Log, LogEntry, read_log

_METADATA = "session.json"
_RECIPE = "recipe.json"  # its birth recipe, for Restablecer (ADR-0015)
_LOG = "log.jsonl"
_ALIASES = "aliases.json"


class _WorkspacePath(BaseModel, frozen=True):
    path: str  # absolute and resolved, as a human chose it


class _Aliases(BaseModel):
    aliases: dict[SessionId, SessionId] = {}  # an old id -> the Session's id now


class Change(BaseModel, frozen=True):
    """What a Notificación says a Session was given or lost: one about the same thing the other
    way, still pending, cancels out with it."""

    subject: str  # a piece (``function:clock``) or a Command session (``@send``)
    given: bool  # given, or taken away


class Notification(BaseModel, frozen=True):
    """A Notificación pending for its Session's next Prompt (``allin.main.notifications``)."""

    id: int  # unique in its Session while pending
    source: NotificationSource
    text: str
    change: Change | None = None


class MoldVersion(BaseModel, frozen=True):
    """Which Molde, and which version of it: the hash it was approved and read with."""

    piece: PieceId
    hash: str


class BirthRecipe(BaseModel, frozen=True):
    """What a Session was born from, as it was then: the Molde and the version it came from,
    or the Session template ``/new`` copied, with what that gave it (ADR-0015, #112)."""

    mold: MoldVersion | None = None
    settings: SessionSettings
    sysprompt: str  # rendered
    functions: tuple[PieceId, ...] = ()
    commands: tuple[CommandName, ...] = ()
    history: list[Item] = []


class HeldMethods(BaseModel, frozen=True):
    """A Session has the methods of the Proceso adherido ``name`` while it keeps its first
    ``kept_from`` Turns: a Rewind to fewer takes them away (ADR-0021)."""

    name: AttachedName
    kept_from: int


class Session(BaseModel, frozen=True):
    id: SessionId
    owner: MainId  # the only Main that may open it
    created_by: MainId
    created_at: datetime
    settings: SessionSettings = Field(default_factory=SessionSettings)
    # The palette colour its border takes under a Theme whose border is ``auto`` (#47): chosen
    # when it is born, the one the other Sessions use least, and kept, like its id. ``None`` for
    # a Session from before, which gets one the first time it is shown.
    border_color: str | None = None
    # Its Function tools, as pieces (``function:<package>``, ``agent:<package>``): what its
    # LLM may get in ``tools=[...]``, once each is approved (#106).
    functions: tuple[PieceId, ...] = ()
    # The Command sessions its LLM may use, by name (``send``): it limits its tools, not a
    # human (ADR-0025). One saved before them has none.
    commands: tuple[CommandName, ...] = ()
    # Its Notificaciones waiting for its next Prompt (#109).
    notifications: tuple[Notification, ...] = ()
    # The Procesos adheridos whose methods its LLM gets as tools (#114).
    attached_methods: tuple[HeldMethods, ...] = ()
    # The Session whose LLM created it, by the id it had then: that one may send it to the
    # background (ADR-0020). ``None`` if a human created it, or one saved before #122.
    creator: SessionId | None = None


class SessionStore:
    def __init__(self, root: Path, new_id: Callable[[], SessionId] = new_session_id) -> None:
        self._root = root
        self._new_id = new_id

    def workspace(self, session_id: SessionId) -> Path:
        """The Session's Carpeta de trabajo: the folder a human pointed it to, or by default
        ``<data dir>/workspaces/<id>/``, apart from its folder, which holds its state
        (ADR-0016). The default is made when something is first put in it."""
        try:
            chosen = _WorkspacePath.model_validate_json(self._pointer(session_id).read_bytes())
        except (OSError, ValidationError):
            return self._default_workspace(session_id)
        return Path(chosen.path)

    def point_workspace(self, session_id: SessionId, path: Path | None, installation: Path) -> Path:
        """Point the Session's Carpeta de trabajo to ``path``, a folder of the human's, or
        back to its default with ``None``. Only a human does it, and never into AllIn (its data
        folder or its ``installation``); the path is not a Setting, so it never
        travels in a template, Molde, Plano or Kit (ADR-0016). Raises ``WorkspaceRefused``."""
        pointer = self._pointer(session_id)
        try:
            if path is None:
                pointer.unlink(missing_ok=True)
                return self._default_workspace(session_id)
            chosen = path.expanduser().resolve()
            allin = (self._root.parent.resolve(), installation.resolve())
            if any(chosen.is_relative_to(folder) for folder in allin):
                raise WorkspaceRefused(path=str(chosen), reason="inside AllIn")
            if not chosen.is_dir():
                raise WorkspaceRefused(path=str(chosen), reason="not a folder")
            pointer.parent.mkdir(parents=True, exist_ok=True)
            write_atomically(pointer, _WorkspacePath(path=str(chosen)).model_dump_json())
        except OSError as error:
            raise SessionStorageFailed(root=str(pointer), reason=str(error)) from error
        return chosen

    def _default_workspace(self, session_id: SessionId) -> Path:
        return self._root.parent / "workspaces" / session_id

    def _pointer(self, session_id: SessionId) -> Path:
        """Where the path a human pointed the Carpeta de trabajo to is kept: beside the
        workspaces, out of every tool's Alcance and of a Profile's copy."""
        return self._root.parent / "workspaces" / f"{session_id}.path.json"

    def create(
        self,
        owner: MainId,
        settings: SessionSettings,
        sysprompt: str,
        chosen: str | None = None,
        functions: tuple[PieceId, ...] = (),
        commands: tuple[CommandName, ...] = (),
        history: Sequence[Item] = (),
        mold: MoldVersion | None = None,
        creator: SessionId | None = None,
    ) -> Session:
        """Create a Session and its folder, with the id a human ``chosen`` or a fresh one;
        ``sysprompt`` is already rendered, and ``history`` opens its Conversation. ``creator``
        is the Session whose LLM asked for it. Its birth recipe is written before it exists: no
        Session is ever without one."""
        wanted = None if chosen is None else self.check_new_id(chosen)
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            session_id, folder = self._new_folder() if wanted is None else self._folder(wanted)
            session = Session(
                id=session_id,
                owner=owner,
                created_by=owner,
                created_at=datetime.now(UTC),
                settings=settings,
                border_color=pick_border_color(self._border_colors(but=session_id)),
                functions=functions,
                commands=commands,
                creator=creator,
            )
            recipe = BirthRecipe(
                mold=mold,
                settings=settings,
                sysprompt=sysprompt,
                functions=functions,
                commands=commands,
                history=list(history),
            )
            write_atomically(folder / _RECIPE, recipe.model_dump_json(indent=2))
            save_conversation(folder, Conversation(sysprompt=sysprompt, history=list(history)))
            write_atomically(folder / _METADATA, session.model_dump_json(indent=2))
        except OSError as error:
            raise SessionStorageFailed(root=str(self._root), reason=str(error)) from error
        return session

    def resolve(self, text: str) -> SessionId:
        """The id of the Session ``text`` names, by its id or by an alias it had (#59): every
        lookup of a Session goes through here."""
        # Anything that is not an id is never turned into a path.
        session_id = parse_session_id(text)
        if session_id is None:
            raise SessionNotFound(session_id=text)
        if (self._root / session_id).is_dir():
            return session_id
        renamed = self._aliases().get(session_id)
        if renamed is None or not (self._root / renamed).is_dir():
            raise SessionNotFound(session_id=text)
        return renamed

    def check_new_id(self, text: str) -> SessionId:
        """``text`` as an id a Session may take: of the right shape, and no other Session's id
        or alias."""
        session_id = parse_session_id(text)
        if session_id is None:
            raise SessionIdInvalid(session_id=text, rule="4 to 12 characters of a-z and 0-9")
        if self._taken(session_id):
            raise SessionIdTaken(session_id=session_id)
        return session_id

    def rename(self, session: Session, text: str) -> Session:
        """The Session under the id ``text``: its folder and Carpeta de trabajo move, and its
        old id, and every alias it had, lead to the new one."""
        new_id = self.check_new_id(text)
        renamed = session.model_copy(update={"id": new_id})
        aliases = {
            alias: new_id if target == session.id else target
            for alias, target in self._aliases().items()
        }
        aliases[session.id] = new_id
        old_folder, new_folder = self._root / session.id, self._root / new_id
        old_workspace = self._default_workspace(session.id)
        new_workspace = self._default_workspace(new_id)
        old_pointer, new_pointer = self._pointer(session.id), self._pointer(new_id)
        undo: list[Callable[[], object]] = []  # what was done so far, to put back on a failure
        try:
            write_atomically(old_folder / _METADATA, renamed.model_dump_json(indent=2))
            undo.append(
                lambda: write_atomically(old_folder / _METADATA, session.model_dump_json(indent=2))
            )
            old_folder.rename(new_folder)
            undo.append(lambda: new_folder.rename(old_folder))
            if old_workspace.is_dir():
                old_workspace.rename(new_workspace)
                undo.append(lambda: new_workspace.rename(old_workspace))
            if old_pointer.is_file():  # a folder of the human's stays where it is
                old_pointer.rename(new_pointer)
                undo.append(lambda: new_pointer.rename(old_pointer))
            # Last: once the old id is an alias, the rename is done.
            write_atomically(
                self._root / _ALIASES, _Aliases(aliases=aliases).model_dump_json(indent=2)
            )
        except OSError as error:
            for step in reversed(undo):
                with suppress(OSError):  # the first failure is the one to tell
                    step()
            raise SessionStorageFailed(root=str(self._root), reason=str(error)) from error
        return renamed

    def load(self, text: str) -> Session:
        """The Session whose id, or an alias of it, is ``text``, typed by the user or read
        from disk."""
        session_id = self.resolve(text)
        path = self._root / session_id / _METADATA
        try:
            return Session.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as error:
            raise SessionUnreadable(session_id=session_id, reason=str(error)) from error

    def with_border_color(self, session: Session) -> Session:
        """The Session with its border colour, given now and saved if it had none (#47)."""
        if session.border_color is not None:
            return session
        colored = session.model_copy(
            update={"border_color": pick_border_color(self._border_colors(but=session.id))}
        )
        try:
            write_atomically(self._root / session.id / _METADATA, colored.model_dump_json(indent=2))
        except OSError as error:
            raise SessionStorageFailed(root=str(self._root), reason=str(error)) from error
        return colored

    def _border_colors(self, but: SessionId) -> list[str]:
        """The border colours the other stored Sessions have."""
        colors: list[str] = []
        for session_id in self.ids():
            if session_id == but:
                continue
            try:
                color = self.load(session_id).border_color
            except (SessionNotFound, SessionUnreadable):
                continue
            if color is not None:
                colors.append(color)
        return colors

    def save_settings(self, session: Session, settings: SessionSettings) -> Session:
        """The Session with new Settings, already on disk."""
        return self._save(session.model_copy(update={"settings": settings}))

    def save_functions(self, session: Session, functions: tuple[PieceId, ...]) -> Session:
        """The Session with other Function tools, already on disk."""
        return self._save(session.model_copy(update={"functions": functions}))

    def save_attached_methods(self, session: Session, held: tuple[HeldMethods, ...]) -> Session:
        """The Session with the methods of other Procesos adheridos, already on disk."""
        return self._save(session.model_copy(update={"attached_methods": held}))

    def save_notifications(
        self, session: Session, notifications: tuple[Notification, ...]
    ) -> Session:
        """The Session with other pending Notificaciones, already on disk."""
        return self._save(session.model_copy(update={"notifications": notifications}))

    def load_recipe(self, session: Session) -> BirthRecipe | None:
        """Its birth recipe; ``None`` for one born before Sessions kept it."""
        try:
            return BirthRecipe.model_validate_json((self._root / session.id / _RECIPE).read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, ValidationError) as error:
            raise SessionUnreadable(session_id=session.id, reason=str(error)) from error

    def save_commands(self, session: Session, commands: tuple[CommandName, ...]) -> Session:
        """The Session with another list of Command sessions, already on disk."""
        return self._save(session.model_copy(update={"commands": commands}))

    def _save(self, changed: Session) -> Session:
        try:
            write_atomically(self._root / changed.id / _METADATA, changed.model_dump_json(indent=2))
        except OSError as error:
            raise SessionStorageFailed(root=str(self._root), reason=str(error)) from error
        return changed

    def load_owned(self, text: str, main_id: MainId) -> Session:
        """Load a Session only ``main_id`` may use: the Main that owns it."""
        session = self.load(text)
        if session.owner != main_id:
            raise SessionNotOwned(session_id=session.id, owner=session.owner)
        return session

    def ids(self) -> list[SessionId]:
        """The ids of every stored Session, in no particular order."""
        if not self._root.is_dir():
            return []
        parsed = (parse_session_id(path.name) for path in self._root.iterdir())
        return [session_id for session_id in parsed if session_id is not None]

    def changed_at(self, session_id: SessionId) -> int:
        """When anything in the Session's folder last changed, in nanoseconds; 0 if it has no
        folder. Cheap: the Organizer asks it for each row (#53)."""
        try:
            with os.scandir(self._root / session_id) as entries:
                return max((entry.stat().st_mtime_ns for entry in entries), default=0)
        except OSError:
            return 0

    def load_conversation(self, session: Session) -> Conversation | None:
        """``None`` for a Session created before Sessions had a Conversation."""
        return load_conversation(self._root / session.id)

    def save_conversation(self, session: Session, conversation: Conversation) -> None:
        save_conversation(self._root / session.id, conversation)

    def archive_conversation(self, session: Session, conversation: Conversation) -> str:
        """Archive what a Compaction replaces; its path within the Session's folder."""
        return archive_conversation(self._root / session.id, conversation)

    def load_archived(self, session: Session) -> list[Conversation]:
        """What its Compactions archived, oldest first."""
        return load_archived(self._root / session.id)

    def log(self, session_id: SessionId) -> Log:
        if parse_session_id(session_id) is None:  # an id made by hand never becomes a path
            raise SessionNotFound(session_id=session_id)
        # Something late about a Session just renamed goes to its Log all the same.
        return Log(self._root / self._aliases().get(session_id, session_id) / _LOG)

    def read_log(self, session: Session) -> list[LogEntry]:
        return read_log(self._root / session.id / _LOG)

    def _aliases(self) -> dict[SessionId, SessionId]:
        path = self._root / _ALIASES
        try:
            return _Aliases.model_validate_json(path.read_bytes()).aliases
        except FileNotFoundError:
            return {}
        except (OSError, ValidationError) as error:
            raise SessionStorageFailed(root=str(path), reason=str(error)) from error

    def _taken(self, session_id: SessionId) -> bool:
        return (self._root / session_id).exists() or session_id in self._aliases()

    def _folder(self, session_id: SessionId) -> tuple[SessionId, Path]:
        """The folder of a Session whose id a human chose."""
        folder = self._root / session_id
        try:
            folder.mkdir()  # fails if taken meanwhile, so two Sessions never share one
        except FileExistsError as error:
            raise SessionIdTaken(session_id=session_id) from error
        return session_id, folder

    def _new_folder(self) -> tuple[SessionId, Path]:
        while True:
            session_id = self._new_id()
            if session_id in self._aliases():
                continue  # an old id still leads to its Session
            folder = self._root / session_id
            try:
                folder.mkdir()  # fails if taken, so two Sessions never share one
            except FileExistsError:
                continue
            return session_id, folder
