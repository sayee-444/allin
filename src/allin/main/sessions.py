"""Sessions on disk: one folder per Session under ``<data-dir>/sessions/<sid>/``.

The folder outlives the Session's Window; ``session.json`` holds its metadata.
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from allin.core.errors import SessionNotFound, SessionStorageFailed, SessionUnreadable
from allin.core.ids import MainId, is_session_id, new_session_id

_METADATA = "session.json"


class Session(BaseModel, frozen=True):
    id: str
    owner: MainId  # the only Main that may open it
    created_by: MainId
    created_at: datetime


class SessionStore:
    def __init__(self, root: Path, new_id: Callable[[], str] = new_session_id) -> None:
        self._root = root
        self._new_id = new_id

    def create(self, owner: MainId) -> Session:
        """Create a Session with a fresh id and its folder."""
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            folder = self._new_folder()
            session = Session(
                id=folder.name, owner=owner, created_by=owner, created_at=datetime.now(UTC)
            )
            # Written aside and renamed, so a crash never leaves half a file behind.
            partial = folder / f"{_METADATA}.partial"
            partial.write_text(session.model_dump_json(indent=2))
            os.replace(partial, folder / _METADATA)
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

    def _new_folder(self) -> Path:
        while True:
            folder = self._root / self._new_id()
            try:
                folder.mkdir()  # fails if taken, so two Sessions never share one
            except FileExistsError:
                continue
            return folder
