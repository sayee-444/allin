"""Sessions on disk: one folder per Session under ``<data-dir>/sessions/<sid>/``.

The folder outlives the Session's Window; ``session.json`` holds its metadata and Settings,
``conversation.json`` its Conversation and ``log.jsonl`` its Log.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from allin.core.errors import SessionNotFound, SessionStorageFailed, SessionUnreadable
from allin.core.files import write_atomically
from allin.core.ids import MainId, is_session_id, new_session_id
from allin.core.settings import SessionSettings
from allin.main.conversation import Conversation, load_conversation, save_conversation
from allin.main.log import Log, LogEntry, read_log

_METADATA = "session.json"
_LOG = "log.jsonl"


class Session(BaseModel, frozen=True):
    id: str
    owner: MainId  # the only Main that may open it
    created_by: MainId
    created_at: datetime
    settings: SessionSettings = Field(default_factory=SessionSettings)


class SessionStore:
    def __init__(self, root: Path, new_id: Callable[[], str] = new_session_id) -> None:
        self._root = root
        self._new_id = new_id

    def create(self, owner: MainId, settings: SessionSettings, sysprompt: str) -> Session:
        """Create a Session with a fresh id and its folder; ``sysprompt`` is already rendered."""
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            folder = self._new_folder()
            session = Session(
                id=folder.name,
                owner=owner,
                created_by=owner,
                created_at=datetime.now(UTC),
                settings=settings,
            )
            save_conversation(folder, Conversation(sysprompt=sysprompt))
            write_atomically(folder / _METADATA, session.model_dump_json(indent=2))
        except OSError as error:
            raise SessionStorageFailed(root=str(self._root), reason=str(error)) from error
        return session

    def load(self, session_id: str) -> Session:
        # Anything that is not an id is never turned into a path.
        if not is_session_id(session_id) or not (self._root / session_id).is_dir():
            raise SessionNotFound(session_id=session_id)
        path = self._root / session_id / _METADATA
        try:
            return Session.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as error:
            raise SessionUnreadable(session_id=session_id, reason=str(error)) from error

    def ids(self) -> list[str]:
        """The ids of every stored Session, in no particular order."""
        if not self._root.is_dir():
            return []
        return [path.name for path in self._root.iterdir() if is_session_id(path.name)]

    def load_conversation(self, session: Session) -> Conversation | None:
        """``None`` for a Session created before Sessions had a Conversation."""
        return load_conversation(self._root / session.id)

    def save_conversation(self, session: Session, conversation: Conversation) -> None:
        save_conversation(self._root / session.id, conversation)

    def log(self, session_id: str) -> Log:
        if not is_session_id(session_id):
            raise SessionNotFound(session_id=session_id)
        return Log(self._root / session_id / _LOG)

    def read_log(self, session: Session) -> list[LogEntry]:
        return read_log(self._root / session.id / _LOG)

    def _new_folder(self) -> Path:
        while True:
            folder = self._root / self._new_id()
            try:
                folder.mkdir()  # fails if taken, so two Sessions never share one
            except FileExistsError:
                continue
            return folder
